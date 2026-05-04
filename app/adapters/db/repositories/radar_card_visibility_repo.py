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

    _COLS = (
        "card_uid, owner_id, owner_username, created_by_id, created_by_username, "
        "group_id, group_title, module_id, module_name, module_description, "
        "visibility, previous_visibility, card_json, feature, created_at, updated_at"
    )

    async def get(self, card_uid: str) -> dict | None:
        async with connect() as db:
            cur = await db.execute(
                f"SELECT {self._COLS} FROM radar_card_visibility WHERE card_uid = ?",
                (card_uid,),
            )
            row = await cur.fetchone()
            return self._row_to_dict(row) if row else None

    async def list_for_owner(self, owner_id: str) -> dict[str, dict]:
        """Retorna mapa uid → row dos cards do dono. Usado p/ override inline no GET state."""
        async with connect() as db:
            cur = await db.execute(
                f"SELECT {self._COLS} FROM radar_card_visibility WHERE owner_id = ?",
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
            f"SELECT {self._COLS} "
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
                f"SELECT {self._COLS} FROM radar_card_visibility ORDER BY updated_at DESC"
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
        """Upsert do card. Em INSERTs, snapshota o criador (owner=criador inicial).
        Em UPDATEs, NUNCA sobrescreve created_by_*. Não altera previous_visibility
        — mudanças de visibility vão por update_visibility, que captura o anterior.
        """
        if visibility not in VALID_VISIBILITY:
            visibility = "private"
        async with connect() as db:
            await db.execute(
                """
                INSERT INTO radar_card_visibility
                  (card_uid, owner_id, owner_username, created_by_id, created_by_username,
                   group_id, group_title, module_id, module_name, module_description,
                   visibility, card_json, feature, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
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
                    owner_id,                # created_by_id no INSERT (owner inicial)
                    owner_username,          # created_by_username no INSERT
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
        """Atualiza visibility e armazena a anterior em previous_visibility.

        Comparação atômica: lê o estado atual, salva como previous, grava o novo.
        Se o novo == atual, no-op (não polui previous_visibility com duplicatas).
        """
        if visibility not in VALID_VISIBILITY:
            return False
        async with connect() as db:
            cur = await db.execute(
                "SELECT visibility FROM radar_card_visibility WHERE card_uid = ?",
                (card_uid,),
            )
            row = await cur.fetchone()
            if not row:
                return False
            current = row[0] or "private"
            if current == visibility:
                return True  # nada a fazer
            cur = await db.execute(
                "UPDATE radar_card_visibility "
                "SET visibility = ?, previous_visibility = ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE card_uid = ?",
                (visibility, current, card_uid),
            )
            await db.commit()
            return cur.rowcount > 0

    async def change_owner(
        self,
        card_uid: str,
        new_owner_id: str,
        new_owner_username: str | None,
    ) -> bool:
        """Altera o dono atual; preserva created_by_* (criador original).
        Limpa previous_visibility — transferência de dono é evento independente.
        """
        async with connect() as db:
            cur = await db.execute(
                "UPDATE radar_card_visibility "
                "SET owner_id = ?, owner_username = ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE card_uid = ?",
                (new_owner_id, new_owner_username, card_uid),
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
        # Ordem das colunas em `_COLS`:
        # 0=card_uid, 1=owner_id, 2=owner_username, 3=created_by_id,
        # 4=created_by_username, 5=group_id, 6=group_title, 7=module_id,
        # 8=module_name, 9=module_description, 10=visibility,
        # 11=previous_visibility, 12=card_json, 13=feature,
        # 14=created_at, 15=updated_at
        try:
            card_json = json.loads(row[12]) if row[12] else None
        except Exception:
            card_json = None
        return {
            "card_uid": row[0],
            "owner_id": row[1],
            "owner_username": row[2],
            "created_by_id": row[3],
            "created_by_username": row[4],
            "group_id": row[5],
            "group_title": row[6],
            "module_id": row[7],
            "module_name": row[8],
            "module_description": row[9],
            "visibility": row[10],
            "previous_visibility": row[11],
            "card_json": card_json,
            "feature": row[13],
            "created_at": row[14],
            "updated_at": row[15],
        }
