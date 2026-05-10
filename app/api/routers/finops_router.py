"""Router HTTP do módulo FinOps — Ledger, Budgets, Policies, Routing."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import Response

from app.api.deps import (
    get_cost_aware_router,
    get_finops_budget_service,
    get_finops_policy_service,
    get_finops_service,
    require_roles,
)
from app.api.schemas.finops import (
    AggregateRow,
    AlertOut,
    BudgetCreateRequest,
    BudgetOut,
    BudgetStatusOut,
    BudgetUpdateRequest,
    DimensionRow,
    FinOpsSummary,
    ImportResultOut,
    ImportRowError,
    PolicyOut,
    PolicyUpsertRequest,
    RouteRequest,
    RouteResponse,
)
from app.core.domain.entities import User
from app.core.services.finops_service import (
    CostAwareRouter,
    FinOpsBudgetService,
    FinOpsPolicyService,
    FinOpsService,
)

router = APIRouter()

# Custos, orçamentos, políticas e recomendação de roteamento são sensíveis —
# espelha o gate da página /finops em pages.py (admin/supervisor/finops).
_FINOPS_ROLES = ("admin", "supervisor", "finops")


# ---------------------------------------------------------------------------
# Sumário e dimensões (chargeback/showback)
# ---------------------------------------------------------------------------


@router.get("/summary", response_model=FinOpsSummary)
async def summary(
    svc: FinOpsService = Depends(get_finops_service),
    user: User = Depends(require_roles(*_FINOPS_ROLES)),
):
    by_module = await svc.by_module()
    by_model = await svc.by_model()
    total_cost = sum(r["cost"] for r in by_model)
    total_calls = sum(r["calls"] for r in by_model)
    return FinOpsSummary(
        by_module=[
            AggregateRow(
                key=r["module"], tokens_input=r["tokens_input"],
                tokens_output=r["tokens_output"], cost=r["cost"], calls=r["calls"],
            )
            for r in by_module
        ],
        by_model=[
            AggregateRow(
                key=r["model"], tokens_input=r["tokens_input"],
                tokens_output=r["tokens_output"], cost=r["cost"], calls=r["calls"],
            )
            for r in by_model
        ],
        total_cost=round(total_cost, 6),
        total_calls=total_calls,
    )


@router.get("/by-dimension", response_model=list[DimensionRow])
async def by_dimension(
    dim: str = Query(..., description="domain|product|agent|flow|prompt_id|integration|environment|module|model"),
    svc: FinOpsService = Depends(get_finops_service),
    user: User = Depends(require_roles(*_FINOPS_ROLES)),
):
    """Chargeback/showback genérico — qualquer dimensão suportada pelo ledger."""
    try:
        rows = await svc.by_dimension(dim)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return [DimensionRow(**r) for r in rows]


# ---------------------------------------------------------------------------
# Orçamentos
# ---------------------------------------------------------------------------


def _budget_to_out(b) -> BudgetOut:
    return BudgetOut(
        id=str(b.id),
        name=b.name,
        scope_type=b.scope_type.value,
        scope_value=b.scope_value,
        period=b.period.value,
        limit_brl=b.limit_brl,
        warning_threshold=b.warning_threshold,
        hard_stop=b.hard_stop,
        notes=b.notes,
    )


@router.get("/budgets", response_model=list[BudgetStatusOut])
async def list_budgets(
    svc: FinOpsBudgetService = Depends(get_finops_budget_service),
    user: User = Depends(require_roles(*_FINOPS_ROLES)),
):
    statuses = await svc.evaluate_all()
    return [
        BudgetStatusOut(
            budget=_budget_to_out(s.budget),
            spent=round(s.spent, 6),
            remaining=round(s.remaining, 6),
            pct_used=round(s.pct_used, 4),
            severity=s.severity,
        )
        for s in statuses
    ]


@router.post("/budgets", response_model=BudgetOut, status_code=status.HTTP_201_CREATED)
async def create_budget(
    body: BudgetCreateRequest,
    svc: FinOpsBudgetService = Depends(get_finops_budget_service),
    user: User = Depends(require_roles(*_FINOPS_ROLES)),
):
    try:
        b = await svc.create(
            name=body.name, scope_type=body.scope_type, scope_value=body.scope_value,
            period=body.period, limit_brl=body.limit_brl,
            warning_threshold=body.warning_threshold, hard_stop=body.hard_stop,
            notes=body.notes, created_by=user.id,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _budget_to_out(b)


@router.patch("/budgets/{budget_id}", response_model=BudgetOut)
async def update_budget(
    budget_id: UUID,
    body: BudgetUpdateRequest,
    svc: FinOpsBudgetService = Depends(get_finops_budget_service),
    user: User = Depends(require_roles(*_FINOPS_ROLES)),
):
    try:
        b = await svc.update(
            budget_id, name=body.name, limit_brl=body.limit_brl,
            warning_threshold=body.warning_threshold, hard_stop=body.hard_stop,
            notes=body.notes,
        )
    except ValueError as e:
        msg = str(e); code = 404 if "não encontrado" in msg else 400
        raise HTTPException(code, msg)
    return _budget_to_out(b)


@router.delete("/budgets/{budget_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_budget(
    budget_id: UUID,
    svc: FinOpsBudgetService = Depends(get_finops_budget_service),
    user: User = Depends(require_roles(*_FINOPS_ROLES)),
):
    try:
        await svc.delete(budget_id)
    except ValueError as e:
        raise HTTPException(404, str(e))


_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.get("/budgets/template.xlsx")
async def budgets_template(user: User = Depends(require_roles(*_FINOPS_ROLES))):
    """Baixa template xlsx para preenchimento e upload em massa de orçamentos."""
    bytes_ = FinOpsBudgetService.xlsx_template()
    return Response(
        content=bytes_,
        media_type=_XLSX_MIME,
        headers={"Content-Disposition": 'attachment; filename="finops_orcamentos_template.xlsx"'},
    )


@router.post("/budgets/import", response_model=ImportResultOut)
async def budgets_import(
    file: UploadFile = File(...),
    svc: FinOpsBudgetService = Depends(get_finops_budget_service),
    user: User = Depends(require_roles(*_FINOPS_ROLES)),
):
    """Importa orçamentos a partir do template xlsx. Linhas inválidas são
    pulladas e devolvidas em ``errors`` — uma linha ruim não cancela as outras."""
    if not (file.filename or "").lower().endswith(".xlsx"):
        raise HTTPException(400, "envie um arquivo .xlsx")
    payload = await file.read()
    if not payload:
        raise HTTPException(400, "arquivo vazio")
    try:
        result = await svc.import_xlsx(payload, created_by=user.id)
    except Exception as e:
        raise HTTPException(500, f"falha ao processar planilha: {e}")
    return ImportResultOut(
        imported=result["imported"],
        errors=[ImportRowError(**err) for err in result.get("errors", [])],
    )


@router.get("/policies/template.xlsx")
async def policies_template(user: User = Depends(require_roles(*_FINOPS_ROLES))):
    """Baixa template xlsx para upload em massa de políticas de modelo."""
    bytes_ = FinOpsPolicyService.xlsx_template()
    return Response(
        content=bytes_,
        media_type=_XLSX_MIME,
        headers={"Content-Disposition": 'attachment; filename="finops_politicas_template.xlsx"'},
    )


@router.post("/policies/import", response_model=ImportResultOut)
async def policies_import(
    file: UploadFile = File(...),
    svc: FinOpsPolicyService = Depends(get_finops_policy_service),
    user: User = Depends(require_roles(*_FINOPS_ROLES)),
):
    """Importa políticas via xlsx. Política reimportada com mesmo
    ``model_name`` faz UPSERT (atualiza em vez de duplicar)."""
    if not (file.filename or "").lower().endswith(".xlsx"):
        raise HTTPException(400, "envie um arquivo .xlsx")
    payload = await file.read()
    if not payload:
        raise HTTPException(400, "arquivo vazio")
    try:
        result = await svc.import_xlsx(payload)
    except Exception as e:
        raise HTTPException(500, f"falha ao processar planilha: {e}")
    return ImportResultOut(
        imported=result["imported"],
        errors=[ImportRowError(**err) for err in result.get("errors", [])],
    )


@router.get("/alerts", response_model=list[AlertOut])
async def list_alerts(
    limit: int = Query(20, ge=1, le=200),
    svc: FinOpsBudgetService = Depends(get_finops_budget_service),
    user: User = Depends(require_roles(*_FINOPS_ROLES)),
):
    rows = await svc.recent_alerts(limit)
    return [
        AlertOut(
            id=str(a.id), budget_id=str(a.budget_id), severity=a.severity,
            cost_observed=a.cost_observed, limit_reference=a.limit_reference,
            triggered_at=a.triggered_at,
        )
        for a in rows
    ]


# ---------------------------------------------------------------------------
# Políticas de modelo
# ---------------------------------------------------------------------------


def _policy_to_out(p) -> PolicyOut:
    return PolicyOut(
        id=str(p.id),
        model_name=p.model_name,
        risk_tier=p.risk_tier.value,
        value_tier=p.value_tier.value,
        max_cost_per_call=p.max_cost_per_call,
        max_tokens_per_call=p.max_tokens_per_call,
        allowed_features=p.allowed_features,
        rationale=p.rationale,
        enabled=p.enabled,
    )


@router.get("/policies", response_model=list[PolicyOut])
async def list_policies(
    svc: FinOpsPolicyService = Depends(get_finops_policy_service),
    user: User = Depends(require_roles(*_FINOPS_ROLES)),
):
    return [_policy_to_out(p) for p in await svc.list()]


@router.post("/policies", response_model=PolicyOut)
async def upsert_policy(
    body: PolicyUpsertRequest,
    svc: FinOpsPolicyService = Depends(get_finops_policy_service),
    user: User = Depends(require_roles(*_FINOPS_ROLES)),
):
    try:
        p = await svc.upsert(
            model_name=body.model_name, risk_tier=body.risk_tier,
            value_tier=body.value_tier, max_cost_per_call=body.max_cost_per_call,
            max_tokens_per_call=body.max_tokens_per_call,
            allowed_features=body.allowed_features, rationale=body.rationale,
            enabled=body.enabled,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _policy_to_out(p)


@router.delete("/policies/{policy_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_policy(
    policy_id: UUID,
    svc: FinOpsPolicyService = Depends(get_finops_policy_service),
    user: User = Depends(require_roles(*_FINOPS_ROLES)),
):
    try:
        await svc.delete(policy_id)
    except ValueError as e:
        raise HTTPException(404, str(e))


# ---------------------------------------------------------------------------
# Cost-aware routing — recomenda modelo dado custo + política + budget
# ---------------------------------------------------------------------------


@router.post("/route/recommend", response_model=RouteResponse)
async def route_recommend(
    body: RouteRequest,
    router_svc: CostAwareRouter = Depends(get_cost_aware_router),
    user: User = Depends(require_roles(*_FINOPS_ROLES)),
):
    """Recebe candidatos (modelo + custo estimado) e devolve a recomendação
    sob políticas e orçamentos vigentes. Idempotente — não consome ledger."""
    candidates = [{"model": c.model, "estimated_cost": c.estimated_cost} for c in body.candidates]
    try:
        result = await router_svc.recommend(
            candidates=candidates,
            feature=body.feature,
            min_value_tier=body.min_value_tier,
        )
    except KeyError as e:
        raise HTTPException(400, f"min_value_tier inválido: {e}")
    return RouteResponse(**result)
