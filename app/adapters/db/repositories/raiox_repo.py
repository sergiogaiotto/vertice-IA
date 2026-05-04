"""Repositórios SQLite do Raio X Cliente — boards, charts e relacionamentos."""

from __future__ import annotations

import json
from datetime import datetime
from uuid import UUID

from app.adapters.db.sqlite import connect
from app.core.domain.entities import RaioXAnalysis, RaioXBoard, RaioXChart, RaioXRelationship
from app.core.ports.repositories import (
    RaioXAnalysisRepository,
    RaioXBoardRepository,
    RaioXChartRepository,
    RaioXRelationshipRepository,
)


def _ts(value) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return datetime.utcnow()
    return datetime.utcnow()


def _loads(value: str | None, default):
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _row_to_board(row) -> RaioXBoard:
    return RaioXBoard(
        id=UUID(row[0]),
        name=row[1],
        description=row[2] or "",
        owner_id=row[3],
        is_shared=bool(row[4]),
        layout=_loads(row[5], {}),
        filters=_loads(row[6], {}),
        cover_emoji=row[7] or "🩻",
        created_at=_ts(row[8]),
        updated_at=_ts(row[9]),
        allowed_roles=_loads(row[10] if len(row) > 10 else None, []),
        allowed_departments=_loads(row[11] if len(row) > 11 else None, []),
    )


def _row_to_chart(row) -> RaioXChart:
    return RaioXChart(
        id=UUID(row[0]),
        board_id=UUID(row[1]),
        title=row[2] or "",
        chart_type=row[3],
        position_row=int(row[4] or 0),
        position_col=int(row[5] or 0),
        span_cols=int(row[6] or 1),
        span_rows=int(row[7] or 1),
        query_spec=_loads(row[8], {}),
        plotly_config=_loads(row[9], {}),
        created_by_ai=bool(row[10]),
        created_at=_ts(row[11]),
        updated_at=_ts(row[12]),
        skill_path=row[13] if len(row) > 13 and row[13] else "",
    )


def _row_to_rel(row) -> RaioXRelationship:
    return RaioXRelationship(
        id=UUID(row[0]),
        table_a=row[1],
        column_a=row[2],
        table_b=row[3],
        column_b=row[4],
        kind=row[5] or "one_to_many",
        confidence=float(row[6] or 0.0),
        confirmed_by_user=row[7],
        confirmed_at=_ts(row[8]) if row[8] else None,
        created_at=_ts(row[9]),
    )


class SqliteRaioXBoardRepository(RaioXBoardRepository):
    _COLS = (
        "id, name, description, owner_id, is_shared, layout_json, "
        "filters_json, cover_emoji, created_at, updated_at, "
        "allowed_roles, allowed_departments"
    )

    async def list_visible(self, user_id: str | None) -> list[RaioXBoard]:
        # Visibilidade fina (papel/departamento) é aplicada no service —
        # aqui devolvemos shared + owned, e o service refina com user.roles/department.
        async with connect() as db:
            if user_id:
                cur = await db.execute(
                    f"SELECT {self._COLS} FROM raiox_boards "
                    "WHERE is_shared = 1 OR owner_id = ? "
                    "OR (allowed_roles IS NOT NULL AND allowed_roles != '[]') "
                    "OR (allowed_departments IS NOT NULL AND allowed_departments != '[]') "
                    "ORDER BY updated_at DESC",
                    (user_id,),
                )
            else:
                cur = await db.execute(
                    f"SELECT {self._COLS} FROM raiox_boards "
                    "WHERE is_shared = 1 ORDER BY updated_at DESC"
                )
            return [_row_to_board(r) for r in await cur.fetchall()]

    async def get(self, board_id: UUID) -> RaioXBoard | None:
        async with connect() as db:
            cur = await db.execute(
                f"SELECT {self._COLS} FROM raiox_boards WHERE id = ?",
                (str(board_id),),
            )
            row = await cur.fetchone()
            return _row_to_board(row) if row else None

    async def save(self, board: RaioXBoard) -> RaioXBoard:
        async with connect() as db:
            await db.execute(
                "INSERT INTO raiox_boards "
                "(id, name, description, owner_id, is_shared, layout_json, filters_json, "
                " cover_emoji, allowed_roles, allowed_departments) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "  name = excluded.name, "
                "  description = excluded.description, "
                "  is_shared = excluded.is_shared, "
                "  layout_json = excluded.layout_json, "
                "  filters_json = excluded.filters_json, "
                "  cover_emoji = excluded.cover_emoji, "
                "  allowed_roles = excluded.allowed_roles, "
                "  allowed_departments = excluded.allowed_departments, "
                "  updated_at = CURRENT_TIMESTAMP",
                (
                    str(board.id),
                    board.name,
                    board.description,
                    board.owner_id,
                    1 if board.is_shared else 0,
                    json.dumps(board.layout, ensure_ascii=False),
                    json.dumps(board.filters, ensure_ascii=False),
                    board.cover_emoji,
                    json.dumps(board.allowed_roles, ensure_ascii=False),
                    json.dumps(board.allowed_departments, ensure_ascii=False),
                ),
            )
            await db.commit()
            return board

    async def delete(self, board_id: UUID) -> bool:
        async with connect() as db:
            cur = await db.execute("DELETE FROM raiox_boards WHERE id = ?", (str(board_id),))
            await db.commit()
            return cur.rowcount > 0


