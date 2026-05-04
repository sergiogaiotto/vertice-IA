"""Port para clientes LLM."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class LLMResponse:
    text: str
    model: str
    tokens_input: int
    tokens_output: int
    cost_estimated: float
    raw: dict | None = None


class LLMClient(ABC):
    name: str
    cost_per_1k_input: float = 0.0
    cost_per_1k_output: float = 0.0
    # Tarifa para tokens de input que são *cache hit* (prompt caching).
    # Provedores como OpenAI cobram ~75% menos por tokens cacheados.
    # Quando 0.0, assumimos que não há diferenciação (tudo cobra como input).
    cost_per_1k_cached_input: float = 0.0

    @abstractmethod
    async def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 800,
        temperature: float = 0.2,
        force_json: bool = False,
    ) -> LLMResponse: ...
