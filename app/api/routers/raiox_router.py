"""Router HTTP do Raio X Cliente."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import get_raiox_service, get_schema_service, require_user
from app.api.schemas.raiox import (
    BoardCreate,
    BoardOut,
    BoardUpdate,
    ChartIn,
    ChartOut,
    ChartUpdate,
    QuerySpec,
    RelationshipIn,
    RelationshipOut,
    SeriesOut,
)
from app.core.domain.entities import RaioXBoard, RaioXChart, RaioXRelationship, User
from app.core.services.raiox_service import RaioXService, SUPPORTED_CHART_TYPES

router = APIRouter()


# Roles autorizados a criar/editar (analista_n3 lê e interage)
_EDIT_ROLES = {"admin", "supervisor"}


def _can_edit(user: User) -> bool:
    return any(r in _EDIT_ROLES for r in (user.roles or []))


def _require_edit(user: User) -> None:
    if not _can_edit(user):
        raise HTTPException(403, "ação restrita a admin/supervisor")


def _board_to_out(b: RaioXBoard) -> BoardOut:
    return BoardOut(
        id=b.id,
        name=b.name,
        description=b.description,
        owner_id=b.owner_id,
        is_shared=b.is_shared,
        layout=b.layout,
        filters=b.filters,
        cover_emoji=b.cover_emoji,
        created_at=b.created_at,
        updated_at=b.updated_at,
    )


def _chart_to_out(c: RaioXChart) -> ChartOut:
    return ChartOut(
        id=c.id,
        board_id=c.board_id,
        chart_type=c.chart_type,
        title=c.title,
        position_row=c.position_row,
        position_col=c.position_col,
        span_cols=c.span_cols,
        span_rows=c.span_rows,
        query_spec=c.query_spec,
        plotly_config=c.plotly_config,
        created_by_ai=c.created_by_ai,
        created_at=c.created_at,
        updated_at=c.updated_at,
    )


def _rel_to_out(r: RaioXRelationship) -> RelationshipOut:
    return RelationshipOut(
        id=r.id,
        table_a=r.table_a,
        column_a=r.column_a,
        table_b=r.table_b,
        column_b=r.column_b,
        kind=r.kind,
        confidence=r.confidence,
        confirmed_by_user=r.confirmed_by_user,
        confirmed_at=r.confirmed_at,
    )


# ============================================================
# Boards
# ============================================================

@router.get("/boards", response_model=list[BoardOut])
async def list_boards(
    svc: RaioXService = Depends(get_raiox_service),
    user: User = Depends(require_user),
):
    boards = await svc.list_boards(user_id=str(user.id) if user.id else None)
    return [_board_to_out(b) for b in boards]


@router.post("/boards", response_model=BoardOut, status_code=201)
async def create_board(
    body: BoardCreate,
    svc: RaioXService = Depends(get_raiox_service),
    user: User = Depends(require_user),
):
    _require_edit(user)
    board = await svc.create_board(
        name=body.name,
        owner_id=str(user.id) if user.id else None,
        description=body.description,
        is_shared=body.is_shared,
        cover_emoji=body.cover_emoji,
    )
    return _board_to_out(board)


@router.get("/boards/{board_id}", response_model=BoardOut)
async def get_board(
    board_id: UUID,
    svc: RaioXService = Depends(get_raiox_service),
    user: User = Depends(require_user),
):
    board = await svc.get_board(board_id)
    if not board:
        raise HTTPException(404, "board não encontrado")
    return _board_to_out(board)


@router.patch("/boards/{board_id}", response_model=BoardOut)
async def update_board(
    board_id: UUID,
    body: BoardUpdate,
    svc: RaioXService = Depends(get_raiox_service),
    user: User = Depends(require_user),
):
    _require_edit(user)
    board = await svc.update_board(
        board_id,
        name=body.name,
        description=body.description,
        layout=body.layout,
        filters=body.filters,
        is_shared=body.is_shared,
    )
    if not board:
        raise HTTPException(404, "board não encontrado")
    return _board_to_out(board)


@router.delete("/boards/{board_id}", status_code=204)
async def delete_board(
    board_id: UUID,
    svc: RaioXService = Depends(get_raiox_service),
    user: User = Depends(require_user),
):
    _require_edit(user)
    ok = await svc.delete_board(board_id)
    if not ok:
        raise HTTPException(404, "board não encontrado")


# ============================================================
# Charts
# ============================================================

@router.get("/boards/{board_id}/charts", response_model=list[ChartOut])
async def list_charts(
    board_id: UUID,
    svc: RaioXService = Depends(get_raiox_service),
    user: User = Depends(require_user),
):
    if not await svc.get_board(board_id):
        raise HTTPException(404, "board não encontrado")
    charts = await svc.list_charts(board_id)
    return [_chart_to_out(c) for c in charts]


@router.post("/boards/{board_id}/charts", response_model=ChartOut, status_code=201)
async def add_chart(
    board_id: UUID,
    body: ChartIn,
    svc: RaioXService = Depends(get_raiox_service),
    user: User = Depends(require_user),
):
    _require_edit(user)
    if not await svc.get_board(board_id):
        raise HTTPException(404, "board não encontrado")
    try:
        chart = await svc.add_chart(
            board_id=board_id,
            chart_type=body.chart_type,
            query_spec=body.query_spec.model_dump(),
            title=body.title,
            position_row=body.position_row,
            position_col=body.position_col,
            span_cols=body.span_cols,
            span_rows=body.span_rows,
            plotly_config=body.plotly_config,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _chart_to_out(chart)


@router.patch("/charts/{chart_id}", response_model=ChartOut)
async def update_chart(
    chart_id: UUID,
    body: ChartUpdate,
    svc: RaioXService = Depends(get_raiox_service),
    user: User = Depends(require_user),
):
    _require_edit(user)
    fields: dict = body.model_dump(exclude_unset=True)
    if "query_spec" in fields and fields["query_spec"] is not None:
        fields["query_spec"] = body.query_spec.model_dump()
    try:
        chart = await svc.update_chart(chart_id, **fields)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not chart:
        raise HTTPException(404, "chart não encontrado")
    return _chart_to_out(chart)


@router.delete("/charts/{chart_id}", status_code=204)
async def delete_chart(
    chart_id: UUID,
    svc: RaioXService = Depends(get_raiox_service),
    user: User = Depends(require_user),
):
    _require_edit(user)
    ok = await svc.delete_chart(chart_id)
    if not ok:
        raise HTTPException(404, "chart não encontrado")


# ============================================================
# Query (séries) — alimenta o renderizador Plotly do cliente
# ============================================================

@router.post("/query", response_model=SeriesOut)
async def query_series(
    body: QuerySpec,
    svc: RaioXService = Depends(get_raiox_service),
    user: User = Depends(require_user),
):
    try:
        result = await svc.build_series(body.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e))
    return SeriesOut(**result)


# ============================================================
# Schema introspection (proxy para SchemaService)
# ============================================================

@router.get("/tables")
async def list_tables(
    schema=Depends(get_schema_service),
    user: User = Depends(require_user),
):
    """Lista tabelas das Funcionalidades (radar, churn, ...) + dinâmicas geradas
    por Módulos. Tabelas de aplicação (admin/usuários/finops/etc) ficam fora —
    o Raio X opera apenas sobre dados de Funcionalidade."""
    all_tables = await schema.list_tables(feature=None)
    return [
        t for t in all_tables
        if t.get("is_dynamic") or any(f != "admin" for f in t.get("features", []))
    ]


# ============================================================
# Relationships
# ============================================================

@router.get("/relationships", response_model=list[RelationshipOut])
async def list_relationships(
    svc: RaioXService = Depends(get_raiox_service),
    user: User = Depends(require_user),
):
    rels = await svc.list_relationships()
    return [_rel_to_out(r) for r in rels]


@router.get("/relationships/suggestions", response_model=list[RelationshipOut])
async def suggest_relationships(
    svc: RaioXService = Depends(get_raiox_service),
    user: User = Depends(require_user),
):
    """Heurística que sugere relacionamentos prováveis (não persiste)."""
    rels = await svc.detect_relationships(only_unconfirmed=True)
    return [_rel_to_out(r) for r in rels]


@router.post("/relationships", response_model=RelationshipOut, status_code=201)
async def create_relationship(
    body: RelationshipIn,
    svc: RaioXService = Depends(get_raiox_service),
    user: User = Depends(require_user),
):
    _require_edit(user)
    from app.core.domain.entities import new_uuid
    rel = RaioXRelationship(
        id=new_uuid(),
        table_a=body.table_a,
        column_a=body.column_a,
        table_b=body.table_b,
        column_b=body.column_b,
        kind=body.kind,
        confidence=1.0,
        confirmed_by_user=user.username,
    )
    saved = await svc.save_relationship(rel)
    return _rel_to_out(saved)


@router.post("/relationships/{rel_id}/confirm", response_model=RelationshipOut)
async def confirm_relationship(
    rel_id: UUID,
    svc: RaioXService = Depends(get_raiox_service),
    user: User = Depends(require_user),
):
    _require_edit(user)
    ok = await svc.confirm_relationship(rel_id, user.username)
    if not ok:
        raise HTTPException(404, "relationship não encontrado")
    rels = await svc.list_relationships()
    rel = next((r for r in rels if r.id == rel_id), None)
    if not rel:
        raise HTTPException(404, "relationship não encontrado após confirmar")
    return _rel_to_out(rel)


@router.delete("/relationships/{rel_id}", status_code=204)
async def delete_relationship(
    rel_id: UUID,
    svc: RaioXService = Depends(get_raiox_service),
    user: User = Depends(require_user),
):
    _require_edit(user)
    ok = await svc.delete_relationship(rel_id)
    if not ok:
        raise HTTPException(404, "relationship não encontrado")


# ============================================================
# Capabilities (chart types disponíveis nesta fase)
# ============================================================

@router.get("/capabilities")
async def capabilities(user: User = Depends(require_user)):
    return {
        "phase": 1,
        "supported_chart_types": sorted(SUPPORTED_CHART_TYPES),
        "max_grid_cols": 3,
        "max_grid_rows": 10,
        "can_edit": _can_edit(user),
        "supports_joins": True,
        "supports_crossfilter": True,
    }
