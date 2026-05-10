"""Testes do ArtifactStore agora persistido no Postgres.

Antes era um dict in-memory single-worker; agora a tabela `artifacts`
permite multi-worker e sobrevive a restart. API pública preservada,
então estes testes cobrem o contrato esperado pelos callers (radar
service / router).
"""

from __future__ import annotations

import pytest

from app.adapters.db.postgres import connect
from app.core.services.artifact_store import ArtifactStore


@pytest.mark.asyncio
async def test_put_get_roundtrip():
    store = ArtifactStore(ttl_seconds=60)
    art = await store.put(
        content="hello,world\n1,2\n",
        filename="exemplo.csv",
        mime_type="text/csv",
    )
    assert art.id and len(art.id) == 32  # uuid hex sem hífen
    assert art.filename == "exemplo.csv"

    fetched = await store.get(art.id)
    assert fetched is not None
    assert fetched.content == b"hello,world\n1,2\n"
    assert fetched.mime_type == "text/csv"


@pytest.mark.asyncio
async def test_get_inexistente_devolve_none():
    store = ArtifactStore()
    assert await store.get("00000000000000000000000000000000") is None
    # id inválido (não-uuid) também devolve None sem estourar.
    assert await store.get("nao-eh-uuid") is None


@pytest.mark.asyncio
async def test_ttl_expira_artefato():
    """Forçando created_at no passado via UPDATE direto — simula expiração."""
    store = ArtifactStore(ttl_seconds=60)
    art = await store.put(content=b"x", filename="x.bin", mime_type="application/octet-stream")

    # Recém-criado: legível.
    assert await store.get(art.id) is not None

    # "envelhece" o registro em 2 horas — bem além do TTL de 60s.
    async with connect() as db:
        await db.execute(
            "UPDATE artifacts SET created_at = NOW() - INTERVAL '2 hours' WHERE id::text = $1",
            f"{art.id[:8]}-{art.id[8:12]}-{art.id[12:16]}-{art.id[16:20]}-{art.id[20:]}",
        )

    # Agora deveria estar expirado.
    assert await store.get(art.id) is None


@pytest.mark.asyncio
async def test_gc_apaga_expirados():
    store = ArtifactStore(ttl_seconds=60)
    a = await store.put(content=b"a", filename="a.txt", mime_type="text/plain")
    b = await store.put(content=b"b", filename="b.txt", mime_type="text/plain")

    # Envelhece SÓ o `a`.
    async with connect() as db:
        await db.execute(
            "UPDATE artifacts SET created_at = NOW() - INTERVAL '2 hours' WHERE id::text = $1",
            f"{a.id[:8]}-{a.id[8:12]}-{a.id[12:16]}-{a.id[16:20]}-{a.id[20:]}",
        )

    deleted = await store.gc()
    assert deleted >= 1

    # `b` sobreviveu, `a` foi.
    assert await store.get(a.id) is None
    assert await store.get(b.id) is not None


@pytest.mark.asyncio
async def test_hard_cap_10mb():
    store = ArtifactStore()
    big = b"x" * (10 * 1024 * 1024 + 1)
    with pytest.raises(ValueError, match="excede"):
        await store.put(content=big, filename="big.bin", mime_type="application/octet-stream")
