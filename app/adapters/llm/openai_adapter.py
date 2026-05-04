"""Adaptador OpenAI (GPT-4.1)."""

from __future__ import annotations

from app.config import get_settings
from app.core.ports.llm import LLMClient, LLMResponse

settings = get_settings()


def _approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)


class OpenAIClient(LLMClient):
    name = "gpt-4.1"
    cost_per_1k_input = 0.005
    cost_per_1k_output = 0.015
    # OpenAI prompt caching: tokens cacheados saem por 25% do preço de input
    # nos modelos GPT-4.x. Atualize aqui se a tarifa do provedor mudar.
    cost_per_1k_cached_input = 0.00125

    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.api_key = api_key or settings.openai_api_key
        self.name = model or settings.openai_model

    async def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 800,
        temperature: float = 0.2,
        force_json: bool = False,
    ) -> LLMResponse:
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY não configurada.")
        try:
            from openai import AsyncOpenAI
        except ImportError as e:
            raise RuntimeError("openai não instalado.") from e

        client = AsyncOpenAI(api_key=self.api_key)
        kwargs = dict(
            model=self.name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        if force_json:
            # OpenAI JSON mode garante JSON sintaticamente válido
            # Requisito OpenAI: o system_prompt DEVE mencionar "JSON"
            # (já é o caso quando is_structured=True no radar_service)
            kwargs["response_format"] = {"type": "json_object"}

        resp = await client.chat.completions.create(**kwargs)
        text = resp.choices[0].message.content or ""
        usage = resp.usage
        ti = usage.prompt_tokens if usage else _approx_tokens(system_prompt + user_prompt)
        to = usage.completion_tokens if usage else _approx_tokens(text)
        cost = (ti / 1000) * self.cost_per_1k_input + (to / 1000) * self.cost_per_1k_output
        return LLMResponse(
            text=text,
            model=self.name,
            tokens_input=ti,
            tokens_output=to,
            cost_estimated=round(cost, 6),
        )
