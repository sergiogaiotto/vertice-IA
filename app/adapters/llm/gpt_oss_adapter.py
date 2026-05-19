"""Adaptador para gpt-oss-120b (OpenAI open-weights) via endpoint OpenAI-compatible.

gpt-oss-120b é um modelo de raciocínio (reasoning) com formato Harmony. Quando
servido por Groq, Together AI, Fireworks, vLLM ou Ollama em modo OpenAI-compat,
o servidor já encapsula o Harmony e expõe `chat/completions` padrão — o cliente
recebe apenas o canal `final` em `choices[0].message.content`.

Particularidades que este adaptador trata:

1. **Reasoning tokens consomem o orçamento de saída**. Diferente de modelos
   tradicionais, gpt-oss "pensa" antes de responder e esses tokens contam
   contra `max_tokens`. Para JSON estruturado, bumpamos a headroom (+1500
   tokens) quando `force_json=True` para garantir que o JSON tenha onde caber.

2. **`reasoning_effort` controla o trade-off**. Para JSON com schema rígido,
   `"low"` é ótimo (menos divagação, mais tokens para output). Valores:
   `"minimal" | "low" | "medium" | "high"` — exposto via env var.

3. **JSON-mode**: `response_format={"type":"json_object"}` é suportado por
   Groq, Together e vLLM ≥0.6.2. Ollama mais antigo pode ignorar — caímos
   no system prompt + parser tolerante a jusante.

4. **Harmony leakage defensivo**: alguns hosts/versões eventualmente vazam
   tokens de canal (`<|channel|>final<|message|>...`, `<think>...</think>`)
   no `content`. `_strip_harmony_artifacts` limpa isso antes de devolver.

5. **`reasoning_content` separado**: Groq e outros devolvem o "pensamento" em
   um campo à parte. Ignoramos — só nos interessa o `content` final.
"""

from __future__ import annotations

import re

import httpx

from app.config import get_settings
from app.core.ports.llm import LLMClient, LLMResponse


# Tokens consumidos APENAS pela cadeia de raciocínio quando `force_json=True`.
# Reservados ANTES do JSON propriamente dito. Calibrado em `reasoning_effort=low`;
# subir para "medium" pode exigir 2500–3500.
_REASONING_HEADROOM_TOKENS = 1500

# Bloco completo de canal `analysis` (raciocínio): tudo entre `<|channel|>analysis…<|message|>`
# e o próximo `<|end|>`. Removido por ser informativo, não-estrutural.
_HARMONY_ANALYSIS = re.compile(
    r"<\|channel\|>analysis<\|message\|>.*?<\|end\|>",
    flags=re.DOTALL,
)
# Mesmo padrão para canais de raciocínio com nomes alternativos
# (`commentary` aparece em algumas versões da Harmony spec).
_HARMONY_COMMENTARY = re.compile(
    r"<\|channel\|>commentary<\|message\|>.*?<\|end\|>",
    flags=re.DOTALL,
)
# Cabeçalho do canal `final` — queremos preservar o CONTEÚDO, descartar o header.
_HARMONY_FINAL_HEADER = re.compile(
    r"<\|start\|>assistant<\|channel\|>final<\|message\|>"
    r"|<\|channel\|>final<\|message\|>",
)
# Tokens soltos remanescentes (terminadores, start de outras roles).
_HARMONY_TOKEN = re.compile(r"<\|(?:start|end|return|channel|message|constrain)\|>[a-z]*")
# Tags <think>…</think> que alguns hosts emitem (Ollama tipicamente).
_THINK_BLOCK = re.compile(r"<think>.*?</think>", flags=re.DOTALL)


def _approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _strip_harmony_artifacts(text: str) -> str:
    """Remove tokens de canal Harmony que vazaram no `content`.

    Estratégia:
      1. Remove blocos completos dos canais `analysis`/`commentary` (raciocínio).
      2. Remove o cabeçalho do canal `final`, preservando o conteúdo após ele.
      3. Limpa <think>…</think> de hosts tipo Ollama.
      4. Remove tokens soltos remanescentes (terminadores).

    Idempotente: sem artefatos no input, devolve o texto inalterado.
    """
    if not text:
        return text
    if "<|" not in text and "<think>" not in text:
        return text
    out = _HARMONY_ANALYSIS.sub("", text)
    out = _HARMONY_COMMENTARY.sub("", out)
    out = _HARMONY_FINAL_HEADER.sub("", out)
    out = _THINK_BLOCK.sub("", out)
    out = _HARMONY_TOKEN.sub("", out)
    return out.strip()


