"""Repositórios PostgreSQL do Raio X Cliente — boards, charts e relacionamentos."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from app.adapters.db.postgres import connect
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
    return datetime.utcnow()


def _list_or_default(value, default):
    if isinstance(value, list):
        return value
    return default


def _dict_or_default(value, default):
    if isinstance(value, dict):
        return value
    return default


def _row_to_board(row) -> RaioXBoard:
    return RaioXBoard(
        id=UUID(row["id"]),
        name=row["name"],
        description=row["description"] or "",
        owner_id=row["owner_id"],
        is_shared=bool(row["is_shared"]),
        layout=_dict_or_default(row["layout_json"], {}),
        filters=_dict_or_default(row["filters_json"], {}),
        cover_emoji=row["cover_emoji"] or "🩻",
        created_at=_ts(row["created_at"]),
        updated_at=_ts(row["updated_at"]),
        allowed_roles=_list_or_default(row["allowed_roles"], []),
        allowed_departments=_list_or_default(row["allowed_departments"], []),
    )


def _row_to_chart(row) -> RaioXChart:
    return RaioXChart(
        id=UUID(row["id"]),
        board_id=UUID(row["board_id"]),
        title=row["title"] or "",
        chart_type=row["chart_type"],
        position_row=int(row["position_row"] or 0),
        position_col=int(row["position_col"] or 0),
        span_cols=int(row["span_cols"] or 1),
        span_rows=int(row["span_rows"] or 1),
        query_spec=_dict_or_default(row["query_spec_json"], {}),
        plotly_config=_dict_or_default(row["plotly_config_json"], {}),
        created_by_ai=bool(row["created_by_ai"]),
        created_at=_ts(row["created_at"]),
        updated_at=_ts(row["updated_at"]),
        skill_path=row["skill_path"] or "",
    )


def _row_to_rel(row) -> RaioXRelationship:
    return RaioXRelationship(
        id=UUID(row["id"]),
        table_a=row["table_a"],
        column_a=row["column_a"],
        table_b=row["table_b"],
        column_b=row["column_b"],
        kind=row["kind"] or "one_to_many",
        confidence=float(row["confidence"] or 0.0),
        confirmed_by_user=row["confirmed_by_user"],
        confirmed_at=_ts(row["confirmed_at"]) if row["confirmed_at"] else None,
        created_at=_ts(row["created_at"]),
    )


class PgRaioXBoardRepository(RaioXBoardRepository):
    _COLS = (
        "id::text AS id, name, description, owner_id::text AS owner_id, "
        "is_shared, layout_json, filters_json, cover_emoji, "
        "created_at, updated_at, allowed_roles, allowed_departments"
    )

    async def list_visible(self, user_id: str | None) -> list[RaioXBoard]:
        # Visibilidade fina é refinada no service. Aqui devolvemos shared,
        # owned, e qualquer board com restrições por papel/dept (que o
        # service vai cruzar com user.roles/department).
        async with connect() as db:
            if user_id:
                rows = await db.fetch(
                    f"SELECT {self._COLS} FROM raiox_boards "
                    "WHERE is_shared = TRUE OR owner_id = $1::uuid "
                    "   OR (allowed_roles IS NOT NULL "
                    "       AND jsonb_array_length(allowed_roles) > 0) "
                    "   OR (allowed_departments IS NOT NULL "
                    "       AND jsonb_array_length(allowed_departments) > 0) "
                    "ORDER BY updated_at DESC",
                    user_id,
                )
            else:
                rows = await db.fetch(
                    f"SELECT {self._COLS} FROM raiox_boards "
                    "WHERE is_shared = TRUE ORDER BY updated_at DESC"
                )
            return [_row_to_board(r) for r in rows]

    async def get(self, board_id: UUID) -> RaioXBoard | None:
        async with connect() as db:
            row = await db.fetchrow(
                f"SELECT {self._COLS} FROM raiox_boards WHERE id = $1::uuid",
                str(board_id),
            )
            return _row_to_board(row) if row else None

    async def save(self, board: RaioXBoard) -> RaioXBoard:
        async with connect() as db:
            await db.execute(
                """
                INSERT INTO raiox_boards
                  (id, name, description, owner_id, is_shared, layout_json,
                   filters_json, cover_emoji, allowed_roles, allowed_departments)
                VALUES ($1::uuid, $2, $3, $4::uuid, $5, $6::jsonb, $7::jsonb,
                        $8, $9::jsonb, $10::jsonb)
                ON CONFLICT (id) DO UPDATE SET
                    name                = EXCLUDED.name,
                    description         = EXCLUDED.description,
                    is_shared           = EXCLUDED.is_shared,
                    layout_json         = EXCLUDED.layout_json,
                    filters_json        = EXCLUDED.filters_json,
                    cover_emoji         = EXCLUDED.cover_emoji,
                    allowed_roles       = EXCLUDED.allowed_roles,
                    allowed_departments = EXCLUDED.allowed_departments,
                    updated_at          = NOW()
                """,
                str(board.id), board.name, board.description, board.owner_id,
                board.is_shared, board.layout, board.filters, board.cover_emoji,
                board.allowed_roles or [], board.allowed_departments or [],
            )
            return board

    async def delete(self, board_id: UUID) -> bool:
        async with connect() as db:
            result = await db.execute(
                "DELETE FROM raiox_boards WHERE id = $1::uuid", str(board_id)
            )
            return result.endswith(" 1")


class PgRaioXChartRepository(RaioXChartRepository):
    _COLS = (
        "id::text AS id, board_id::text AS board_id, title, chart_type, "
        "position_row, position_col, span_cols, span_rows, query_spec_json, "
        "plotly_config_json, created_by_ai, created_at, updated_at, skill_path"
    )

    async def list_for_board(self, board_id: UUID) -> list[RaioXChart]:
        async with connect() as db:
            rows = await db.fetch(
                f"SELECT {self._COLS} FROM raiox_charts WHERE board_id = $1::uuid "
                "ORDER BY position_row, position_col",
                str(board_id),
            )
            return [_row_to_chart(r) for r in rows]

    async def get(self, chart_id: UUID) -> RaioXChart | None:
        async with connect() as db:
            row = await db.fetchrow(
                f"SELECT {self._COLS} FROM raiox_charts WHERE id = $1::uuid",
                str(chart_id),
            )
            return _row_to_chart(row) if row else None

    async def save(self, chart: RaioXChart) -> RaioXChart:
        async with connect() as db:
            await db.execute(
                """
                INSERT INTO raiox_charts
                  (id, board_id, title, chart_type, position_row, position_col,
                   span_cols, span_rows, query_spec_json, plotly_config_json,
                   created_by_ai, skill_path)
                VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7, $8, $9::jsonb,
                        $10::jsonb, $11, $12)
                ON CONFLICT (id) DO UPDATE SET
                    title              = EXCLUDED.title,
                    chart_type         = EXCLUDED.chart_type,
                    position_row       = EXCLUDED.position_row,
                    position_col       = EXCLUDED.position_col,
                    span_cols          = EXCLUDED.span_cols,
                    span_rows          = EXCLUDED.span_rows,
                    query_spec_json    = EXCLUDED.query_spec_json,
                    plotly_config_json = EXCLUDED.plotly_config_json,
                    skill_path         = EXCLUDED.skill_path,
                    updated_at         = NOW()
                """,
                str(chart.id), str(chart.board_id), chart.title, chart.chart_type,
                chart.position_row, chart.position_col, chart.span_cols,
                chart.span_rows, chart.query_spec or {}, chart.plotly_config or {},
                chart.created_by_ai, chart.skill_path or None,
            )
            return chart

    async def delete(self, chart_id: UUID) -> bool:
        async with connect() as db:
            result = await db.execute(
                "DELETE FROM raiox_charts WHERE id = $1::uuid", str(chart_id)
            )
            return result.endswith(" 1")


def _row_to_analysis(row) -> RaioXAnalysis:
    return RaioXAnalysis(
        id=UUID(row["id"]),
        board_id=UUID(row["board_id"]),
        user_id=row["user_id"],
        username=row["username"] or "",
        charts_snapshot=_list_or_default(row["charts_snapshot"], []),
        per_chart=_list_or_default(row["per_chart_json"], []),
        synthesis=_dict_or_default(row["synthesis_json"], {}),
        totals=_dict_or_default(row["totals_json"], {}),
        created_at=_ts(row["created_at"]),
    )


class PgRaioXAnalysisRepository(RaioXAnalysisRepository):
    _COLS = (
        "id::text AS id, board_id::text AS board_id, user_id::text AS user_id, "
        "username, charts_snapshot, per_chart_json, synthesis_json, totals_json, "
        "created_at"
    )

    async def list_for_board(self, board_id: UUID, limit: int = 50) -> list[RaioXAnalysis]:
        async with connect() as db:
            rows = await db.fetch(
                f"SELECT {self._COLS} FROM raiox_analyses "
                "WHERE board_id = $1::uuid "
                "ORDER BY created_at DESC LIMIT $2",
                str(board_id), limit,
            )
            return [_row_to_analysis(r) for r in rows]

    async def get(self, analysis_id: UUID) -> RaioXAnalysis | None:
        async with connect() as db:
            row = await db.fetchrow(
                f"SELECT {self._COLS} FROM raiox_analyses WHERE id = $1::uuid",
                str(analysis_id),
            )
            return _row_to_analysis(row) if row else None

    async def save(self, a: RaioXAnalysis) -> RaioXAnalysis:
        async with connect() as db:
            await db.execute(
                """
                INSERT INTO raiox_analyses
                  (id, board_id, user_id, username, charts_snapshot,
                   per_chart_json, synthesis_json, totals_json)
                VALUES ($1::uuid, $2::uuid, $3::uuid, $4,
                        $5::jsonb, $6::jsonb, $7::jsonb, $8::jsonb)
                """,
                str(a.id), str(a.board_id),
                a.user_id, a.username,
                a.charts_snapshot or [], a.per_chart or [],
                a.synthesis or {}, a.totals or {},
            )
            return a

    async def delete(self, analysis_id: UUID) -> bool:
        async with connect() as db:
            result = await db.execute(
                "DELETE FROM raiox_analyses WHERE id = $1::uuid",
                str(analysis_id),
            )
            return result.endswith(" 1")


class PgRaioXRelationshipRepository(RaioXRelationshipRepository):

    _COLS = (
        "id::text AS id, table_a, column_a, table_b, column_b, kind, "
        "confidence, confirmed_by_user, confirmed_at, created_at"
    )

    async def list_all(self) -> list[RaioXRelationship]:
        async with connect() as db:
            rows = await db.fetch(
                f"SELECT {self._COLS} FROM raiox_relationships "
                "ORDER BY confidence DESC"
            )
            return [_row_to_rel(r) for r in rows]

    async def list_for_table(self, table: str) -> list[RaioXRelationship]:
        async with connect() as db:
            rows = await db.fetch(
                f"SELECT {self._COLS} FROM raiox_relationships "
                "WHERE table_a = $1 OR table_b = $1 "
                "ORDER BY confidence DESC",
                table,
            )
            return [_row_to_rel(r) for r in rows]

    async def save(self, rel: RaioXRelationship) -> RaioXRelationship:
        async with connect() as db:
            await db.execute(
                """
                INSERT INTO raiox_relationships
                  (id, table_a, column_a, table_b, column_b, kind, confidence,
                   confirmed_by_user, confirmed_at)
                VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8, $9)
                ON CONFLICT (table_a, column_a, table_b, column_b) DO UPDATE SET
                    kind              = EXCLUDED.kind,
                    confidence        = EXCLUDED.confidence,
                    confirmed_by_user = EXCLUDED.confirmed_by_user,
                    confirmed_at      = EXCLUDED.confirmed_at
                """,
                str(rel.id), rel.table_a, rel.column_a, rel.table_b, rel.column_b,
                rel.kind, rel.confidence, rel.confirmed_by_user, rel.confirmed_at,
            )
            return rel

    async def delete(self, rel_id: UUID) -> bool:
        async with connect() as db:
            result = await db.execute(
                "DELETE FROM raiox_relationships WHERE id = $1::uuid",
                str(rel_id),
            )
            return result.endswith(" 1")

    async def confirm(self, rel_id: UUID, username: str) -> bool:
        async with connect() as db:
            result = await db.execute(
                "UPDATE raiox_relationships "
                "SET confirmed_by_user = $1, confirmed_at = NOW() "
                "WHERE id = $2::uuid",
                username, str(rel_id),
            )
            return result.endswith(" 1")
