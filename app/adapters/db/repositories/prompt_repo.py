"""Repositório SQLite de Prompts.

Suporta relação N:N entre prompt e módulos via coluna `module_names` (JSON).
A coluna legada `module_name` é mantida apenas por compatibilidade — sempre
preenchida com o primeiro item da lista ao gravar.
"""

from __future__ import annotations

import json
from datetime import datetime
from uuid import UUID

from app.adapters.db.sqlite import connect
from app.core.domain.entities import PromptBundle
from app.core.ports.repositories import PromptRepository


def _row_to_bundle(row) -> PromptBundle:
    raw_modules = row[1]  # module_names JSON
    legacy = row[2]       # module_name string
    modules: list[str] = []
    if raw_modules:
        try:
            parsed = json.loads(raw_modules)
            if isinstance(parsed, list):
                modules = [str(m) for m in parsed if m]
        except (json.JSONDecodeError, TypeError):
            modules = []
    if not modules and legacy:
        modules = [legacy]

    return PromptBundle(
        id=UUID(row[0]),
        module_names=modules,
        name=row[3],
        version=row[4],
        input_guardrail=row[5] or "",
        system_prompt=row[6] or "",
        output_guardrail=row[7] or "",
        is_active=bool(row[8]),
        created_at=datetime.fromisoformat(row[9]) if isinstance(row[9], str) else datetime.utcnow(),
    )


_SELECT = (
    "SELECT id, module_names, module_name, name, version, input_guardrail, "
    "system_prompt, output_guardrail, is_active, created_at FROM prompts"
)


def _module_match_clause(module_name: str) -> tuple[str, str]:
    """Cláusula LIKE para encontrar prompts cujo module_names JSON contém o nome.

    Como nomes de módulos são slugs ([a-z0-9_-]), o padrão `"name"` em JSON
    não dá falso positivo.
    """
    return "(module_names LIKE ? OR module_name = ?)", f'%"{module_name}"%'


class SqlitePromptRepository(PromptRepository):

    async def list_for_module(self, module_name: str) -> list[PromptBundle]:
        clause, like = _module_match_clause(module_name)
        async with connect() as db:
            cur = await db.execute(
                f"{_SELECT} WHERE {clause} ORDER BY name, version DESC",
                (like, module_name),
            )
            return [_row_to_bundle(r) for r in await cur.fetchall()]

    async def get_active(self, module_name: str, name: str) -> PromptBundle | None:
        clause, like = _module_match_clause(module_name)
        async with connect() as db:
            cur = await db.execute(
                f"{_SELECT} WHERE name = ? AND is_active = 1 AND {clause} "
                "ORDER BY version DESC LIMIT 1",
                (name, like, module_name),
            )
            row = await cur.fetchone()
            return _row_to_bundle(row) if row else None

    async def save(self, bundle: PromptBundle) -> PromptBundle:
        async with connect() as db:
            # desativa versões anteriores com mesmo `name` (escopo do versionamento)
            await db.execute(
                "UPDATE prompts SET is_active = 0 WHERE name = ?",
                (bundle.name,),
            )
            modules_json = json.dumps(bundle.module_names or [])
            legacy = bundle.module_names[0] if bundle.module_names else None
            await db.execute(
                "INSERT INTO prompts (id, module_name, module_names, name, version, "
                "input_guardrail, system_prompt, output_guardrail, is_active) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(bundle.id),
                    legacy,
                    modules_json,
                    bundle.name,
                    bundle.version,
                    bundle.input_guardrail,
                    bundle.system_prompt,
                    bundle.output_guardrail,
                    int(bundle.is_active),
                ),
            )
            await db.commit()
            return bundle

    async def get(self, prompt_id) -> PromptBundle | None:
        async with connect() as db:
            cur = await db.execute(f"{_SELECT} WHERE id = ?", (str(prompt_id),))
            row = await cur.fetchone()
            return _row_to_bundle(row) if row else None

    async def list_all(self) -> list[PromptBundle]:
        async with connect() as db:
            cur = await db.execute(f"{_SELECT} ORDER BY name, version DESC")
            return [_row_to_bundle(r) for r in await cur.fetchall()]

    async def promote(self, prompt_id) -> None:
        async with connect() as db:
            cur = await db.execute("SELECT name FROM prompts WHERE id = ?", (str(prompt_id),))
            row = await cur.fetchone()
            if not row:
                return
            await db.execute("UPDATE prompts SET is_active = 0 WHERE name = ?", (row[0],))
            await db.execute("UPDATE prompts SET is_active = 1 WHERE id = ?", (str(prompt_id),))
            await db.commit()

    async def delete(self, prompt_id) -> None:
        async with connect() as db:
            await db.execute("DELETE FROM prompts WHERE id = ?", (str(prompt_id),))
            await db.commit()

    async def set_modules(self, prompt_id, module_names: list[str]) -> None:
        """Atualiza a lista de módulos associados a um prompt."""
        modules_json = json.dumps(module_names or [])
        legacy = module_names[0] if module_names else None
        async with connect() as db:
            await db.execute(
                "UPDATE prompts SET module_names = ?, module_name = ? WHERE id = ?",
                (modules_json, legacy, str(prompt_id)),
            )
            await db.commit()
