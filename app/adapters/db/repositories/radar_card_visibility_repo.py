"""Repositório SQLite da visibilidade dos cards da tela Voz do Cliente.

Sidecar do `radar_user_state.state_json` — sincroniza os cards visíveis do
usuário com uma tabela normalizada para permitir queries de compartilhamento
(ver cards de outros usuários conforme o nível de visibilidade) e listagem
administrativa.
"""

from __future__ import annotations

import json
from typing import Any

from app.adapters.db.sqlite import connect


VALID_VISIBILITY = ("private", "public_lideranca", "public_analista")


class SqliteRadarCardVisibilityRepository:

    async def get(self, card_uid: str) -> dict | None:
        async with connect() as db:
            cur = await db.execute(
                "SELECT card_uid, owner_id, owner_username, group_id, group_title, "
                "module_id, module_name, module_description, visibility, card_json, "
                "feature, created_at, updated_at "
                "FROM radar_card_visibility WHERE card_uid = ?",
                (card_uid,),
            )
            row = await cur.fetchone()
            return self._row_to_dict(row) if row else None

    async def list_for_owner(self, owner_id: str) -> dict[str, dict]:
        """Retorna mapa uid → row dos cards do dono. Usado p/ override inline no GET state."""
        async with connect() as db:
            cur = await db.execute(
                "SELECT card_uid, owner_id, owner_username, group_id, group_title, "
                "module_id, module_name, module_description, visibility, card_json, "
                "feature, created_at, updated_at "
                "FROM radar_card_visibility WHERE owner_id = ?",
                (owner_id,),
            )
            return {r[0]: self._row_to_dict(r) for r in await cur.fetchall()}

    async def list_visible_to(
        self, user_id: str, user_roles: list[str]
    ) -> list[dict]:
        """Cards de OUTROS usuários que o `user_id` pode ver dado o set de roles.

        - admin/supervisor/root: vê private (apenas próprios), public_lideranca e public_analista de todos.
        - analista_n3 (e fallback): vê apenas public_analista.
        - Em todos os casos, exclui os próprios (owner_id != user_id) — esses já vêm pelo state.
        """
        is_lideranca = any(r in {"admin", "supervisor", "root"} for r in user_roles)
        if is_lideranca:
            allowed = ("public_lideranca", "public_analista")
        else:
            allowed = ("public_analista",)
        placeholders = ",".join("?" for _ in allowed)
        sql = (
            "SELECT card_uid, owner_id, owner_username, group_id, group_title, "
            "module_id, module_name, module_description, visibility, card_json, "
            "feature, created_at, updated_at "
            "FROM radar_card_visibility "
            f"WHERE owner_id != ? AND visibility IN ({placeholders}) "
            "ORDER BY updated_at DESC"
        )
        async with connect() as db:
            cur = await db.execute(sql, (user_id, *allowed))
            return [self._row_to_dict(r) for r in await cur.fetchall()]

    async def list_all(self) -> list[dict]:
        """Listagem global — usada na visão administrativa."""
        async with connect() as db:
            cur = await db.execute(
                "SELECT card_uid, owner_id, owner_username, group_id, group_title, "
                "module_id, module_name, module_description, visibility, card_json, "
                "feature, created_at, updated_at "
                "FROM radar_card_visibility ORDER BY updated_at DESC"
            )
            return [self._row_to_dict(r) for r in await cur.fetchall()]

    async def upsert(
        self,
        card_uid: str,
        owner_id: str,
        owner_username: str | None,
        group_id: str | None,
        group_title: str | None,
        module_id: str | None,
        module_name: str | None,
        module_description: str | None,
        visibility: str,
        card_json: dict | None,
        feature: str = "radar",
    ) -> None:
        if visibility not in VALID_VISIBILITY:
            visibility = "private"
        async with connect() as db:
            await db.execute(
                """
                INSERT INTO radar_card_visibility
                  (card_uid, owner_id, owner_username, group_id, group_title,
                   module_id, module_name, module_description, visibility,
                   card_json, feature, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(card_uid) DO UPDATE SET
                  owner_username = excluded.owner_username,
                  group_id = excluded.group_id,
                  group_title = excluded.group_title,
                  module_id = excluded.module_id,
                  module_name = excluded.module_name,
                  module_description = excluded.module_description,
                  card_json = excluded.card_json,
                  feature = excluded.feature,
                  updated_at = CURRENT_TIMESTAMP
                """,
                (
                    card_uid,
                    owner_id,
                    owner_username,
                    group_id,
                    group_title,
                    module_id,
                    module_name,
                    module_description,
                    visibility,
                    json.dumps(card_json) if card_json is not None else None,
                    feature,
                ),
            )
            await db.commit()

    async def update_visibility(
        self, card_uid: str, visibility: str
    ) -> bool:
        if visibility not in VALID_VISIBILITY:
            return False
        async with connect() as db:
            cur = await db.execute(
                "UPDATE radar_card_visibility SET visibility = ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE card_uid = ?",
                (visibility, card_uid),
            )
            await db.commit()
            return cur.rowcount > 0

    async def sync_owner_cards(
        self,
        owner_id: str,
        owner_username: str | None,
        cards: list[dict],
    ) -> None:
        """Substitui o conjunto de cards do dono.

        - Upsert de cada card recebido (preservando `visibility` da linha existente
          se o card não trouxer um valor explícito — assim o frontend não precisa
          ecoar o campo em todo PUT).
        - Apaga linhas do dono cujo `card_uid` não está mais no payload.
        """
        # Carrega visibilities existentes para preservar quando payload não envia.
        existing = await self.list_for_owner(owner_id)
        incoming_uids = {c.get("uid") for c in cards if c.get("uid")}

        for c in cards:
            uid = c.get("uid")
            if not uid:
                continue
            wanted_vis = c.get("visibility")
            if wanted_vis not in VALID_VISIBILITY:
                # default: preserva o atual; se for novo, assume 'private'
                prev = existing.get(uid)
                wanted_vis = prev["visibility"] if prev else "private"
            await self.upsert(
                card_uid=uid,
                owner_id=owner_id,
                owner_username=owner_username,
                group_id=c.get("group_id"),
                group_title=c.get("group_title"),
                module_id=c.get("module_id"),
                module_name=c.get("module_name"),
                module_description=c.get("module_description"),
                visibility=wanted_vis,
                card_json=c.get("card_json"),
                feature=c.get("feature", "radar"),
            )

        # Remove cards que sumiram do estado do dono.
        stale = [uid for uid in existing if uid not in incoming_uids]
        if stale:
            placeholders = ",".join("?" for _ in stale)
            async with connect() as db:
                await db.execute(
                    f"DELETE FROM radar_card_visibility "
                    f"WHERE owner_id = ? AND card_uid IN ({placeholders})",
                    (owner_id, *stale),
                )
                await db.commit()

    async def delete(self, card_uid: str) -> None:
        async with connect() as db:
            await db.execute(
                "DELETE FROM radar_card_visibility WHERE card_uid = ?", (card_uid,)
            )
            await db.commit()

    @staticmethod
    def _row_to_dict(row: Any) -> dict:
        try:
            card_json = json.loads(row[9]) if row[9] else None
        except Exception:
            card_json = None
        return {
            "card_uid": row[0],
            "owner_id": row[1],
            "owner_username": row[2],
            "group_id": row[3],
            "group_title": row[4],
            "module_id": row[5],
            "module_name": row[6],
            "module_description": row[7],
            "visibility": row[8],
            "card_json": card_json,
            "feature": row[10],
            "created_at": row[11],
            "updated_at": row[12],
        }
