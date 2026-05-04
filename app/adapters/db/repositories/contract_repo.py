"""Repositório SQLite de contratos (Radar Voz do Cliente)."""

from __future__ import annotations

import json
from datetime import datetime

from app.adapters.db.sqlite import connect
from app.core.domain.entities import Contract, CustomerSegment
from app.core.ports.repositories import ContractRepository


def _row_to_contract(row) -> Contract:
    contact_at = row[4]
    if isinstance(contact_at, str):
        try:
            contact_at = datetime.fromisoformat(contact_at)
        except ValueError:
            contact_at = datetime.utcnow()
    return Contract(
        contract_number=row[0],
        call_id=row[1] or "",
        contact_id=row[2] or "",
        operator=row[3] or "",
        contact_at=contact_at or datetime.utcnow(),
        segment=CustomerSegment(row[5]) if row[5] in CustomerSegment._value2member_map_ else CustomerSegment.residential,
        transcript=row[6] or "",
        extra=json.loads(row[7]) if row[7] else {},
    )


class SqliteContractRepository(ContractRepository):

    async def list_recent(self, limit: int = 200) -> list[Contract]:
        async with connect() as db:
            cur = await db.execute(
                "SELECT contract_number, call_id, contact_id, operator, contact_at, segment, transcript, extra "
                "FROM contracts ORDER BY contact_at DESC LIMIT ?",
                (limit,),
            )
            return [_row_to_contract(r) for r in await cur.fetchall()]

    async def get(self, contract_number: str) -> Contract | None:
        async with connect() as db:
            cur = await db.execute(
                "SELECT contract_number, call_id, contact_id, operator, contact_at, segment, transcript, extra "
                "FROM contracts WHERE contract_number = ?",
                (contract_number,),
            )
            row = await cur.fetchone()
            return _row_to_contract(row) if row else None

    async def bulk_upsert(self, contracts: list[Contract]) -> int:
        async with connect() as db:
            for c in contracts:
                await db.execute(
                    "INSERT INTO contracts (contract_number, call_id, contact_id, operator, contact_at, "
                    "segment, transcript, extra) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(contract_number) DO UPDATE SET "
                    "  call_id = excluded.call_id, "
                    "  contact_id = excluded.contact_id, "
                    "  operator = excluded.operator, "
                    "  contact_at = excluded.contact_at, "
                    "  segment = excluded.segment, "
                    "  transcript = excluded.transcript, "
                    "  extra = excluded.extra",
                    (
                        c.contract_number,
                        c.call_id,
                        c.contact_id,
                        c.operator,
                        c.contact_at.isoformat() if isinstance(c.contact_at, datetime) else str(c.contact_at),
                        c.segment.value,
                        c.transcript,
                        json.dumps(c.extra),
                    ),
                )
            await db.commit()
            return len(contracts)
