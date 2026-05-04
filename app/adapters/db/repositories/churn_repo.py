"""Repositório SQLite de Churn (taxonomia + classificações)."""

from __future__ import annotations

import json
from uuid import UUID

from app.adapters.db.sqlite import connect
from app.core.domain.entities import ChurnClassification, ChurnNode
from app.core.ports.repositories import ChurnRepository


def _row_to_node(row) -> ChurnNode:
    return ChurnNode(
        id=UUID(row[0]),
        label=row[1],
        parent_id=UUID(row[2]) if row[2] else None,
        depth=row[3] or 0,
        examples=json.loads(row[4]) if row[4] else [],
        occurrences=row[5] or 0,
    )


class SqliteChurnRepository(ChurnRepository):

    async def get_taxonomy(self) -> list[ChurnNode]:
        async with connect() as db:
            cur = await db.execute(
                "SELECT id, label, parent_id, depth, examples, occurrences FROM churn_nodes ORDER BY depth, label"
            )
            return [_row_to_node(r) for r in await cur.fetchall()]

    async def upsert_node(self, node: ChurnNode) -> ChurnNode:
        async with connect() as db:
            await db.execute(
                "INSERT INTO churn_nodes (id, label, parent_id, depth, examples, occurrences) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "  label = excluded.label, "
                "  parent_id = excluded.parent_id, "
                "  depth = excluded.depth, "
                "  examples = excluded.examples, "
                "  occurrences = excluded.occurrences",
                (
                    str(node.id),
                    node.label,
                    str(node.parent_id) if node.parent_id else None,
                    node.depth,
                    json.dumps(node.examples),
                    node.occurrences,
                ),
            )
            await db.commit()
            return node

    async def delete_node(self, node_id: UUID) -> None:
        async with connect() as db:
            await db.execute("DELETE FROM churn_nodes WHERE id = ?", (str(node_id),))
            await db.commit()

    async def save_classification(self, c: ChurnClassification) -> None:
        async with connect() as db:
            await db.execute(
                "INSERT INTO churn_classifications (contract_number, path, confidence, rationale) "
                "VALUES (?, ?, ?, ?)",
                (c.contract_number, json.dumps(c.path), c.confidence, c.rationale),
            )
            await db.commit()

    async def list_classifications(self, limit: int = 100) -> list[ChurnClassification]:
        async with connect() as db:
            cur = await db.execute(
                "SELECT contract_number, path, confidence, rationale, classified_at "
                "FROM churn_classifications ORDER BY classified_at DESC LIMIT ?",
                (limit,),
            )
            from datetime import datetime
            out: list[ChurnClassification] = []
            for r in await cur.fetchall():
                ts = r[4]
                if isinstance(ts, str):
                    try:
                        ts = datetime.fromisoformat(ts)
                    except ValueError:
                        ts = datetime.utcnow()
                out.append(
                    ChurnClassification(
                        contract_number=r[0],
                        path=json.loads(r[1]) if r[1] else [],
                        confidence=r[2] or 0.0,
                        rationale=r[3] or "",
                        classified_at=ts,
                    )
                )
            return out
