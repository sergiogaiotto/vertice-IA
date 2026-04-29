"""Roteador de modelos LLM com fallback declarativo."""

from __future__ import annotations

from app.config import get_settings
from app.core.ports.llm import LLMClient, LLMResponse

settings = get_settings()


class ModelRouter:
    """Roteia chamadas para o LLM mais adequado dado o tipo de tarefa.

    Política padrão (parametrizável via settings ou OPA no futuro):
      - UMA_PALAVRA / SCORE / TERMOS  -> modelo barato (GAIA 4Bi)
      - SUMARIO / RESUMO / pt-BR      -> Sabiá-4 (Maritaca)
      - INTENCAO / multi-step / outros -> GPT-4.1 (OpenAI)

    Em caso de falha do modelo escolhido, faz fallback ordenado.
    """

    def __init__(self, clients: dict[str, LLMClient]):
        self.clients = clients
        self.fallback_order = [
            settings.router_default_model,
            settings.router_fallback_model,
            settings.router_cheap_model,
        ]

    def pick(self, hint: str = "", output_type: str = "") -> str:
        ot = (output_type or "").upper()
        if ot in {"UMA_PALAVRA", "SCORE", "TERMOS"}:
            return settings.router_cheap_model
        if ot in {"SUMARIO", "RESUMO"}:
            return settings.router_default_model
        if ot in {"INTENCAO"}:
            return settings.router_fallback_model
        return settings.router_default_model

    async def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        hint: str = "",
        output_type: str = "",
        max_tokens: int = 800,
        temperature: float = 0.2,
        force_json: bool = False,
    ) -> LLMResponse:
        chosen = self.pick(hint=hint, output_type=output_type)
        order = [chosen] + [m for m in self.fallback_order if m != chosen]
        last_error: Exception | None = None
        for model_name in order:
            client = self.clients.get(model_name)
            if not client:
                continue
            try:
                return await client.complete(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    force_json=force_json,
                )
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                continue
        raise RuntimeError(f"Todos os modelos falharam. Último erro: {last_error}")
