"""Repositório PostgreSQL de Prompts.

Suporta relação N:N entre prompt e módulos via coluna `module_names` (JSONB).
A coluna legada `module_name` é mantida apenas por compat — preenchida com o
primeiro item da lista ao gravar.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from app.adapters.db.postgres import connect
from app.core.domain.entities import PromptBundle
from app.core.ports.repositories import PromptRepository


_SELECT = (
    "SELECT id::text AS id, module_names, module_name, name, version, "
    "input_guardrail, system_prompt, output_guardrail, is_active, created_at "
    "FROM prompts"
)


def _row_to_bundle(row) -> PromptBundle:
    raw_modules = row["module_names"]      # JSONB → list já decodificado
    legacy = row["module_name"]
    modules: list[str] = []
    if isinstance(raw_modules, list):
        modules = [str(m) for m in raw_modules if m]
    if not modules and legacy:
        modules = [legacy]

    created_at = row["created_at"]
    if not isinstance(created_at, datetime):
        created_at = datetime.utcnow()

    return PromptBundle(
        id=UUID(row["id"]),
        module_names=modules,
        name=row["name"],
        version=row["version"],
        input_guardrail=row["input_guardrail"] or "",
        system_prompt=row["system_prompt"] or "",
        output_guardrail=row["output_guardrail"] or "",
        is_active=bool(row["is_active"]),
        created_at=created_at,
    )


# Cláusula que detecta prompts cujo array JSONB contém o módulo X.
# Usa o operador `?` do JSONB (existência de chave/string em array de top
# level) — indexável via GIN. Mantém OR com module_name para compat com
# linhas legadas.
_MODULE_MATCH_CLAUSE = "(module_names ? $%d OR module_name = $%d)"


class PgPromptRepository(PromptRepository):

    async def list_for_module(self, module_name: str) -> list[PromptBundle]:
        clause = _MODULE_MATCH_CLAUSE % (1, 2)
        async with connect() as db:
            rows = await db.fetch(
                f"{_SELECT} WHERE {clause} ORDER BY name, version DESC",
                module_name, module_name,
            )
            return [_row_to_bundle(r) for r in rows]

    async def get_active(self, module_name: str, name: str) -> PromptBundle | None:
        clause = _MODULE_MATCH_CLAUSE % (2, 3)
        async with connect() as db:
            row = await db.fetchrow(
                f"{_SELECT} WHERE name = $1 AND is_active = TRUE AND {clause} "
                "ORDER BY version DESC LIMIT 1",
                name, module_name, module_name,
            )
            return _row_to_bundle(row) if row else None

    async def save(self, bundle: PromptBundle) -> PromptBundle:
        async with connect() as db:
            async with db.transaction():
                # desativa versões anteriores com mesmo `name`
                await db.execute(
                    "UPDATE prompts SET is_active = FALSE WHERE name = $1",
                    bundle.name,
                )
                legacy = bundle.module_names[0] if bundle.module_names else None
                await db.execute(
                    "INSERT INTO prompts (id, module_name, module_names, name, "
                    "version, input_guardrail, system_prompt, output_guardrail, "
                    "is_active) "
                    "VALUES ($1::uuid, $2, $3::jsonb, $4, $5, $6, $7, $8, $9)",
                    str(bundle.id), legacy, bundle.module_names or [],
                    bundle.name, bundle.version, bundle.input_guardrail,
                    bundle.system_prompt, bundle.output_guardrail,
                    bundle.is_active,
                )
            return bundle

    async def get(self, prompt_id) -> PromptBundle | None:
        async with connect() as db:
            row = await db.fetchrow(
                f"{_SELECT} WHERE id = $1::uuid", str(prompt_id)
            )
            return _row_to_bundle(row) if row else None

    async def list_all(self) -> list[PromptBundle]:
        async with connect() as db:
            rows = await db.fetch(f"{_SELECT} ORDER BY name, version DESC")
            return [_row_to_bundle(r) for r in rows]

    async def promote(self, prompt_id) -> None:
        async with connect() as db:
            async with db.transaction():
                name = await db.fetchval(
                    "SELECT name FROM prompts WHERE id = $1::uuid",
                    str(prompt_id),
                )
                if not name:
                    return
                await db.execute(
                    "UPDATE prompts SET is_active = FALSE WHERE name = $1", name
                )
                await db.execute(
                    "UPDATE prompts SET is_active = TRUE WHERE id = $1::uuid",
                    str(prompt_id),
                )

    async def delete(self, prompt_id) -> None:
        async with connect() as db:
            await db.execute(
                "DELETE FROM prompts WHERE id = $1::uuid", str(prompt_id)
            )

    async def set_modules(self, prompt_id, module_names: list[str]) -> None:
        legacy = module_names[0] if module_names else None
        async with connect() as db:
            await db.execute(
                "UPDATE prompts SET module_names = $1::jsonb, module_name = $2 "
                "WHERE id = $3::uuid",
                module_names or [], legacy, str(prompt_id),
            )
