"""Adaptador Gemma GAIA 4Bi — endpoint genérico compatível OpenAI/HuggingFace."""

from __future__ import annotations

import httpx

from app.config import get_settings
from app.core.ports.llm import LLMClient, LLMResponse

settings = get_settings()


def _approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)


class GaiaClient(LLMClient):
    name = "gaia-4b"
    cost_per_1k_input = 0.0001
    cost_per_1k_output = 0.0003
    # Self-hosted: cache gratuito (RAM/disco) — efetivamente sem custo extra.
    cost_per_1k_cached_input = 0.0

    def __init__(self, api_key: str | None = None, model: str | None = None, base_url: str | None = None):
        self.api_key = api_key or settings.gaia_api_key
        self.name = model or settings.gaia_model
        self.base_url = (base_url or settings.gaia_base_url).rstrip("/")

    async def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 800,
        temperature: float = 0.2,
        force_json: bool = False,
    ) -> LLMResponse:
        if not self.api_key or not self.base_url:
            raise RuntimeError("GAIA_API_KEY ou GAIA_BASE_URL não configurados.")
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.name,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        text = data["choices"][0]["message"]["content"]
        usage = data.get("usage") or {}
        ti = usage.get("prompt_tokens") or _approx_tokens(system_prompt + user_prompt)
        to = usage.get("completion_tokens") or _approx_tokens(text)
        cost = (ti / 1000) * self.cost_per_1k_input + (to / 1000) * self.cost_per_1k_output
        return LLMResponse(
            text=text,
            model=self.name,
            tokens_input=ti,
            tokens_output=to,
            cost_estimated=round(cost, 6),
            raw=data,
        )
