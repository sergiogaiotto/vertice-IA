"""Store de artefatos gerados por execuções de módulo (CSV/MD/JSON/etc).

Persiste no Postgres (tabela ``artifacts``) para que funcione em deploy
multi-worker e sobreviva a restart. TTL default 30 min, configurável via
``ARTIFACT_TTL_SECONDS`` (env).

API pública mantida idêntica à versão anterior in-memory (``put``, ``get``)
para não exigir mudanças nos callers em radar_router/radar_service.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.adapters.db.repositories.artifact_repo import PgArtifactRepository


@dataclass
class Artifact:
    id: str
    filename: str
    mime_type: str
    content: bytes
    created_at: datetime


# Hard cap defensivo. Mantém artefatos em torno do que faz sentido para
# downloads de execução de módulo (CSV/MD/JSON), nunca vídeos/binários grandes.
_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
_DEFAULT_TTL_SECONDS = 1800    # 30 min


class ArtifactStore:
    def __init__(
        self,
        repo: PgArtifactRepository | None = None,
        ttl_seconds: int = _DEFAULT_TTL_SECONDS,
    ):
        self._repo = repo or PgArtifactRepository()
        self._ttl = ttl_seconds

    async def put(
        self, content: bytes | str, filename: str, mime_type: str
    ) -> Artifact:
        if isinstance(content, str):
            content = content.encode("utf-8")
        if len(content) > _MAX_BYTES:
            raise ValueError(
                f"artefato excede {_MAX_BYTES} bytes (recebido {len(content)})"
            )
        row = await self._repo.put(
            content=content, filename=filename, mime_type=mime_type
        )
        return Artifact(
            id=row["id"].replace("-", ""),  # mantém o formato hex sem hífen
            filename=row["filename"],
            mime_type=row["mime_type"],
            content=row["content"],
            created_at=row["created_at"],
        )

    async def get(self, artifact_id: str) -> Artifact | None:
        row = await self._repo.get(artifact_id, ttl_seconds=self._ttl)
        if not row:
            return None
        return Artifact(
            id=row["id"].replace("-", ""),
            filename=row["filename"],
            mime_type=row["mime_type"],
            content=bytes(row["content"]),
            created_at=row["created_at"],
        )

    async def gc(self) -> int:
        """GC explícito de artefatos expirados. Pode ser chamado por cron."""
        return await self._repo.delete_expired(ttl_seconds=self._ttl)


# Instância singleton compartilhada via DI. Stateless agora (toda I/O é
# repo→Postgres); manter por compat de import e injeção.
_global_store = ArtifactStore()


def get_artifact_store() -> ArtifactStore:
    return _global_store
