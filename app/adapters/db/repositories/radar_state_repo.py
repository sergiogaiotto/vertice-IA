"""Repositório SQLite do estado de Radar (groups/cards) por usuário."""

from __future__ import annotations

from app.adapters.db.sqlite import connect


class SqliteRadarStateRepository:
    """Persistência do array de groups da tela Voz do Cliente, por usuário.

    O `state_json` é tratado como blob opaco — a estrutura é definida pelo
    front-end (Alpine `groups`). Servidor não interpreta, só armazena.
    """

    async def get(self, user_id: str) -> dict | None:
        async with connect() as db:
            cur = await db.execute(
                "SELECT state_json, version, updated_at FROM radar_user_state WHERE user_id = ?",
                (user_id,),
            )
            row = await cur.fetchone()
            if not row:
                return None
            return {
                "state_json": row[0] or "[]",
                "version": row[1] or 1,
                "updated_at": row[2],
            }

    async def put(self, user_id: str, state_json: str, expected_version: int | None = None) -> dict:
        """Upsert otimista. Se `expected_version` for passada, falha em conflito."""
        async with connect() as db:
            cur = await db.execute(
                "SELECT version FROM radar_user_state WHERE user_id = ?",
                (user_id,),
            )
            row = await cur.fetchone()
            current = row[0] if row else 0

            if expected_version is not None and row and current != expected_version:
                return {
                    "ok": False,
                    "conflict": True,
                    "current_version": current,
                }

            new_version = current + 1
            await db.execute(
                """
                INSERT INTO radar_user_state (user_id, state_json, version, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id) DO UPDATE SET
                    state_json = excluded.state_json,
                    version    = excluded.version,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (user_id, state_json, new_version),
            )
            await db.commit()
            return {"ok": True, "version": new_version}

    async def delete(self, user_id: str) -> None:
        async with connect() as db:
            await db.execute("DELETE FROM radar_user_state WHERE user_id = ?", (user_id,))
            await db.commit()
