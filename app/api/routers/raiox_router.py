"""Router HTTP do Raio X Cliente."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import (
    get_finops_service,
    get_raiox_analysis_repo,
    get_raiox_service,
    get_router_clients,
    get_schema_service,
    require_user,
)
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
        allowed_roles=b.allowed_roles,
        allowed_departments=b.allowed_departments,
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
        skill_path=c.skill_path,
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
    user_dept = getattr(user, "department", None) or None
    boards = await svc.list_boards(
        user_id=str(user.id) if user.id else None,
        user_roles=user.roles or [],
        user_department=user_dept,
    )
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
        allowed_roles=body.allowed_roles,
        allowed_departments=body.allowed_departments,
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
        allowed_roles=body.allowed_roles,
        allowed_departments=body.allowed_departments,
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
            skill_path=body.skill_path,
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

# ============================================================
# Copiloto — recomendação de chart e detecção de tipos
# ============================================================

class CopilotRecommendIn(BaseModel):
    table: str
    label_column: str | None = None
    value_column: str | None = None
    intent_hint: str | None = None  # texto livre (ex: "evolução no tempo")


@router.post("/copilot/recommend")
async def copilot_recommend(
    body: CopilotRecommendIn,
    schema=Depends(get_schema_service),
    user: User = Depends(require_user),
):
    """Heurística determinística: dado a tabela e um label/value parcial,
    devolve sugestão de chart_type + agregação + colunas faltantes + rationale.

    Regras (ordem):
      1) Se há coluna timestamp + numérica → line (séries temporais)
      2) Se label tem alta cardinalidade (>20 distintos) → treemap
      3) Se label tem cardinalidade baixa (≤8) → pie/donut
      4) Se há 2 numéricas → scatter
      5) Default → bar
    """
    tables = await schema.list_tables(feature=None)
    table_meta = next((t for t in tables if t["name"] == body.table), None)
    if not table_meta:
        raise HTTPException(404, f"tabela '{body.table}' não encontrada")

    cols = table_meta["columns"]
    by_name = {c["name"]: c for c in cols}

    def _is_numeric(c) -> bool:
        t = (c.get("type") or "").upper()
        return any(x in t for x in ("INT", "REAL", "FLOAT", "NUMERIC", "DECIMAL"))

    def _is_temporal(c) -> bool:
        t = (c.get("type") or "").upper()
        return any(x in t for x in ("DATE", "TIME", "TIMESTAMP")) or c["name"].endswith(("_at", "_date"))

    def _is_categorical(c) -> bool:
        return not _is_numeric(c) and not _is_temporal(c)

    numeric_cols = [c for c in cols if _is_numeric(c) and not c.get("is_pk")]
    temporal_cols = [c for c in cols if _is_temporal(c)]
    cat_cols = [c for c in cols if _is_categorical(c) and not c.get("is_pk")]

    # Cardinalidade aproximada (count distinct samples + non_null)
    def _cardinality_bucket(col_name: str) -> str:
        c = by_name.get(col_name) or {}
        n = int(c.get("non_null_count") or 0)
        samples = len(c.get("sample_values") or [])
        # Heurística: se temos só 3 samples mas non_null é alto, pode ser
        # variada; usamos n como proxy simplificado.
        if samples <= 3 and n <= 8:
            return "low"
        if n > 100:
            return "high"
        return "medium"

    # Resolve seleções faltantes
    label = body.label_column
    value = body.value_column

    if not label:
        # Prefere temporal se houver, depois primeira categórica não-PK
        label = (temporal_cols[0]["name"] if temporal_cols
                 else (cat_cols[0]["name"] if cat_cols
                       else (cols[0]["name"] if cols else None)))

    if not value:
        # Prefere a primeira numérica não-PK
        value = numeric_cols[0]["name"] if numeric_cols else ""

    # Decisão do chart_type
    label_meta = by_name.get(label) if label else None
    label_is_temporal = label_meta and _is_temporal(label_meta)
    label_card = _cardinality_bucket(label) if label else "medium"

    aggregate = "count"
    chart_type = "bar"
    rationale_parts: list[str] = []

    if label_is_temporal and value and _is_numeric(by_name.get(value, {})):
        chart_type = "line"
        aggregate = "sum"
        rationale_parts.append(f"label '{label}' é temporal e value '{value}' é numérico → série temporal (line)")
    elif body.intent_hint and any(k in body.intent_hint.lower() for k in ("compos", "parte", "fatia", "%")):
        chart_type = "donut" if label_card == "low" else "treemap"
        rationale_parts.append("intent menciona composição → donut/treemap")
    elif label_card == "high":
        chart_type = "treemap"
        rationale_parts.append(f"label '{label}' tem alta cardinalidade → treemap evita poluir o eixo")
    elif label_card == "low":
        chart_type = "donut"
        rationale_parts.append(f"label '{label}' tem baixa cardinalidade → donut destaca proporções")
    elif len(numeric_cols) >= 2 and value:
        chart_type = "scatter"
        aggregate = "none"
        rationale_parts.append("há 2+ colunas numéricas → scatter para correlação")
    else:
        rationale_parts.append("padrão seguro: bar de contagem por categoria")

    # Se value não existe, só pode count
    if not value:
        aggregate = "count"
        if chart_type in {"line", "scatter"}:
            chart_type = "bar"
            rationale_parts.append("sem coluna numérica adequada → fallback para bar(count)")

    # Indicator: se intenção menciona total/kpi
    if body.intent_hint and any(k in body.intent_hint.lower() for k in ("total", "kpi", "indicador")):
        chart_type = "indicator"
        aggregate = "sum" if value and _is_numeric(by_name.get(value, {})) else "count"
        rationale_parts.append("intent KPI → indicator")

    # Title sugerido
    label_friendly = label or "(sem label)"
    if aggregate == "count":
        title = f"Contagem por {label_friendly}"
    else:
        title = f"{aggregate.upper()}({value or '*'}) por {label_friendly}"

    return {
        "chart_type": chart_type,
        "label_column": label,
        "value_column": value,
        "aggregate": aggregate,
        "title": title,
        "rationale": " · ".join(rationale_parts),
        "column_types": {
            c["name"]: {
                "type": c["type"],
                "kind": (
                    "temporal" if _is_temporal(c)
                    else ("numeric" if _is_numeric(c) else "categorical")
                ),
                "is_pk": c["is_pk"],
            }
            for c in cols
        },
    }


# ============================================================
# Análise Inteligente do Dashboard
# ============================================================

def _analysis_to_payload(
    *,
    analysis_id: UUID,
    board_id: UUID,
    board_name: str,
    user_id: str | None,
    username: str,
    created_at,
    charts_snapshot: list[dict],
    per_chart: list[dict],
    synthesis: dict,
    totals: dict,
) -> dict:
    return {
        "id": str(analysis_id),
        "board_id": str(board_id),
        "board_name": board_name,
        "user_id": user_id,
        "username": username,
        "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at),
        "charts_snapshot": charts_snapshot,
        "per_chart": per_chart,
        "synthesis": synthesis,
        "totals": totals,
    }


@router.post("/boards/{board_id}/analyze")
async def analyze_board(
    board_id: UUID,
    svc: RaioXService = Depends(get_raiox_service),
    finops=Depends(get_finops_service),
    analyses=Depends(get_raiox_analysis_repo),
    user: User = Depends(require_user),
):
    """Gera análise por chart + síntese conjunta e persiste no histórico."""
    from app.core.domain.entities import RaioXAnalysis, new_uuid
    from app.core.services.raiox_analyzer_service import RaioXAnalyzerService

    analyzer = RaioXAnalyzerService(
        raiox=svc,
        router=get_router_clients(),
        finops=finops,
    )
    try:
        result = await analyzer.analyze_board(
            board_id=board_id,
            user_id=str(user.id) if user.id else None,
        )
    except ValueError as e:
        raise HTTPException(404, str(e))

    per_chart_serialized = [
        {
            "chart_id": c.chart_id,
            "title": c.title,
            "chart_type": c.chart_type,
            "skill": c.skill,
            "rows_returned": c.rows_returned,
            "analysis": c.analysis,
            "error": c.error,
            "model_used": c.model_used,
            "tokens_input": c.tokens_input,
            "tokens_output": c.tokens_output,
            "cost_estimated": c.cost_estimated,
        }
        for c in result.per_chart
    ]
    synthesis = {
        "correlations": result.correlations,
        "patterns": result.patterns,
        "risks": result.risks,
        "opportunities": result.opportunities,
    }
    totals = {
        "cost": result.total_cost,
        "tokens_input": result.total_tokens_input,
        "tokens_output": result.total_tokens_output,
        "model_used": result.model_used,
    }
    charts_snapshot = [
        {
            "chart_id": c.chart_id,
            "title": c.title,
            "chart_type": c.chart_type,
            "skill": c.skill,
        }
        for c in result.per_chart
    ]

    # Persiste no histórico
    saved = await analyses.save(
        RaioXAnalysis(
            id=new_uuid(),
            board_id=board_id,
            user_id=str(user.id) if user.id else None,
            username=user.username,
            charts_snapshot=charts_snapshot,
            per_chart=per_chart_serialized,
            synthesis=synthesis,
            totals=totals,
        )
    )

    return _analysis_to_payload(
        analysis_id=saved.id,
        board_id=board_id,
        board_name=result.board_name,
        user_id=saved.user_id,
        username=saved.username,
        created_at=saved.created_at,
        charts_snapshot=charts_snapshot,
        per_chart=per_chart_serialized,
        synthesis=synthesis,
        totals=totals,
    )


@router.get("/boards/{board_id}/analyses")
async def list_analyses(
    board_id: UUID,
    analyses=Depends(get_raiox_analysis_repo),
    svc: RaioXService = Depends(get_raiox_service),
    user: User = Depends(require_user),
):
    """Lista todas as análises persistidas para um board (mais recentes primeiro)."""
    if not await svc.get_board(board_id):
        raise HTTPException(404, "board não encontrado")
    rows = await analyses.list_for_board(board_id, limit=100)
    return [
        {
            "id": str(a.id),
            "board_id": str(a.board_id),
            "user_id": a.user_id,
            "username": a.username,
            "created_at": a.created_at.isoformat(),
            "charts_count": len(a.charts_snapshot),
            "totals": a.totals,
        }
        for a in rows
    ]


@router.get("/analyses/{analysis_id}")
async def get_analysis(
    analysis_id: UUID,
    analyses=Depends(get_raiox_analysis_repo),
    svc: RaioXService = Depends(get_raiox_service),
    user: User = Depends(require_user),
):
    a = await analyses.get(analysis_id)
    if not a:
        raise HTTPException(404, "análise não encontrada")
    board = await svc.get_board(a.board_id)
    return _analysis_to_payload(
        analysis_id=a.id,
        board_id=a.board_id,
        board_name=board.name if board else "",
        user_id=a.user_id,
        username=a.username,
        created_at=a.created_at,
        charts_snapshot=a.charts_snapshot,
        per_chart=a.per_chart,
        synthesis=a.synthesis,
        totals=a.totals,
    )


@router.delete("/analyses/{analysis_id}", status_code=204)
async def delete_analysis(
    analysis_id: UUID,
    analyses=Depends(get_raiox_analysis_repo),
    user: User = Depends(require_user),
):
    _require_edit(user)
    ok = await analyses.delete(analysis_id)
    if not ok:
        raise HTTPException(404, "análise não encontrada")


@router.get("/skills-options")
async def skills_options(user: User = Depends(require_user)):
    """Lista SKILL.md disponíveis para anexar a um chart."""
    from app.core.services.skill_service import SkillService
    svc = SkillService()
    return [
        {"name": s.name, "title": s.title, "path": s.path}
        for s in svc.list_all()
    ]


@router.get("/scope-options")
async def scope_options(user: User = Depends(require_user)):
    """Devolve a lista de papéis e departamentos para popular os multi-selects
    do modal de criação/edição de prancheta."""
    from app.adapters.db.sqlite import connect
    async with connect() as db:
        cur = await db.execute("SELECT name FROM roles ORDER BY name")
        roles = [r[0] for r in await cur.fetchall()]
        cur = await db.execute(
            "SELECT DISTINCT department FROM users "
            "WHERE department IS NOT NULL AND department != '' "
            "ORDER BY department"
        )
        departments = [r[0] for r in await cur.fetchall()]
    return {"roles": roles, "departments": departments}


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
