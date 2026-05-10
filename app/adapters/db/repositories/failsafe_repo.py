"""Repositório PostgreSQL de ações de Failsafe (human-in-the-loop)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from app.adapters.db.postgres import connect
from app.core.domain.entities import FailsafeAction, FailsafeStatus
from app.core.ports.repositories import FailsafeRepository


def _row_to_action(row) -> FailsafeAction:
    ts = row["created_at"]
    if not isinstance(ts, datetime):
        ts = datetime.utcnow()
    payload = row["payload"]
    if not isinstance(payload, dict):
        payload = {}
    return FailsafeAction(
        id=UUID(row["id"]),
        module_name=row["module_name"],
        description=row["description"],
        payload=payload,
        confidence=row["confidence"] or 0.0,
        status=FailsafeStatus(row["status"]),
        requested_by=UUID(row["requested_by"]) if row["requested_by"] else None,
        decided_by=UUID(row["decided_by"]) if row["decided_by"] else None,
        created_at=ts,
    )


_SELECT_COLS = (
    "SELECT id::text AS id, module_name, description, payload, confidence, "
    "status, requested_by::text AS requested_by, created_at, "
    "decided_by::text AS decided_by FROM failsafe_actions"
)


def _build_filters(
    status: str | None,
    module_name: str | None,
    q: str | None,
    start_param: int = 1,
) -> tuple[str, list]:
    """Monta cláusula WHERE parametrizada (placeholders $N começando em
    `start_param`) compartilhada por list/count."""
    parts: list[str] = []
    args: list = []
    n = start_param
    if status:
        parts.append(f"status = ${n}"); args.append(status); n += 1
    if module_name:
        parts.append(f"module_name = ${n}"); args.append(module_name); n += 1
    if q:
        parts.append(f"(description ILIKE ${n} OR module_name ILIKE ${n + 1})")
        like = f"%{q}%"
        args.extend([like, like])
        n += 2
    where = (" WHERE " + " AND ".join(parts)) if parts else ""
    return where, args


class PgFailsafeRepository(FailsafeRepository):

    async def list_pending(self) -> list[FailsafeAction]:
        async with connect() as db:
            rows = await db.fetch(
                f"{_SELECT_COLS} WHERE status = 'pending' ORDER BY created_at DESC"
            )
            return [_row_to_action(r) for r in rows]

    async def list_filtered(
        self,
        status: str | None = None,
        module_name: str | None = None,
        q: str | None = None,
        limit: int = 30,
        offset: int = 0,
    ) -> list[FailsafeAction]:
        where, args = _build_filters(status, module_name, q, start_param=1)
        next_n = len(args) + 1
        sql = (
            f"{_SELECT_COLS}{where} ORDER BY created_at DESC "
            f"LIMIT ${next_n} OFFSET ${next_n + 1}"
        )
        async with connect() as db:
            rows = await db.fetch(sql, *args, limit, offset)
            return [_row_to_action(r) for r in rows]

    async def count_filtered(
        self,
        status: str | None = None,
        module_name: str | None = None,
        q: str | None = None,
    ) -> int:
        where, args = _build_filters(status, module_name, q, start_param=1)
        async with connect() as db:
            n = await db.fetchval(
                f"SELECT COUNT(*) FROM failsafe_actions{where}", *args
            )
            return int(n or 0)

    async def count_by_status(self) -> dict[str, int]:
        async with connect() as db:
            rows = await db.fetch(
                "SELECT status, COUNT(*) AS n FROM failsafe_actions GROUP BY status"
            )
            return {r["status"]: int(r["n"]) for r in rows}

    async def get(self, action_id: UUID) -> FailsafeAction | None:
        async with connect() as db:
            row = await db.fetchrow(
                f"{_SELECT_COLS} WHERE id = $1::uuid", str(action_id)
            )
            return _row_to_action(row) if row else None

    async def save(self, action: FailsafeAction) -> FailsafeAction:
        # ON CONFLICT cobre tanto reinserção (idempotência) quanto edits via PATCH.
        async with connect() as db:
            await db.execute(
                """
                INSERT INTO failsafe_actions (id, module_name, description,
                                              payload, confidence, status,
                                              requested_by, decided_by)
                VALUES ($1::uuid, $2, $3, $4::jsonb, $5, $6, $7::uuid, $8::uuid)
                ON CONFLICT (id) DO UPDATE SET
                    description = EXCLUDED.description,
                    payload     = EXCLUDED.payload,
                    confidence  = EXCLUDED.confidence,
                    status      = EXCLUDED.status,
                    decided_by  = EXCLUDED.decided_by
                """,
                str(action.id), action.module_name, action.description,
                action.payload or {}, action.confidence, action.status.value,
                str(action.requested_by) if action.requested_by else None,
                str(action.decided_by) if action.decided_by else None,
            )
            return action

    async def delete(self, action_id: UUID) -> bool:
        async with connect() as db:
            result = await db.execute(
                "DELETE FROM failsafe_actions WHERE id = $1::uuid",
                str(action_id),
            )
            return result.endswith(" 1")
