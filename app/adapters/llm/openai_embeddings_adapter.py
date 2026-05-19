"""Adaptador OpenAI Embeddings (api.openai.com direto).

Variante do `AzureOpenAIEmbeddingClient` para quando o usuário tem chave
OpenAI mas não Azure. O cliente `openai>=2` despacha automaticamente para
api.openai.com quando instanciado via `AsyncOpenAI(api_key=...)` sem
`azure_endpoint`.

Default: text-embedding-3-small (1536 dims) — mesma dimensão do adapter
Azure, então KBs criadas com Azure podem ser servidas por OpenAI direto
e vice-versa SE o `embedding_model` for o mesmo (`embedding_dims` em
`knowledge_bases` precisa bater com o que o adapter devolve).

Modo mock: sem `OPENAI_API_KEY`, devolve embeddings determinísticos via
hash — mesmo comportamento do adapter Azure para dev offline.
"""

from __future__ import annotations

from app.adapters.llm.azure_embeddings_adapter import _mock_embedding
from app.config import get_settings
from app.core.ports.embeddings import EmbeddingClient


class OpenAIEmbeddingClient(EmbeddingClient):
    model_name = "text-embedding-3-small"
    dimensions = 1536
    # Limite de itens por request — OpenAI aceita até 2048; 64 mantém
    # paridade com o adapter Azure (menos pressão de memória + maior
    # granularidade de erro/retry).
    _MAX_BATCH = 64

    def __init__(self, model: str | None = None):
        s = get_settings()
        self.api_key = s.openai_api_key
        self.model = model or s.openai_embedding_model or self.model_name

    @property
    def is_mock(self) -> bool:
        return not self.api_key

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if self.is_mock:
            return [_mock_embedding(t, self.dimensions) for t in texts]
        try:
            from openai import AsyncOpenAI
        except ImportError as e:
            raise RuntimeError("openai>=2 não instalado.") from e

        client = AsyncOpenAI(api_key=self.api_key)
        out: list[list[float]] = []
        for i in range(0, len(texts), self._MAX_BATCH):
            batch = texts[i : i + self._MAX_BATCH]
            batch = [t if t.strip() else " " for t in batch]
            resp = await client.embeddings.create(
                model=self.model,
                input=batch,
            )
            for d in resp.data:
                out.append(list(d.embedding))
        return out
