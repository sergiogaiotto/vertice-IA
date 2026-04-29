"""Repositório SQLite de Cards de Análise."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from app.adapters.db.sqlite import connect
from app.core.domain.entities import AnalysisCard, OutputType
from app.core.ports.repositories import AnalysisRepository


def _row_to_card(row) -> AnalysisCard:
    return AnalysisCard(
        id=UUID(row[0]),
        contract_number=row[1],
        name=row[2],
        output_type=OutputType(row[3]),
        prompt_text=row[4],
        expected_size=row[5] or "",
        model_used=row[6] or "",
        result=row[7] or "",
        confidence=row[8],
        tokens_input=row[9] or 0,
        tokens_output=row[10] or 0,
        cost_estimated=row[11] or 0.0,
        created_at=datetime.fromisoformat(row[12]) if isinstance(row[12], str) else datetime.utcnow(),
    )


class SqliteAnalysisRepository(AnalysisRepository):

    async def list_for_contract(self, contract_number: str) -> list[AnalysisCard]:
        async with connect() as db:
            cur = await db.execute(
                "SELECT id, contract_number, name, output_type, prompt_text, expected_size, "
                "model_used, result, confidence, tokens_input, tokens_output, cost_estimated, created_at "
                "FROM analysis_cards WHERE contract_number = ? ORDER BY created_at DESC",
                (contract_number,),
            )
            return [_row_to_card(r) for r in await cur.fetchall()]

    async def save(self, card: AnalysisCard) -> AnalysisCard:
        async with connect() as db:
            await db.execute(
                "INSERT INTO analysis_cards (id, contract_number, name, output_type, prompt_text, "
                "expected_size, model_used, result, confidence, tokens_input, tokens_output, cost_estimated) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "  result = excluded.result, "
                "  confidence = excluded.confidence, "
                "  tokens_input = excluded.tokens_input, "
                "  tokens_output = excluded.tokens_output, "
                "  cost_estimated = excluded.cost_estimated",
                (
                    str(card.id),
                    card.contract_number,
                    card.name,
                    card.output_type.value,
                    card.prompt_text,
                    card.expected_size,
                    card.model_used,
                    card.result,
                    card.confidence,
                    card.tokens_input,
                    card.tokens_output,
                    card.cost_estimated,
                ),
            )
            await db.commit()
            return card

    async def delete(self, card_id: UUID) -> None:
        async with connect() as db:
            await db.execute("DELETE FROM analysis_cards WHERE id = ?", (str(card_id),))
            await db.commit()