class GptOssClient(LLMClient):
    """Wrapper async para servir gpt-oss-120b (e variantes) via endpoint
    OpenAI-compatible — Groq, Together AI, Fireworks, vLLM, Ollama, etc.

    Defaults de custo refletem a tabela do Groq para gpt-oss-120b
    (jan/2026: $0.15/M input, $0.75/M output). Ajuste por provedor via
    settings se o provider for outro.
    """

    name = "gpt-oss-120b"
    # Groq gpt-oss-120b pricing — caso mude de provedor, ajuste em config.
    cost_per_1k_input = 0.00015
    cost_per_1k_output = 0.00075
    # Hosts OSS tipicamente não diferenciam cache pública; tratamos como input cheio.
    cost_per_1k_cached_input = 0.00015

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        reasoning_effort: str | None = None,
    ):
        s = get_settings()
        self.api_key = api_key or s.gpt_oss_api_key
        self.name = model or s.gpt_oss_model
        self.base_url = (base_url or s.gpt_oss_base_url).rstrip("/")
        # Default `"low"` para tarefas com schema (JSON, classificação).
        # Operadores podem subir via env quando rodarem raciocínio profundo.
        self.reasoning_effort = (reasoning_effort or s.gpt_oss_reasoning_effort or "low").lower()

    async def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 800,
        temperature: float = 0.2,
        force_json: bool = False,
    ) -> LLMResponse:
        if not self.api_key or not self.base_url:
            raise RuntimeError(
                "gpt-oss não configurado — defina GPT_OSS_API_KEY e GPT_OSS_BASE_URL."
            )

        # Reasoning models gastam parte do orçamento de saída no canal de
        # raciocínio. Quando o caller pede JSON estruturado, garantimos
        # headroom adicional para o JSON propriamente dito — senão o modelo
        # corta no meio do raciocínio sem nunca emitir o `final`.
        effective_max = max_tokens
        if force_json and self.reasoning_effort not in ("minimal", "none"):
            effective_max = max_tokens + _REASONING_HEADROOM_TOKENS

        payload: dict = {
            "model": self.name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": effective_max,
            "temperature": temperature,
            "reasoning_effort": self.reasoning_effort,
        }
        if force_json:
            payload["response_format"] = {"type": "json_object"}

        async with httpx.AsyncClient(timeout=90) as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            if resp.status_code == 400 and "reasoning_effort" in resp.text:
                # Provedor não conhece o parâmetro (vLLM/Ollama antigos):
                # retry sem ele. Custa um round-trip mas evita falha dura.
                payload.pop("reasoning_effort", None)
                resp = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
            if resp.status_code == 400 and "response_format" in resp.text:
                # Host não suporta JSON-mode estruturado — desliga e segue.
                # O system_prompt do caller já força JSON via instrução,
                # e o _robust_json_parse a jusante absorve imperfeições.
                payload.pop("response_format", None)
                resp = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
            resp.raise_for_status()
            data = resp.json()

        # `content` é o canal `final` da Harmony após o servidor desencapsular.
        # `reasoning_content` (Groq) / `reasoning` (outros) é o canal `analysis`
        # — ignorado de propósito (informativo, não-estrutural).
        message = data["choices"][0]["message"]
        raw_text = message.get("content") or ""
        text = _strip_harmony_artifacts(raw_text)

        usage = data.get("usage") or {}
        ti = usage.get("prompt_tokens") or _approx_tokens(system_prompt + user_prompt)
        # `completion_tokens` em hosts compatíveis já SOMA reasoning + final.
        # `reasoning_tokens` aparece em alguns como subcampo informativo — não
        # subtraímos: cobramos o que o provider cobrou.
        to = usage.get("completion_tokens") or _approx_tokens(raw_text)
        cost = (ti / 1000) * self.cost_per_1k_input + (to / 1000) * self.cost_per_1k_output
        return LLMResponse(
            text=text,
            model=self.name,
            tokens_input=ti,
            tokens_output=to,
            cost_estimated=round(cost, 6),
            raw=data,
        )