class SqliteRaioXChartRepository(RaioXChartRepository):
    _COLS = (
        "id, board_id, title, chart_type, position_row, position_col, "
        "span_cols, span_rows, query_spec_json, plotly_config_json, "
        "created_by_ai, created_at, updated_at, skill_path"
    )

    async def list_for_board(self, board_id: UUID) -> list[RaioXChart]:
        async with connect() as db:
            cur = await db.execute(
                f"SELECT {self._COLS} FROM raiox_charts WHERE board_id = ? "
                "ORDER BY position_row, position_col",
                (str(board_id),),
            )
            return [_row_to_chart(r) for r in await cur.fetchall()]

    async def get(self, chart_id: UUID) -> RaioXChart | None:
        async with connect() as db:
            cur = await db.execute(
                f"SELECT {self._COLS} FROM raiox_charts WHERE id = ?",
                (str(chart_id),),
            )
            row = await cur.fetchone()
            return _row_to_chart(row) if row else None

    async def save(self, chart: RaioXChart) -> RaioXChart:
        async with connect() as db:
            await db.execute(
                "INSERT INTO raiox_charts "
                "(id, board_id, title, chart_type, position_row, position_col, "
                " span_cols, span_rows, query_spec_json, plotly_config_json, "
                " created_by_ai, skill_path) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "  title = excluded.title, "
                "  chart_type = excluded.chart_type, "
                "  position_row = excluded.position_row, "
                "  position_col = excluded.position_col, "
                "  span_cols = excluded.span_cols, "
                "  span_rows = excluded.span_rows, "
                "  query_spec_json = excluded.query_spec_json, "
                "  plotly_config_json = excluded.plotly_config_json, "
                "  skill_path = excluded.skill_path, "
                "  updated_at = CURRENT_TIMESTAMP",
                (
                    str(chart.id),
                    str(chart.board_id),
                    chart.title,
                    chart.chart_type,
                    chart.position_row,
                    chart.position_col,
                    chart.span_cols,
                    chart.span_rows,
                    json.dumps(chart.query_spec, ensure_ascii=False),
                    json.dumps(chart.plotly_config, ensure_ascii=False),
                    1 if chart.created_by_ai else 0,
                    chart.skill_path or None,
                ),
            )
            await db.commit()
            return chart

    async def delete(self, chart_id: UUID) -> bool:
        async with connect() as db:
            cur = await db.execute("DELETE FROM raiox_charts WHERE id = ?", (str(chart_id),))
            await db.commit()
            return cur.rowcount > 0


def _row_to_analysis(row) -> RaioXAnalysis:
    return RaioXAnalysis(
        id=UUID(row[0]),
        board_id=UUID(row[1]),
        user_id=row[2],
        username=row[3] or "",
        charts_snapshot=_loads(row[4], []),
        per_chart=_loads(row[5], []),
        synthesis=_loads(row[6], {}),
        totals=_loads(row[7], {}),
        created_at=_ts(row[8]),
    )


