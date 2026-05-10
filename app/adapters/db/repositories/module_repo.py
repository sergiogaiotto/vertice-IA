"""Repositório PostgreSQL do registry de módulos."""

from __future__ import annotations

from uuid import UUID

from app.adapters.db.postgres import connect
from app.core.domain.entities import Module, ModuleStatus
from app.core.ports.repositories import ModuleRepository


_SELECT_COLS = (
    "id::text AS id, name, endpoint_url, status, config_params, description, "
    "skill_path, response_type, response_config"
)


def _row_to_module(row) -> Module:
    return Module(
        id=UUID(row["id"]),
        name=row["name"],
        endpoint_url=row["endpoint_url"],
        status=ModuleStatus(row["status"]),
        config_params=row["config_params"] or {},
        description=row["description"] or "",
        skill_path=row["skill_path"],
        response_type=row["response_type"] or "text",
        response_config=row["response_config"] or {},
    )


class PgModuleRepository(ModuleRepository):

    async def list_active(self) -> list[Module]:
        async with connect() as db:
            rows = await db.fetch(
                f"SELECT {_SELECT_COLS} FROM modules "
                "WHERE status = 'active' ORDER BY name"
            )
            return [_row_to_module(r) for r in rows]

    async def list_all(self) -> list[Module]:
        async with connect() as db:
            rows = await db.fetch(
                f"SELECT {_SELECT_COLS} FROM modules ORDER BY name"
            )
            return [_row_to_module(r) for r in rows]

    async def get_by_name(self, name: str) -> Module | None:
        async with connect() as db:
            row = await db.fetchrow(
                f"SELECT {_SELECT_COLS} FROM modules WHERE name = $1", name
            )
            return _row_to_module(row) if row else None

    async def get(self, module_id):
        async with connect() as db:
            row = await db.fetchrow(
                f"SELECT {_SELECT_COLS} FROM modules WHERE id = $1::uuid",
                str(module_id),
            )
            return _row_to_module(row) if row else None

    async def delete(self, module_id) -> None:
        async with connect() as db:
            await db.execute(
                "DELETE FROM modules WHERE id = $1::uuid", str(module_id)
            )

    async def upsert(self, module: Module) -> Module:
        async with connect() as db:
            await db.execute(
                """
                INSERT INTO modules (id, name, endpoint_url, status, config_params,
                                     description, skill_path, response_type,
                                     response_config)
                VALUES ($1::uuid, $2, $3, $4, $5::jsonb, $6, $7, $8, $9::jsonb)
                ON CONFLICT (name) DO UPDATE SET
                    endpoint_url    = EXCLUDED.endpoint_url,
                    status          = EXCLUDED.status,
                    config_params   = EXCLUDED.config_params,
                    description     = EXCLUDED.description,
                    skill_path      = EXCLUDED.skill_path,
                    response_type   = EXCLUDED.response_type,
                    response_config = EXCLUDED.response_config
                """,
                str(module.id), module.name, module.endpoint_url,
                module.status.value, module.config_params, module.description,
                module.skill_path, module.response_type, module.response_config,
            )
            return module
