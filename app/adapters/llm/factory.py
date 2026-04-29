"""Fábrica que monta o conjunto de clientes LLM disponíveis.

Se uma API key não estiver configurada, cai no MockLLMClient com o mesmo
nome do modelo — o roteador continua funcional para desenvolvimento offline.
"""

from __future__ import annotations

from app.adapters.llm.gaia_adapter import GaiaClient
from app.adapters.llm.maritaca_adapter import MaritacaClient
from app.adapters.llm.mock_adapter import MockLLMClient
from app.adapters.llm.openai_adapter import OpenAIClient
from app.config import get_settings
from app.core.ports.llm import LLMClient

settings = get_settings()


def build_clients() -> dict[str, LLMClient]:
    clients: dict[str, LLMClient] = {}

    if settings.openai_api_key:
        clients[settings.openai_model] = OpenAIClient()
    else:
        clients[settings.openai_model] = MockLLMClient(name=settings.openai_model, cost_per_1k_input=0.005, cost_per_1k_output=0.015)

    if settings.maritaca_api_key:
        clients[settings.maritaca_model] = MaritacaClient()
    else:
        clients[settings.maritaca_model] = MockLLMClient(name=settings.maritaca_model, cost_per_1k_input=0.0008, cost_per_1k_output=0.0024)

    if settings.gaia_api_key and settings.gaia_base_url:
        clients[settings.gaia_model] = GaiaClient()
    else:
        clients[settings.gaia_model] = MockLLMClient(name=settings.gaia_model, cost_per_1k_input=0.0001, cost_per_1k_output=0.0003)

    return clients