class SqliteRaioXAnalysisRepository(RaioXAnalysisRepository):
    _COLS = (
        "id, board_id, user_id, username, charts_snapshot, "
        "per_chart_json, synthesis_json, totals_json, created_at"
    )

    async def list_for_board(self, board_id: UUID, limit: int = 50) -> list[RaioXAnalysis]:
        async with connect() as db:
            cur = await db.execute(
                f"SELECT {self._COLS} FROM raiox_analyses "
                "WHERE board_id = ? ORDER BY created_at DESC LIMIT ?",
                (str(board_id), limit),
            )
            return [_row_to_analysis(r) for r in await cur.fetchall()]

    async def get(self, analysis_id: UUID) -> RaioXAnalysis | None:
        async with connect() as db:
            cur = await db.execute(
                f"SELECT {self._COLS} FROM raiox_analyses WHERE id = ?",
                (str(analysis_id),),
            )
            row = await cur.fetchone()
            return _row_to_analysis(row) if row else None

    async def save(self, a: RaioXAnalysis) -> RaioXAnalysis:
        async with connect() as db:
            await db.execute(
                "INSERT INTO raiox_analyses "
                "(id, board_id, user_id, username, charts_snapshot, "
                " per_chart_json, synthesis_json, totals_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(a.id),
                    str(a.board_id),
                    a.user_id,
                    a.username,
                    json.dumps(a.charts_snapshot, ensure_ascii=False),
                    json.dumps(a.per_chart, ensure_ascii=False),
                    json.dumps(a.synthesis, ensure_ascii=False),
                    json.dumps(a.totals, ensure_ascii=False),
                ),
            )
            await db.commit()
            return a

    async def delete(self, analysis_id: UUID) -> bool:
        async with connect() as db:
            cur = await db.execute(
                "DELETE FROM raiox_analyses WHERE id = ?", (str(analysis_id),)
            )
            await db.commit()
            return cur.rowcount > 0


class SqliteRaioXRelationshipRepository(RaioXRelationshipRepository):

    _COLS = (
        "id, table_a, column_a, table_b, column_b, kind, confidence, "
        "confirmed_by_user, confirmed_at, created_at"
    )

    async def list_all(self) -> list[RaioXRelationship]:
        async with connect() as db:
            cur = await db.execute(
                f"SELECT {self._COLS} FROM raiox_relationships ORDER BY confidence DESC"
            )
            return [_row_to_rel(r) for r in await cur.fetchall()]

    async def list_for_table(self, table: str) -> list[RaioXRelationship]:
        async with connect() as db:
            cur = await db.execute(
                f"SELECT {self._COLS} FROM raiox_relationships "
                "WHERE table_a = ? OR table_b = ? ORDER BY confidence DESC",
                (table, table),
            )
            return [_row_to_rel(r) for r in await cur.fetchall()]

    async def save(self, rel: RaioXRelationship) -> RaioXRelationship:
        async with connect() as db:
            await db.execute(
                "INSERT INTO raiox_relationships "
                "(id, table_a, column_a, table_b, column_b, kind, confidence, "
                " confirmed_by_user, confirmed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(table_a, column_a, table_b, column_b) DO UPDATE SET "
                "  kind = excluded.kind, "
                "  confidence = excluded.confidence, "
                "  confirmed_by_user = excluded.confirmed_by_user, "
                "  confirmed_at = excluded.confirmed_at",
                (
                    str(rel.id),
                    rel.table_a,
                    rel.column_a,
                    rel.table_b,
                    rel.column_b,
                    rel.kind,
                    rel.confidence,
                    rel.confirmed_by_user,
                    rel.confirmed_at.isoformat() if rel.confirmed_at else None,
                ),
            )
            await db.commit()
            return rel

    async def delete(self, rel_id: UUID) -> bool:
        async with connect() as db:
            cur = await db.execute(
                "DELETE FROM raiox_relationships WHERE id = ?", (str(rel_id),)
            )
            await db.commit()
            return cur.rowcount > 0

    async def confirm(self, rel_id: UUID, username: str) -> bool:
        async with connect() as db:
            cur = await db.execute(
                "UPDATE raiox_relationships "
                "SET confirmed_by_user = ?, confirmed_at = CURRENT_TIMESTAMP "
                "WHERE id = ?",
                (username, str(rel_id)),
            )
            await db.commit()
            return cur.rowcount > 0
