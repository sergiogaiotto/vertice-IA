"""Router HTTP da Rastreabilidade."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import require_user
from app.core.domain.entities import User
from app.core.services.audit_service import AuditService, get_audit_service

router = APIRouter()


def _serialize(e) -> dict:
    return {
        "id": e.id,
        "ts": e.ts.isoformat() if e.ts else None,
        "user_id": e.user_id,
        "username": e.username,
        "category": e.category,
        "action": e.action,
        "target": e.target,
        "status_code": e.status_code,
        "duration_ms": e.duration_ms,
        "feature": e.feature,
        "payload": e.payload,
        "error": e.error,
        "ip": e.ip,
        "user_agent": e.user_agent,
    }


@router.get("/")
async def list_audit(
    page: int = 1,
    per_page: int = 30,
    category: str | None = None,
    feature: str | None = None,
    username: str | None = None,
    status_min: int | None = None,
    q: str | None = None,
    since: str | None = None,
    svc: AuditService = Depends(get_audit_service),
    user: User = Depends(require_user),
):
    """Lista paginada com filtros. per_page=-1 retorna todos (cap em 5000).

    ``since`` aceita ``1h | 6h | 24h | 7d | 30d`` (janela rolante a partir de agora).
    """
    if per_page not in (-1, 10, 30, 100):
        raise HTTPException(400, "per_page deve ser 10, 30, 100 ou -1")
    result = await svc.list_events(
        page=page, per_page=per_page,
        category=category, feature=feature, username=username,
        status_min=status_min, q=q, since=since,
    )
    return {
        "events": [_serialize(e) for e in result["events"]],
        "total": result["total"],
        "page": result["page"],
        "per_page": result["per_page"],
    }


@router.get("/stats")
async def audit_stats(
    svc: AuditService = Depends(get_audit_service),
    user: User = Depends(require_user),
):
    return await svc.stats()


@router.get("/{event_id}")
async def get_event(
    event_id: str,
    svc: AuditService = Depends(get_audit_service),
    user: User = Depends(require_user),
):
    e = await svc.get_event(event_id)
    if not e:
        raise HTTPException(404, "evento não encontrado")
    return _serialize(e)
