"""Use case: wizard 'IA, me ajuda' para sugerir um SKILL.md alinhado à plataforma.

Recebe descrição em linguagem natural do que o agente deve fazer e devolve um
SKILL.md completo seguindo o formato canônico Vértice:

    # {Título}

    ## Identidade
    ## Inputs aceitos
    ## Saída esperada
    ## Ferramentas autorizadas
    ## Política de roteamento
    ## Guardrails
        ### Entrada
        ### Saída
    ## Sinais de Failsafe

A IA pode propor o output_format (markdown / json / csv / html / xml) que será
usado pelo SkillService.detect_output_format na hora de executar o módulo.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


@dataclass
class SkillSuggestion:
    name: str               # slug (filename sem .md)
    title: str              # título humano (linha # ...)
    content: str            # SKILL.md completo
    output_format: str      # 'markdown' | 'json' | 'csv' | 'html' | 'xml'
    reasoning: str          # 1-2 frases do porquê desse formato
    source: str             # 'llm' | 'heuristic'
    tokens_input: int = 0
    tokens_output: int = 0
    cost_estimated: float = 0.0
    model_used: str = ""


_STOPWORDS = {
    "que", "para", "com", "uma", "isso", "esse", "essa", "como", "quero",
    "preciso", "fazer", "criar", "gerar", "skill", "agente", "ia", "novo",
    "nova", "dos", "das", "tudo", "muito", "pelo", "pela",
}


def _slugify(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text or "")
    ascii_text = nfkd.encode("ascii", "ignore").decode("ascii").lower()
    cleaned = re.sub(r"[^a-z0-9\s_]", " ", ascii_text)
    parts = [p for p in cleaned.split() if p and p not in _STOPWORDS]
    return "_".join(parts)[:50] or "nova_skill"


_SYSTEM_PROMPT = """Você é o arquiteto de skills da plataforma Vértice.

A partir de uma descrição livre em PT-BR do que um agente deve fazer, você gera
um arquivo SKILL.md completo seguindo o formato CANÔNICO da plataforma Vértice.

# Formato OBRIGATÓRIO (NÃO altere a ordem nem os títulos das seções)

```
# {Título da Skill — específico ao domínio}

## Identidade
{1 frase definindo escopo, papel e tom do agente}

## Inputs aceitos
- `nome_campo` (tipo): descrição
- ... (1-5 campos)

## Saída esperada
{Descrição precisa do formato. Se for JSON, mostre o schema entre ```json ... ``` ABAIXO desta seção, em uma linha separada — nunca no mesmo parágrafo. Se for markdown, descreva as seções esperadas. Se for CSV, liste as colunas. NUNCA misture formatos.}

## Ferramentas autorizadas
- `tool(params)`: condição de uso
- ou "Nenhuma — apenas inferência"

## Política de roteamento
- Default: `sabia-4`
- Fallback: `gpt-4o`

## Guardrails

### Entrada
- regra específica 1
- regra específica 2

### Saída
- regra específica 1
- regra específica 2

## Sinais de Failsafe
- condição que dispara revisão humana
```

# Regras de QUALIDADE

1. **Título** específico ao domínio (ex: "Classificador de Intenção de Cancelamento" — NÃO "Skill Genérica")
2. **Identidade** define escopo, NÃO repete o título
3. **Inputs** espelham a descrição do usuário; tipos comuns: `string`, `string[]`, `int`, `float`, `bool`, `dict`, `markdown`
4. **Saída esperada**: escolha UM formato (markdown / json / csv / html / xml) e seja preciso. Para JSON, declare o schema. Para markdown, liste as seções `##` que o agente deve produzir.
5. **Guardrails** específicos ao domínio (NÃO genéricos como "validar entrada"). Exemplos bons: "Recusar se input_text < 50 chars", "Bloquear PII no output", "Truncar a 4000 chars antes de processar".
6. **Roteamento**: padrão Sabia-4 + Fallback GPT-4o. Se o domínio exigir multilíngue extenso ou raciocínio complexo, use Default: gpt-4o.
7. **Failsafe**: condição operacional concreta (ex: "Confidence < 0.6", "Mais de 3 retries", "Output vazio em 2 chamadas seguidas").

# Formato JSON da resposta (NÃO o formato da skill — formato da SUA resposta)

