"""Repositório SQLite de ações de Failsafe (human-in-the-loop)."""

from __future__ import annotations

import json
from datetime import datetime
from uuid import UUID

from app.adapters.db.sqlite import connect
from app.core.domain.entities import FailsafeAction, FailsafeStatus
from app.core.ports.repositories import FailsafeRepository


def _row_to_action(row) -> FailsafeAction:
    ts = row[7]
    if isinstance(ts, str):
        try:
            ts = datetime.fromisoformat(ts)
        except ValueError:
            ts = datetime.utcnow()
    return FailsafeAction(
        id=UUID(row[0]),
        module_name=row[1],
        description=row[2],
        payload=json.loads(row[3]) if row[3] else {},
        confidence=row[4] or 0.0,
        status=FailsafeStatus(row[5]),
        requested_by=UUID(row[6]) if row[6] else None,
        decided_by=UUID(row[8]) if row[8] else None,
        created_at=ts,
    )


_SELECT_COLS = (
    "SELECT id, module_name, description, payload, confidence, status, "
    "requested_by, created_at, decided_by FROM failsafe_actions"
)


def _build_filters(
    status: str | None,
    module_name: str | None,
    q: str | None,
) -> tuple[str, list]:
    """Monta cláusula WHERE parametrizada para list/count com mesmos filtros."""
    parts: list[str] = []
    args: list = []
    if status:
        parts.append("status = ?")
        args.append(status)
    if module_name:
        parts.append("module_name = ?")
        args.append(module_name)
    if q:
        parts.append("(description LIKE ? OR module_name LIKE ?)")
        like = f"%{q}%"
        args.extend([like, like])
    where = (" WHERE " + " AND ".join(parts)) if parts else ""
    return where, args


class SqliteFailsafeRepository(FailsafeRepository):

    async def list_pending(self) -> list[FailsafeAction]:
        async with connect() as db:
            cur = await db.execute(
                f"{_SELECT_COLS} WHERE status = 'pending' ORDER BY created_at DESC"
            )
            return [_row_to_action(r) for r in await cur.fetchall()]

    async def list_filtered(
        self,
        status: str | None = None,
        module_name: str | None = None,
        q: str | None = None,
        limit: int = 30,
        offset: int = 0,
    ) -> list[FailsafeAction]:
        where, args = _build_filters(status, module_name, q)
        sql = f"{_SELECT_COLS}{where} ORDER BY created_at DESC LIMIT ? OFFSET ?"
        async with connect() as db:
            cur = await db.execute(sql, (*args, limit, offset))
            return [_row_to_action(r) for r in await cur.fetchall()]

    async def count_filtered(
        self,
        status: str | None = None,
        module_name: str | None = None,
        q: str | None = None,
    ) -> int:
        where, args = _build_filters(status, module_name, q)
        async with connect() as db:
            cur = await db.execute(
                f"SELECT COUNT(*) FROM failsafe_actions{where}", args
            )
            row = await cur.fetchone()
            return int(row[0]) if row else 0

    async def count_by_status(self) -> dict[str, int]:
        async with connect() as db:
            cur = await db.execute(
                "SELECT status, COUNT(*) FROM failsafe_actions GROUP BY status"
            )
            return {r[0]: int(r[1]) for r in await cur.fetchall()}

    async def get(self, action_id: UUID) -> FailsafeAction | None:
        async with connect() as db:
            cur = await db.execute(
                f"{_SELECT_COLS} WHERE id = ?",
                (str(action_id),),
            )
            row = await cur.fetchone()
            return _row_to_action(row) if row else None

    async def save(self, action: FailsafeAction) -> FailsafeAction:
        # ON CONFLICT cobre tanto reinserção (idempotência) quanto edits via PATCH:
        # description, payload, confidence e status migram quando a row já existe.
        async with connect() as db:
            await db.execute(
                "INSERT INTO failsafe_actions (id, module_name, description, payload, confidence, "
                "status, requested_by, decided_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "  description = excluded.description, "
                "  payload = excluded.payload, "
                "  confidence = excluded.confidence, "
                "  status = excluded.status, "
                "  decided_by = excluded.decided_by",
                (
                    str(action.id),
                    action.module_name,
                    action.description,
                    json.dumps(action.payload),
                    action.confidence,
                    action.status.value,
                    str(action.requested_by) if action.requested_by else None,
                    str(action.decided_by) if action.decided_by else None,
                ),
            )
            await db.commit()
            return action

    async def delete(self, action_id: UUID) -> bool:
        async with connect() as db:
            cur = await db.execute(
                "DELETE FROM failsafe_actions WHERE id = ?",
                (str(action_id),),
            )
            await db.commit()
            return cur.rowcount > 0
