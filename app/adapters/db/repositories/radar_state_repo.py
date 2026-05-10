"""Repositório PostgreSQL do estado de Radar (groups/cards) por usuário."""

from __future__ import annotations

from app.adapters.db.postgres import connect


class PgRadarStateRepository:
    """Persistência do array de groups da tela Voz do Cliente, por usuário.

    O `state_json` é tratado como blob opaco — a estrutura é definida pelo
    front-end (Alpine `groups`). Servidor não interpreta, só armazena.
    """

    async def get(self, user_id: str) -> dict | None:
        async with connect() as db:
            row = await db.fetchrow(
                "SELECT state_json, version, updated_at "
                "FROM radar_user_state WHERE user_id = $1",
                user_id,
            )
            if not row:
                return None
            return {
                "state_json": row["state_json"] or "[]",
                "version":    row["version"] or 1,
                "updated_at": row["updated_at"],
            }

    async def put(
        self,
        user_id: str,
        state_json: str,
        expected_version: int | None = None,
    ) -> dict:
        """Upsert otimista. Se `expected_version` for passada, falha em conflito."""
        async with connect() as db:
            async with db.transaction():
                current = await db.fetchval(
                    "SELECT version FROM radar_user_state "
                    "WHERE user_id = $1",
                    user_id,
                )
                current = current or 0

                if (
                    expected_version is not None
                    and current != 0
                    and current != expected_version
                ):
                    return {
                        "ok": False,
                        "conflict": True,
                        "current_version": current,
                    }

                new_version = current + 1
                await db.execute(
                    """
                    INSERT INTO radar_user_state (user_id, state_json, version,
                                                  updated_at)
                    VALUES ($1, $2, $3, NOW())
                    ON CONFLICT (user_id) DO UPDATE SET
                        state_json = EXCLUDED.state_json,
                        version    = EXCLUDED.version,
                        updated_at = NOW()
                    """,
                    user_id, state_json, new_version,
                )
                return {"ok": True, "version": new_version}

    async def delete(self, user_id: str) -> None:
        async with connect() as db:
            await db.execute(
                "DELETE FROM radar_user_state WHERE user_id = $1",
                user_id,
            )

    async def get_preferences(self, user_id: str) -> dict:
        """Preferências por-usuário (cross-device). Defaults a {} se não tiver
        linha. Não cria a linha por leitura — o `set_preferences` faz upsert."""
        async with connect() as db:
            value = await db.fetchval(
                "SELECT preferences FROM radar_user_state WHERE user_id = $1",
                user_id,
            )
            return value or {}

    async def set_preferences(self, user_id: str, patch: dict) -> dict:
        """Merge-update das preferências: ``patch`` sobrescreve chaves
        existentes; chaves ausentes são preservadas. ``null`` na chave a remove.

        Faz upsert — cria a linha em ``radar_user_state`` com state_json vazio
        se ainda não existir, para que preferências persistam mesmo antes do
        primeiro save de state. Devolve o dict final pós-merge.
        """
        async with connect() as db:
            async with db.transaction():
                current = await db.fetchval(
                    "SELECT preferences FROM radar_user_state WHERE user_id = $1",
                    user_id,
                )
                merged = dict(current or {})
                for k, v in (patch or {}).items():
                    if v is None:
                        merged.pop(k, None)
                    else:
                        merged[k] = v
                await db.execute(
                    """
                    INSERT INTO radar_user_state (user_id, state_json, version,
                                                  preferences, updated_at)
                    VALUES ($1, '[]', 1, $2::jsonb, NOW())
                    ON CONFLICT (user_id) DO UPDATE SET
                        preferences = EXCLUDED.preferences,
                        updated_at  = NOW()
                    """,
                    user_id, merged,
                )
                return merged