Devolva APENAS este JSON, sem ``` ao redor, sem texto antes ou depois:

```json
{
  "name": "slug_em_snake_case_até_50_chars",
  "title": "Título humano completo",
  "content": "# Título...\\n\\n## Identidade\\n...\\n\\n... (SKILL.md completo)",
  "output_format": "markdown" | "json" | "csv" | "html" | "xml",
  "reasoning": "1-2 frases explicando por que escolheu este formato e roteamento"
}
```

REGRAS CRÍTICAS DE FORMATAÇÃO JSON:
- Aspas duplas internas no `content` DEVEM ser escapadas como \\"
- Quebras de linha do markdown DEVEM ser \\n (não quebra real)
- O `content` começa OBRIGATORIAMENTE com `# ` e tem todas as 7 seções na ordem exata
"""


class SkillWizardService:

    def __init__(self, llms: dict):
        self.llms = llms

    async def suggest(self, prompt: str) -> SkillSuggestion:
        """Gera sugestão de SKILL.md. Tenta LLM; cai em heurística se falhar."""
        prompt = (prompt or "").strip()
        if not prompt:
            raise ValueError("descrição vazia")

        # 1) tenta LLM (escolhe o primeiro cliente disponível)
        client = next(iter(self.llms.values())) if self.llms else None
        if client is not None:
            try:
                resp = await client.complete(
                    _SYSTEM_PROMPT,
                    f"# Descrição do agente\n\n{prompt}",
                    max_tokens=2200,
                    temperature=0.3,
                )
                data = self._robust_json_parse(resp.text)
                if data and self._validate_payload(data):
                    slug = _slugify(data.get("name") or data.get("title") or prompt)
                    return SkillSuggestion(
                        name=slug[:50],
                        title=str(data.get("title") or "Nova Skill")[:120],
                        content=self._normalize_content(data.get("content", "")),
                        output_format=str(data.get("output_format") or "markdown").lower(),
                        reasoning=str(data.get("reasoning") or ""),
                        source="llm",
                        tokens_input=resp.tokens_input,
                        tokens_output=resp.tokens_output,
                        cost_estimated=resp.cost_estimated,
                        model_used=resp.model,
                    )
            except Exception:
                pass

        # 2) fallback heurístico
        slug = _slugify(prompt)
        return SkillSuggestion(
            name=slug[:50],
            title=self._title_from_prompt(prompt),
            content=self._heuristic_skill(prompt),
            output_format="markdown",
            reasoning="Não foi possível obter resposta do LLM — gerado por heurística determinística baseada no template canônico.",
            source="heuristic",
        )

    @staticmethod
    def _robust_json_parse(text: str) -> dict | None:
        import json as _json
        if not text:
            return None
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            if lines and lines[-1].strip().startswith("```"):
                text = "\n".join(lines[1:-1]).strip()
        try:
            return _json.loads(text)
        except _json.JSONDecodeError:
            pass
        first = text.find("{")
        last = text.rfind("}")
        if first >= 0 and last > first:
            try:
                return _json.loads(text[first:last + 1])
            except _json.JSONDecodeError:
                pass
        return None

    @staticmethod
    def _validate_payload(data: dict) -> bool:
        """Sanidade mínima: tem 'content' começando com '#' e as 7 seções principais."""
        content = str(data.get("content") or "")
        if not content.strip().startswith("#"):
            return False
        required = [
            "## Identidade",
            "## Inputs aceitos",
            "## Saída esperada",
            "## Ferramentas autorizadas",
            "## Política de roteamento",
            "## Guardrails",
            "## Sinais de Failsafe",
        ]
        return all(s in content for s in required)

    @staticmethod
    def _normalize_content(content: str) -> str:
        """Garante que content é string com newlines reais (LLM às vezes manda \\n literal)."""
        if not isinstance(content, str):
            return ""
        # se o conteúdo veio com \n literal (texto), converte para newline real
        if "\\n" in content and "\n" not in content:
            content = content.replace("\\n", "\n")
        return content.strip() + "\n"

    @staticmethod
    def _title_from_prompt(prompt: str) -> str:
        """Extrai título humano da descrição (primeiras 60 chars, capitalizado)."""
        cleaned = re.sub(r"\s+", " ", prompt).strip()
        if len(cleaned) > 60:
            cleaned = cleaned[:60].rsplit(" ", 1)[0] + "…"
        return cleaned[:1].upper() + cleaned[1:] if cleaned else "Nova Skill"

    @staticmethod
    def _heuristic_skill(prompt: str) -> str:
        """Skill template-based quando LLM falha — preenche apenas Identidade."""
        title = SkillWizardService._title_from_prompt(prompt)
        return (
            f"# {title}\n\n"
            "## Identidade\n"
            f"Agente especializado em: {prompt[:200]}\n\n"
            "## Inputs aceitos\n"
            "- `input_text` (string): texto principal a ser processado\n\n"
            "## Saída esperada\n"
            "Resposta em **Markdown** estruturada com seções relevantes ao domínio. "
            "Use títulos `##` para organizar seções principais.\n\n"
            "## Ferramentas autorizadas\n"
            "- Nenhuma — apenas inferência\n\n"
            "## Política de roteamento\n"
            "- Default: `sabia-4`\n"
            "- Fallback: `gpt-4o`\n\n"
            "## Guardrails\n\n"
            "### Entrada\n"
            "- Validar que `input_text` não está vazio\n"
            "- Truncar entrada acima de 6000 caracteres\n\n"
            "### Saída\n"
            "- Não vazar PII (CPF, RG, cartão, email)\n"
            "- Manter resposta abaixo de 2000 caracteres\n\n"
            "## Sinais de Failsafe\n"
            "- Resposta vazia em 2 chamadas seguidas\n"
            "- Erro de validação repetido > 3 vezes\n"
        )
