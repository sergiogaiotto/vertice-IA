"""Repositório PostgreSQL de contratos (Radar Voz do Cliente)."""

from __future__ import annotations

from datetime import datetime

from app.adapters.db.postgres import connect
from app.core.domain.entities import Contract, CustomerSegment
from app.core.ports.repositories import ContractRepository


def _row_to_contract(row) -> Contract:
    contact_at = row["contact_at"]
    if not isinstance(contact_at, datetime):
        contact_at = datetime.utcnow()
    seg = row["segment"]
    segment = (
        CustomerSegment(seg)
        if seg in CustomerSegment._value2member_map_
        else CustomerSegment.residential
    )
    return Contract(
        contract_number=row["contract_number"],
        call_id=row["call_id"] or "",
        contact_id=row["contact_id"] or "",
        operator=row["operator"] or "",
        contact_at=contact_at,
        segment=segment,
        transcript=row["transcript"] or "",
        extra=row["extra"] or {},
    )


_SELECT = (
    "SELECT contract_number, call_id, contact_id, operator, contact_at, "
    "segment, transcript, extra FROM contracts"
)


class PgContractRepository(ContractRepository):

    async def list_recent(self, limit: int = 200) -> list[Contract]:
        async with connect() as db:
            rows = await db.fetch(
                f"{_SELECT} ORDER BY contact_at DESC NULLS LAST LIMIT $1",
                limit,
            )
            return [_row_to_contract(r) for r in rows]

    async def get(self, contract_number: str) -> Contract | None:
        async with connect() as db:
            row = await db.fetchrow(
                f"{_SELECT} WHERE contract_number = $1", contract_number
            )
            return _row_to_contract(row) if row else None

    async def bulk_upsert(self, contracts: list[Contract]) -> int:
        if not contracts:
            return 0
        async with connect() as db:
            async with db.transaction():
                for c in contracts:
                    await db.execute(
                        """
                        INSERT INTO contracts (contract_number, call_id, contact_id,
                                               operator, contact_at, segment,
                                               transcript, extra)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb)
                        ON CONFLICT (contract_number) DO UPDATE SET
                            call_id    = EXCLUDED.call_id,
                            contact_id = EXCLUDED.contact_id,
                            operator   = EXCLUDED.operator,
                            contact_at = EXCLUDED.contact_at,
                            segment    = EXCLUDED.segment,
                            transcript = EXCLUDED.transcript,
                            extra      = EXCLUDED.extra
                        """,
                        c.contract_number, c.call_id, c.contact_id, c.operator,
                        c.contact_at, c.segment.value, c.transcript,
                        c.extra or {},
                    )
            return len(contracts)
