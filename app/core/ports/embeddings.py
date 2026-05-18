"""Port para clientes de embedding.

Separado do `LLMClient` porque embedding tem contrato distinto: in =
list[str], out = list[list[float]], sem temperatura/max_tokens. Manter
em ports/ permite swap do provider (Azure → OpenAI direto → local
sentence-transformers) sem tocar no core.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class EmbeddingClient(ABC):
    """Contrato mínimo de um cliente de embeddings."""

    model_name: str
    dimensions: int

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Recebe lista de textos e devolve um vetor por texto.

        Implementações devem fazer batch interno se o provider impõe limite
        de itens por request (Azure OpenAI: até 2048).
        """
        ...

    async def embed_one(self, text: str) -> list[float]:
        """Conveniência — embed de um único texto."""
        out = await self.embed([text])
        return out[0] if out else []
