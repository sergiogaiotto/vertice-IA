"""Adaptador Azure OpenAI Embeddings (default: text-embedding-3-small, 1536d).

Compartilha credenciais com o `AzureOpenAIClient` (mesmo recurso/endpoint
do Azure), mas usa um deployment distinto para o modelo de embeddings.
O deployment é configurável via `AZURE_OPENAI_EMBEDDING_DEPLOYMENT`;
se ausente, default é `text-embedding-3-small`.

Modo mock: quando `AZURE_OPENAI_API_KEY` não está configurada, devolve
embeddings determinísticos derivados de hash do texto. Permite testar o
pipeline KB offline sem chamadas pagas. Os vetores mock NÃO são úteis para
retrieval real — servem só para validar que o caminho ponta-a-ponta funciona.
"""

from __future__ import annotations

import hashlib
import math

from app.config import get_settings
from app.core.ports.embeddings import EmbeddingClient


class AzureOpenAIEmbeddingClient(EmbeddingClient):
    model_name = "text-embedding-3-small"
    dimensions = 1536
    # Limite seguro de itens por request — Azure OpenAI aceita até 2048,
    # mas em alguns SKUs reduzem para 16 quando o texto é longo. 64 é
    # um meio-termo conservador.
    _MAX_BATCH = 64

    def __init__(self, deployment: str | None = None):
        s = get_settings()
        self.api_key = s.azure_openai_api_key
        self.endpoint = s.azure_openai_endpoint
        self.api_version = s.azure_openai_api_version
        # Deployment do modelo de embeddings — pode coexistir com o gpt-4o
        # no mesmo recurso Azure.
        self.deployment = (
            deployment
            or getattr(s, "azure_openai_embedding_deployment", None)
            or self.model_name
        )

    @property
    def is_mock(self) -> bool:
        return not (self.api_key and self.endpoint)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if self.is_mock:
            return [_mock_embedding(t, self.dimensions) for t in texts]
        try:
            from openai import AsyncAzureOpenAI
        except ImportError as e:
            raise RuntimeError("openai>=2 não instalado.") from e

        client = AsyncAzureOpenAI(
            api_key=self.api_key,
            azure_endpoint=self.endpoint,
            api_version=self.api_version,
        )
        out: list[list[float]] = []
        # Batching defensivo — o Azure rejeita batches grandes silenciosamente
        # em algumas regiões.
        for i in range(0, len(texts), self._MAX_BATCH):
            batch = texts[i : i + self._MAX_BATCH]
            # Defesa contra strings vazias: o Azure devolve 400 nesses casos.
            batch = [t if t.strip() else " " for t in batch]
            resp = await client.embeddings.create(
                model=self.deployment,
                input=batch,
            )
            for d in resp.data:
                out.append(list(d.embedding))
        return out


def _mock_embedding(text: str, dims: int) -> list[float]:
    """Vetor determinístico derivado de SHA-256 do texto.

    Suficiente para testar o pipeline (mesma string → mesmo vetor; strings
    diferentes → vetores diferentes), mas SEM significado semântico real.
    Normalizado (norma L2 = 1) para que cosine similarity fique em escala
    razoável.
    """
    seed = hashlib.sha256(text.encode("utf-8")).digest()
    # Expande o seed (32 bytes) para `dims` floats em [-1, 1].
    vec: list[float] = []
    for i in range(dims):
        byte = seed[i % len(seed)]
        # Mistura com o índice para evitar pattern repetitivo.
        v = ((byte * 31 + i * 17) % 200 - 100) / 100.0
        vec.append(v)
    # Normaliza L2.
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]
