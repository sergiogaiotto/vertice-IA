"""Repositório SQLite do registry de módulos."""

from __future__ import annotations

import json
from uuid import UUID

from app.adapters.db.sqlite import connect
from app.core.domain.entities import Module, ModuleStatus
from app.core.ports.repositories import ModuleRepository


_SELECT_COLS = (
    "id, name, endpoint_url, status, config_params, description, skill_path, "
    "response_type, response_config"
)


def _row_to_module(row) -> Module:
    return Module(
        id=UUID(row[0]),
        name=row[1],
        endpoint_url=row[2],
        status=ModuleStatus(row[3]),
        config_params=json.loads(row[4]) if row[4] else {},
        description=row[5] or "",
        skill_path=row[6],
        response_type=row[7] if (len(row) > 7 and row[7]) else "text",
        response_config=(json.loads(row[8]) if (len(row) > 8 and row[8]) else {}),
    )


class SqliteModuleRepository(ModuleRepository):

    async def list_active(self) -> list[Module]:
        async with connect() as db:
            cur = await db.execute(
                f"SELECT {_SELECT_COLS} FROM modules WHERE status = 'active' ORDER BY name"
            )
            return [_row_to_module(r) for r in await cur.fetchall()]

    async def list_all(self) -> list[Module]:
        async with connect() as db:
            cur = await db.execute(f"SELECT {_SELECT_COLS} FROM modules ORDER BY name")
            return [_row_to_module(r) for r in await cur.fetchall()]

    async def get_by_name(self, name: str) -> Module | None:
        async with connect() as db:
            cur = await db.execute(
                f"SELECT {_SELECT_COLS} FROM modules WHERE name = ?", (name,)
            )
            row = await cur.fetchone()
            return _row_to_module(row) if row else None

    async def get(self, module_id):
        async with connect() as db:
            cur = await db.execute(
                f"SELECT {_SELECT_COLS} FROM modules WHERE id = ?", (str(module_id),)
            )
            row = await cur.fetchone()
            return _row_to_module(row) if row else None

    async def delete(self, module_id) -> None:
        async with connect() as db:
            await db.execute("DELETE FROM modules WHERE id = ?", (str(module_id),))
            await db.commit()

    async def upsert(self, module: Module) -> Module:
        async with connect() as db:
            await db.execute(
                "INSERT INTO modules (id, name, endpoint_url, status, config_params, "
                "description, skill_path, response_type, response_config) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(name) DO UPDATE SET "
                "  endpoint_url = excluded.endpoint_url, "
                "  status = excluded.status, "
                "  config_params = excluded.config_params, "
                "  description = excluded.description, "
                "  skill_path = excluded.skill_path, "
                "  response_type = excluded.response_type, "
                "  response_config = excluded.response_config",
                (
                    str(module.id),
                    module.name,
                    module.endpoint_url,
                    module.status.value,
                    json.dumps(module.config_params),
                    module.description,
                    module.skill_path,
                    module.response_type,
                    json.dumps(module.response_config),
                ),
            )
            await db.commit()
            return module
