"""Router HTTP do módulo Failsafe (CRUD + decide)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from app.api.deps import get_failsafe_service, require_roles, require_user
from app.api.schemas.failsafe import (
    DecideRequest,
    FailsafeCreateRequest,
    FailsafeListResponse,
    FailsafeOut,
    FailsafeStatsOut,
    FailsafeUpdateRequest,
)
from app.core.domain.entities import User
from app.core.services.failsafe_service import FailsafeService

router = APIRouter()


def _to_out(a) -> FailsafeOut:
    return FailsafeOut(
        id=str(a.id),
        module_name=a.module_name,
        description=a.description,
        payload=a.payload,
        confidence=a.confidence,
        status=a.status.value,
        requested_by=str(a.requested_by) if a.requested_by else None,
        decided_by=str(a.decided_by) if a.decided_by else None,
        created_at=a.created_at,
    )


# Inbox (compat) — equivalente a GET / com ?status=pending
@router.get("/inbox", response_model=list[FailsafeOut])
async def list_inbox(
    svc: FailsafeService = Depends(get_failsafe_service),
    user: User = Depends(require_user),
):
    return [_to_out(a) for a in await svc.list_pending()]


# CRUD principal
@router.get("/", response_model=FailsafeListResponse)
async def list_actions(
    status_: str | None = Query(default=None, alias="status"),
    module_name: str | None = None,
    q: str | None = None,
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=30),
    svc: FailsafeService = Depends(get_failsafe_service),
    user: User = Depends(require_user),
):
    """Lista paginada com filtros: status, module_name, busca textual (q)."""
    try:
        result = await svc.list(
            status=status_, module_name=module_name, q=q,
            page=page, per_page=per_page,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return FailsafeListResponse(
        items=[_to_out(a) for a in result["items"]],
        total=result["total"],
        page=result["page"],
        per_page=result["per_page"],
    )


@router.get("/stats", response_model=FailsafeStatsOut)
async def get_stats(
    svc: FailsafeService = Depends(get_failsafe_service),
    user: User = Depends(require_user),
):
    return FailsafeStatsOut(**await svc.stats())


@router.get("/{action_id}", response_model=FailsafeOut)
async def get_action(
    action_id: UUID,
    svc: FailsafeService = Depends(get_failsafe_service),
    user: User = Depends(require_user),
):
    try:
        return _to_out(await svc.get(action_id))
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.post("/", response_model=FailsafeOut, status_code=status.HTTP_201_CREATED)
async def create_action(
    body: FailsafeCreateRequest,
    svc: FailsafeService = Depends(get_failsafe_service),
    user: User = Depends(require_roles("admin", "supervisor", "finops")),
):
    """Cria ação Failsafe manualmente (uso administrativo / testes)."""
    try:
        action = await svc.request(
            module_name=body.module_name,
            description=body.description,
            payload=body.payload,
            confidence=body.confidence,
            requested_by=user.id,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _to_out(action)


@router.patch("/{action_id}", response_model=FailsafeOut)
async def update_action(
    action_id: UUID,
    body: FailsafeUpdateRequest,
    svc: FailsafeService = Depends(get_failsafe_service),
    user: User = Depends(require_roles("admin", "supervisor", "finops")),
):
    """Edita ação enquanto está `pending`. Decisões são imutáveis."""
    try:
        action = await svc.update(
            action_id=action_id,
            description=body.description,
            payload=body.payload,
            confidence=body.confidence,
        )
    except ValueError as e:
        # 404 só quando não existe; 409 quando estado proíbe edição
        msg = str(e)
        code = 404 if "não encontrada" in msg else 409
        raise HTTPException(code, msg)
    return _to_out(action)


@router.delete("/{action_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_action(
    action_id: UUID,
    svc: FailsafeService = Depends(get_failsafe_service),
    user: User = Depends(require_roles("admin", "supervisor", "finops")),
):
    """Apaga ação `pending`. Decisões já tomadas ficam preservadas (audit)."""
    try:
        await svc.delete(action_id)
    except ValueError as e:
        msg = str(e)
        code = 404 if "não encontrada" in msg else 409
        raise HTTPException(code, msg)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# Decisão (mantido em path próprio para clareza semântica vs. PATCH genérico).
# Aprovação/rejeição é o ato administrativo mais sensível — gate idêntico ao
# da página /failsafe em pages.py (admin/supervisor/finops).
@router.post("/{action_id}/decide", response_model=FailsafeOut)
async def decide(
    action_id: UUID,
    body: DecideRequest,
    svc: FailsafeService = Depends(get_failsafe_service),
    user: User = Depends(require_roles("admin", "supervisor", "finops")),
):
    try:
        a = await svc.decide(action_id, body.approve, decided_by=user.id)
    except ValueError as e:
        msg = str(e)
        code = 404 if "não encontrada" in msg else 409
        raise HTTPException(code, msg)
    return _to_out(a)
