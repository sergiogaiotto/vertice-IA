"""Fábrica que monta o conjunto de clientes LLM disponíveis.

Se uma API key não estiver configurada, cai no MockLLMClient com o mesmo
nome do modelo — o roteador continua funcional para desenvolvimento offline.
"""

from __future__ import annotations

from app.adapters.llm.azure_embeddings_adapter import AzureOpenAIEmbeddingClient
from app.adapters.llm.azure_openai_adapter import AzureOpenAIClient
from app.adapters.llm.gaia_adapter import GaiaClient
from app.adapters.llm.maritaca_adapter import MaritacaClient
from app.adapters.llm.mock_adapter import MockLLMClient
from app.adapters.llm.openai_embeddings_adapter import OpenAIEmbeddingClient
from app.config import get_settings
from app.core.ports.embeddings import EmbeddingClient
from app.core.ports.llm import LLMClient


def build_clients() -> dict[str, LLMClient]:
    """Reload settings a cada chamada — evita capturar valores antigos quando
    testes/conftest fazem monkeypatch de env vars depois do import."""
    settings = get_settings()
    clients: dict[str, LLMClient] = {}

    if settings.azure_openai_api_key and settings.azure_openai_endpoint:
        clients[settings.azure_openai_deployment] = AzureOpenAIClient()
    else:
        clients[settings.azure_openai_deployment] = MockLLMClient(
            name=settings.azure_openai_deployment,
            cost_per_1k_input=0.0025,
            cost_per_1k_output=0.01,
        )

    if settings.maritaca_api_key:
        clients[settings.maritaca_model] = MaritacaClient()
    else:
        clients[settings.maritaca_model] = MockLLMClient(
            name=settings.maritaca_model,
            cost_per_1k_input=0.0008,
            cost_per_1k_output=0.0024,
        )

    if settings.gaia_api_key and settings.gaia_base_url:
        clients[settings.gaia_model] = GaiaClient()
    else:
        clients[settings.gaia_model] = MockLLMClient(
            name=settings.gaia_model,
            cost_per_1k_input=0.0001,
            cost_per_1k_output=0.0003,
        )

    return clients


def build_embedding_client() -> EmbeddingClient:
    """Escolhe o provider de embeddings disponível.

    Prioridade:
      1. OpenAI direto (OPENAI_API_KEY) — preferido por ser mais simples
         de configurar (sem deployment, sem endpoint).
      2. Azure OpenAI (AZURE_OPENAI_API_KEY + AZURE_OPENAI_ENDPOINT) —
         para quando a infra já está no Azure.
      3. Mock — fallback offline (vetores hash, sem semântica real).
         Retrieval funciona em pipeline mas resultados são ruído.

    Recriado a cada request (não-cacheado) porque pode haver rotação de
    credenciais em runtime via `.env` (uvicorn --reload re-instancia).
    """
    s = get_settings()
    if s.openai_api_key:
        return OpenAIEmbeddingClient()
    if s.azure_openai_api_key and s.azure_openai_endpoint:
        return AzureOpenAIEmbeddingClient()
    # Mock: ambos OpenAI e Azure são candidatos válidos quando is_mock=True,
    # mas o OpenAI é o caminho default sem config; mantemos consistência.
    return OpenAIEmbeddingClient()
