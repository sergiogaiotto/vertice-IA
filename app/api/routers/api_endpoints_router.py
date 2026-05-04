"""Router HTTP de API Endpoints externos."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import require_user
from app.core.domain.entities import User
from app.core.services.api_endpoint_service import (
    ApiEndpointService,
    get_api_endpoint_service,
)

router = APIRouter()


class ApiEndpointCreate(BaseModel):
    name: str
    url: str
    method: str = "POST"
    description: str = ""
    headers: dict = {}
    timeout_seconds: int = 30


class ApiEndpointUpdate(BaseModel):
    name: str
    url: str
    method: str = "POST"
    description: str = ""
    headers: dict = {}
    timeout_seconds: int = 30
    is_active: bool = True


class ApiEndpointTest(BaseModel):
    body: dict = {"input": "teste de conectividade"}


def _serialize(e) -> dict:
    return {
        "id": e.id,
        "name": e.name,
        "description": e.description,
        "url": e.url,
        "method": e.method,
        "headers": e.headers,
        "timeout_seconds": e.timeout_seconds,
        "is_active": e.is_active,
        "created_by_user": e.created_by_user,
        "created_at": e.created_at.isoformat() if e.created_at else None,
    }


@router.get("/")
async def list_endpoints(
    only_active: bool = False,
    svc: ApiEndpointService = Depends(get_api_endpoint_service),
    user: User = Depends(require_user),
):
    items = await svc.list_all(only_active=only_active)
    return [_serialize(e) for e in items]


@router.get("/{endpoint_id}")
async def get_endpoint(
    endpoint_id: str,
    svc: ApiEndpointService = Depends(get_api_endpoint_service),
    user: User = Depends(require_user),
):
    e = await svc.get(endpoint_id)
    if not e:
        raise HTTPException(404, "endpoint não encontrado")
    return _serialize(e)


@router.post("/", status_code=201)
async def create_endpoint(
    body: ApiEndpointCreate,
    svc: ApiEndpointService = Depends(get_api_endpoint_service),
    user: User = Depends(require_user),
):
    if "admin" not in (user.roles or []):
        raise HTTPException(403, "apenas admin pode criar endpoints")
    if not body.name or not body.url:
        raise HTTPException(400, "name e url são obrigatórios")
    e = await svc.create(
        name=body.name, url=body.url, method=body.method,
        description=body.description, headers=body.headers,
        timeout_seconds=body.timeout_seconds,
        created_by_user=user.username,
    )
    return _serialize(e)


@router.patch("/{endpoint_id}")
async def update_endpoint(
    endpoint_id: str,
    body: ApiEndpointUpdate,
    svc: ApiEndpointService = Depends(get_api_endpoint_service),
    user: User = Depends(require_user),
):
    if "admin" not in (user.roles or []):
        raise HTTPException(403, "apenas admin pode editar endpoints")
    e = await svc.update(
        endpoint_id=endpoint_id, name=body.name, url=body.url, method=body.method,
        description=body.description, headers=body.headers,
        timeout_seconds=body.timeout_seconds, is_active=body.is_active,
    )
    if not e:
        raise HTTPException(404, "endpoint não encontrado")
    return _serialize(e)


@router.delete("/{endpoint_id}")
async def delete_endpoint(
    endpoint_id: str,
    svc: ApiEndpointService = Depends(get_api_endpoint_service),
    user: User = Depends(require_user),
):
    if "admin" not in (user.roles or []):
        raise HTTPException(403, "apenas admin pode excluir endpoints")
    await svc.delete(endpoint_id)
    return {"ok": True}


@router.post("/{endpoint_id}/test")
async def test_endpoint(
    endpoint_id: str,
    body: ApiEndpointTest,
    svc: ApiEndpointService = Depends(get_api_endpoint_service),
    user: User = Depends(require_user),
):
    """Testa conectividade chamando o endpoint com um body de exemplo."""
    e = await svc.get(endpoint_id)
    if not e:
        raise HTTPException(404, "endpoint não encontrado")
    result = await svc.call(endpoint=e, body=body.body, user_id=str(user.id))
    return result
