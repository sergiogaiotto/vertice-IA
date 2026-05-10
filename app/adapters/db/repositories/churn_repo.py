"""Repositório PostgreSQL de Churn (taxonomia + classificações)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from app.adapters.db.postgres import connect
from app.core.domain.entities import ChurnClassification, ChurnNode
from app.core.ports.repositories import ChurnRepository


def _row_to_node(row) -> ChurnNode:
    examples = row["examples"]
    if not isinstance(examples, list):
        examples = []
    return ChurnNode(
        id=UUID(row["id"]),
        label=row["label"],
        parent_id=UUID(row["parent_id"]) if row["parent_id"] else None,
        depth=row["depth"] or 0,
        examples=examples,
        occurrences=row["occurrences"] or 0,
    )


class PgChurnRepository(ChurnRepository):

    async def get_taxonomy(self) -> list[ChurnNode]:
        async with connect() as db:
            rows = await db.fetch(
                "SELECT id::text AS id, label, parent_id::text AS parent_id, "
                "depth, examples, occurrences "
                "FROM churn_nodes ORDER BY depth, label"
            )
            return [_row_to_node(r) for r in rows]

    async def upsert_node(self, node: ChurnNode) -> ChurnNode:
        async with connect() as db:
            await db.execute(
                """
                INSERT INTO churn_nodes (id, label, parent_id, depth, examples,
                                         occurrences)
                VALUES ($1::uuid, $2, $3::uuid, $4, $5::jsonb, $6)
                ON CONFLICT (id) DO UPDATE SET
                    label       = EXCLUDED.label,
                    parent_id   = EXCLUDED.parent_id,
                    depth       = EXCLUDED.depth,
                    examples    = EXCLUDED.examples,
                    occurrences = EXCLUDED.occurrences
                """,
                str(node.id), node.label,
                str(node.parent_id) if node.parent_id else None,
                node.depth, node.examples or [], node.occurrences,
            )
            return node

    async def delete_node(self, node_id: UUID) -> None:
        async with connect() as db:
            await db.execute(
                "DELETE FROM churn_nodes WHERE id = $1::uuid", str(node_id)
            )

    async def save_classification(self, c: ChurnClassification) -> None:
        async with connect() as db:
            await db.execute(
                "INSERT INTO churn_classifications (contract_number, path, "
                "confidence, rationale) VALUES ($1, $2::jsonb, $3, $4)",
                c.contract_number, c.path or [], c.confidence, c.rationale,
            )

    async def list_classifications(self, limit: int = 100) -> list[ChurnClassification]:
        async with connect() as db:
            rows = await db.fetch(
                "SELECT contract_number, path, confidence, rationale, classified_at "
                "FROM churn_classifications ORDER BY classified_at DESC LIMIT $1",
                limit,
            )
            out: list[ChurnClassification] = []
            for r in rows:
                ts = r["classified_at"]
                if not isinstance(ts, datetime):
                    ts = datetime.utcnow()
                path = r["path"] if isinstance(r["path"], list) else []
                out.append(
                    ChurnClassification(
                        contract_number=r["contract_number"],
                        path=path,
                        confidence=r["confidence"] or 0.0,
                        rationale=r["rationale"] or "",
                        classified_at=ts,
                    )
                )
            return out
