"""Repositório PostgreSQL de Cards de Análise."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from app.adapters.db.postgres import connect
from app.core.domain.entities import AnalysisCard, OutputType
from app.core.ports.repositories import AnalysisRepository


def _row_to_card(row) -> AnalysisCard:
    created_at = row["created_at"]
    if not isinstance(created_at, datetime):
        created_at = datetime.utcnow()
    return AnalysisCard(
        id=UUID(row["id"]),
        contract_number=row["contract_number"],
        name=row["name"],
        output_type=OutputType(row["output_type"]),
        prompt_text=row["prompt_text"],
        expected_size=row["expected_size"] or "",
        model_used=row["model_used"] or "",
        result=row["result"] or "",
        confidence=row["confidence"],
        tokens_input=row["tokens_input"] or 0,
        tokens_output=row["tokens_output"] or 0,
        cost_estimated=row["cost_estimated"] or 0.0,
        created_at=created_at,
    )


_SELECT = (
    "SELECT id::text AS id, contract_number, name, output_type, prompt_text, "
    "expected_size, model_used, result, confidence, tokens_input, "
    "tokens_output, cost_estimated, created_at FROM analysis_cards"
)


class PgAnalysisRepository(AnalysisRepository):

    async def list_for_contract(self, contract_number: str) -> list[AnalysisCard]:
        async with connect() as db:
            rows = await db.fetch(
                f"{_SELECT} WHERE contract_number = $1 ORDER BY created_at DESC",
                contract_number,
            )
            return [_row_to_card(r) for r in rows]

    async def save(self, card: AnalysisCard) -> AnalysisCard:
        async with connect() as db:
            await db.execute(
                """
                INSERT INTO analysis_cards (id, contract_number, name, output_type,
                                            prompt_text, expected_size, model_used,
                                            result, confidence, tokens_input,
                                            tokens_output, cost_estimated)
                VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                ON CONFLICT (id) DO UPDATE SET
                    result         = EXCLUDED.result,
                    confidence     = EXCLUDED.confidence,
                    tokens_input   = EXCLUDED.tokens_input,
                    tokens_output  = EXCLUDED.tokens_output,
                    cost_estimated = EXCLUDED.cost_estimated
                """,
                str(card.id), card.contract_number, card.name,
                card.output_type.value, card.prompt_text, card.expected_size,
                card.model_used, card.result, card.confidence,
                card.tokens_input, card.tokens_output, card.cost_estimated,
            )
            return card

    async def delete(self, card_id: UUID) -> None:
        async with connect() as db:
            await db.execute(
                "DELETE FROM analysis_cards WHERE id = $1::uuid", str(card_id)
            )
