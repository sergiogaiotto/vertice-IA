"""Store em memória de artefatos gerados por execuções de módulo.

Cada artefato fica disponível por TTL (default 30 min) para download via
GET /api/radar/artifacts/{artifact_id}.

Implementação proposital com dict + lock asyncio — para um deploy multi-worker
seria substituído por Redis ou disco compartilhado, mas para uso single-worker
local (FastAPI dev) é suficiente e zero-config.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass


@dataclass
class Artifact:
    id: str
    filename: str
    mime_type: str
    content: bytes
    created_at: float


class ArtifactStore:
    def __init__(self, ttl_seconds: int = 1800):
        self._store: dict[str, Artifact] = {}
        self._lock = asyncio.Lock()
        self._ttl = ttl_seconds

    async def put(self, content: bytes | str, filename: str, mime_type: str) -> Artifact:
        if isinstance(content, str):
            content = content.encode("utf-8")
        art = Artifact(
            id=uuid.uuid4().hex,
            filename=filename,
            mime_type=mime_type,
            content=content,
            created_at=time.time(),
        )
        async with self._lock:
            self._gc_locked()
            self._store[art.id] = art
        return art

    async def get(self, artifact_id: str) -> Artifact | None:
        async with self._lock:
            self._gc_locked()
            return self._store.get(artifact_id)

    def _gc_locked(self) -> None:
        now = time.time()
        expired = [k for k, v in self._store.items() if now - v.created_at > self._ttl]
        for k in expired:
            self._store.pop(k, None)


# instância singleton compartilhada via DI
_global_store = ArtifactStore()


def get_artifact_store() -> ArtifactStore:
    return _global_store
