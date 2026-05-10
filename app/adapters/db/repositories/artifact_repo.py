"""Repositório PostgreSQL de artefatos efêmeros (downloads pós-execução)."""

from __future__ import annotations

import uuid

from app.adapters.db.postgres import connect


class PgArtifactRepository:
    """CRUD em ``artifacts``. TTL é aplicado no SELECT (WHERE created_at >
    NOW() - INTERVAL). Não há auto-GC — quem quiser pode chamar
    ``delete_expired`` periodicamente."""

    async def put(
        self, content: bytes, filename: str, mime_type: str
    ) -> dict:
        """Insere e devolve o registro completo (com id gerado e created_at)."""
        new_id = uuid.uuid4().hex
        async with connect() as db:
            row = await db.fetchrow(
                """
                INSERT INTO artifacts (id, filename, mime_type, content)
                VALUES ($1::uuid, $2, $3, $4)
                RETURNING id::text, filename, mime_type, content, created_at
                """,
                new_id, filename, mime_type, content,
            )
            return dict(row)

    async def get(self, artifact_id: str, ttl_seconds: int) -> dict | None:
        """Devolve o registro se ainda dentro do TTL; None se expirado/inexistente.

        Aceita id como str (UUID hex sem hífens ou com).
        """
        try:
            uid = uuid.UUID(artifact_id)
        except (ValueError, TypeError):
            return None
        async with connect() as db:
            row = await db.fetchrow(
                """
                SELECT id::text, filename, mime_type, content, created_at
                FROM artifacts
                WHERE id = $1::uuid
                  AND created_at > NOW() - make_interval(secs => $2)
                """,
                str(uid), ttl_seconds,
            )
            return dict(row) if row else None

    async def delete_expired(self, ttl_seconds: int) -> int:
        """Apaga artefatos expirados. Devolve a contagem deletada."""
        async with connect() as db:
            result = await db.execute(
                "DELETE FROM artifacts "
                "WHERE created_at <= NOW() - make_interval(secs => $1)",
                ttl_seconds,
            )
            # `DELETE N` — pega o número.
            try:
                return int(result.rsplit(" ", 1)[-1])
            except ValueError:
                return 0
