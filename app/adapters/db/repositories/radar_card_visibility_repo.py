"""Repositório PostgreSQL da visibilidade dos cards da tela Voz do Cliente.

Sidecar do `radar_user_state.state_json` — sincroniza os cards visíveis do
usuário com uma tabela normalizada para permitir queries de compartilhamento
e listagem administrativa.
"""

from __future__ import annotations

from typing import Any

from app.adapters.db.postgres import connect


VALID_VISIBILITY = ("private", "public_lideranca", "public_analista")


class PgRadarCardVisibilityRepository:

    _COLS = (
        "card_uid, owner_id, owner_username, "
        "created_by_id, created_by_username, "
        "group_id, group_title, module_id, module_name, module_description, "
        "visibility, previous_visibility, card_json, feature, "
        "visibility_changed_by_id, visibility_changed_by_username, "
        "visibility_changed_at, "
        "owner_changed_by_id, owner_changed_by_username, owner_changed_at, "
        "created_at, updated_at"
    )

    async def get(self, card_uid: str) -> dict | None:
        async with connect() as db:
            row = await db.fetchrow(
                f"SELECT {self._COLS} FROM radar_card_visibility "
                "WHERE card_uid = $1",
                card_uid,
            )
            return self._row_to_dict(row) if row else None

    async def list_for_owner(self, owner_id: str) -> dict[str, dict]:
        """Mapa uid → row dos cards do dono."""
        async with connect() as db:
            rows = await db.fetch(
                f"SELECT {self._COLS} FROM radar_card_visibility "
                "WHERE owner_id = $1",
                owner_id,
            )
            return {r["card_uid"]: self._row_to_dict(r) for r in rows}

    async def list_foreign_uids(
        self, uids: list[str] | set[str], owner_id: str
    ) -> set[str]:
        """Dentro de ``uids``, retorna o subset que JÁ pertence a outro dono.

        Usado como defesa no PUT /api/radar/state: cliente pode mandar UIDs
        residuais de outro usuário (localStorage compartilhado, edição manual,
        cliente velho). O servidor filtra esses antes de salvar.
        """
        uid_list = [u for u in uids if u]
        if not uid_list:
            return set()
        async with connect() as db:
            rows = await db.fetch(
                "SELECT card_uid FROM radar_card_visibility "
                "WHERE card_uid = ANY($1::text[]) AND owner_id <> $2",
                uid_list, owner_id,
            )
            return {r["card_uid"] for r in rows}

    async def list_visible_to(
        self, user_id: str, user_roles: list[str]
    ) -> list[dict]:
        """Cards de OUTROS usuários que o `user_id` pode ver dado o set de
        roles."""
        is_lideranca = any(r in {"admin", "supervisor", "root"} for r in user_roles)
        if is_lideranca:
            allowed = ["public_lideranca", "public_analista"]
        else:
            allowed = ["public_analista"]
        sql = (
            f"SELECT {self._COLS} "
            "FROM radar_card_visibility "
            "WHERE owner_id <> $1 AND visibility = ANY($2::text[]) "
            "ORDER BY updated_at DESC"
        )
        async with connect() as db:
            rows = await db.fetch(sql, user_id, allowed)
            return [self._row_to_dict(r) for r in rows]

    async def list_all(self) -> list[dict]:
        async with connect() as db:
            rows = await db.fetch(
                f"SELECT {self._COLS} FROM radar_card_visibility "
                "ORDER BY updated_at DESC"
            )
            return [self._row_to_dict(r) for r in rows]

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
    ) -> bool:
        """Upsert do card. Em INSERTs, snapshota o criador (owner=criador inicial).
        Em UPDATEs do MESMO dono, atualiza metadados (group, module, card_json).
        Se o `card_uid` já pertence a OUTRO dono, NÃO faz upsert — é tentativa
        de roubo cross-user (ex.: localStorage residual de outro usuário no
        mesmo browser). Devolve True se upsert ocorreu, False se foi rejeitado.

        ``created_by_*`` é snapshot do criador e NUNCA é sobrescrito.
        ``previous_visibility`` também é preservado (transição vai pelo
        ``update_visibility`` separado).
        """
        if visibility not in VALID_VISIBILITY:
            visibility = "private"
        async with connect() as db:
            # Defense-in-depth: rejeita atualização cross-user. O frontend
            # também é fixado (storageKey namespaced por user_id), mas se um
            # cliente velho mandar dados de outro dono, não corromper o DB.
            current_owner = await db.fetchval(
                "SELECT owner_id FROM radar_card_visibility WHERE card_uid = $1",
                card_uid,
            )
            if current_owner is not None and current_owner != owner_id:
                return False

            await db.execute(
                """
                INSERT INTO radar_card_visibility
                  (card_uid, owner_id, owner_username, created_by_id,
                   created_by_username, group_id, group_title, module_id,
                   module_name, module_description, visibility, card_json,
                   feature, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
                        $11, $12::jsonb, $13, NOW())
                ON CONFLICT (card_uid) DO UPDATE SET
                  owner_username     = EXCLUDED.owner_username,
                  group_id           = EXCLUDED.group_id,
                  group_title        = EXCLUDED.group_title,
                  module_id          = EXCLUDED.module_id,
                  module_name        = EXCLUDED.module_name,
                  module_description = EXCLUDED.module_description,
                  card_json          = EXCLUDED.card_json,
                  feature            = EXCLUDED.feature,
                  updated_at         = NOW()
                """,
                card_uid, owner_id, owner_username,
                owner_id, owner_username,        # created_by_id/_username no INSERT
                group_id, group_title, module_id, module_name,
                module_description, visibility, card_json, feature,
            )
            return True

    async def update_visibility(
        self,
        card_uid: str,
        visibility: str,
        actor_id: str | None = None,
        actor_username: str | None = None,
    ) -> bool:
        """Atualiza visibility e armazena a anterior em previous_visibility.

        Faz a transição atômica em uma única UPDATE com expressão que captura
        o valor anterior. Se o novo == atual, no-op.

        ``actor_id``/``actor_username`` registram quem fez a mudança — ato
        administrativo sensível (admin/supervisor pode mudar visibility de
        cards alheios via /admin/cards-em-tela). NULL é aceito por compat,
        mas todos os callers de produção devem preencher.
        """
        if visibility not in VALID_VISIBILITY:
            return False
        async with connect() as db:
            row = await db.fetchrow(
                "SELECT visibility FROM radar_card_visibility "
                "WHERE card_uid = $1",
                card_uid,
            )
            if not row:
                return False
            current = row["visibility"] or "private"
            if current == visibility:
                return True
            result = await db.execute(
                "UPDATE radar_card_visibility "
                "SET visibility = $1, previous_visibility = $2, "
                "    visibility_changed_by_id = $3, "
                "    visibility_changed_by_username = $4, "
                "    visibility_changed_at = NOW(), "
                "    updated_at = NOW() "
                "WHERE card_uid = $5",
                visibility, current, actor_id, actor_username, card_uid,
            )
            # `UPDATE 1` se conseguiu, `UPDATE 0` se não.
            return result.endswith(" 1")

    async def change_owner(
        self,
        card_uid: str,
        new_owner_id: str,
        new_owner_username: str | None,
        actor_id: str | None = None,
        actor_username: str | None = None,
    ) -> bool:
        """Altera o dono atual; preserva created_by_*.

        ``actor_id``/``actor_username`` registram quem fez o ato administrativo
        (admin/supervisor) — simétrico ao tracking de mudança de visibility.
        NULL é aceito por compat com callers antigos.
        """
        async with connect() as db:
            result = await db.execute(
                "UPDATE radar_card_visibility "
                "SET owner_id = $1, owner_username = $2, "
                "    owner_changed_by_id = $3, "
                "    owner_changed_by_username = $4, "
                "    owner_changed_at = NOW(), "
                "    updated_at = NOW() "
                "WHERE card_uid = $5",
                new_owner_id, new_owner_username,
                actor_id, actor_username, card_uid,
            )
            return result.endswith(" 1")

    async def sync_owner_cards(
        self,
        owner_id: str,
        owner_username: str | None,
        cards: list[dict],
    ) -> None:
        """Substitui o conjunto de cards do dono."""
        existing = await self.list_for_owner(owner_id)
        incoming_uids = {c.get("uid") for c in cards if c.get("uid")}

        for c in cards:
            uid = c.get("uid")
            if not uid:
                continue
            wanted_vis = c.get("visibility")
            if wanted_vis not in VALID_VISIBILITY:
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

        stale = [uid for uid in existing if uid not in incoming_uids]
        if stale:
            async with connect() as db:
                await db.execute(
                    "DELETE FROM radar_card_visibility "
                    "WHERE owner_id = $1 AND card_uid = ANY($2::text[])",
                    owner_id, stale,
                )

    async def delete(self, card_uid: str) -> None:
        async with connect() as db:
            await db.execute(
                "DELETE FROM radar_card_visibility WHERE card_uid = $1",
                card_uid,
            )

    @staticmethod
    def _row_to_dict(row: Any) -> dict:
        # `card_json` já vem como dict/list (JSONB decodificado por asyncpg).
        return {
            "card_uid":            row["card_uid"],
            "owner_id":            row["owner_id"],
            "owner_username":      row["owner_username"],
            "created_by_id":       row["created_by_id"],
            "created_by_username": row["created_by_username"],
            "group_id":            row["group_id"],
            "group_title":         row["group_title"],
            "module_id":           row["module_id"],
            "module_name":         row["module_name"],
            "module_description":  row["module_description"],
            "visibility":          row["visibility"],
            "previous_visibility": row["previous_visibility"],
            "card_json":           row["card_json"],
            "feature":             row["feature"],
            "visibility_changed_by_id":       row["visibility_changed_by_id"],
            "visibility_changed_by_username": row["visibility_changed_by_username"],
            "visibility_changed_at":          row["visibility_changed_at"],
            "owner_changed_by_id":            row["owner_changed_by_id"],
            "owner_changed_by_username":      row["owner_changed_by_username"],
            "owner_changed_at":               row["owner_changed_at"],
            "created_at":          row["created_at"],
            "updated_at":          row["updated_at"],
        }
