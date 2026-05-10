"""Router HTTP do CRUD de Prompts (N:N com módulos)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_prompt_service, require_roles, require_user
from app.api.schemas.prompt import (
    PromptBundleOut,
    SavePromptRequest,
    UpdateModulesRequest,
)
from app.core.domain.entities import User
from app.core.services.prompt_service import PromptService

router = APIRouter()


def _to_out(p) -> PromptBundleOut:
    return PromptBundleOut(
        id=str(p.id),
        name=p.name,
        module_names=p.module_names,
        version=p.version,
        input_guardrail=p.input_guardrail,
        system_prompt=p.system_prompt,
        output_guardrail=p.output_guardrail,
        is_active=p.is_active,
        created_at=p.created_at,
        module_name=p.module_name,  # property: primeiro item ou ""
    )


@router.get("/", response_model=list[PromptBundleOut])
async def list_all(
    svc: PromptService = Depends(get_prompt_service),
    user: User = Depends(require_user),
):
    return [_to_out(p) for p in await svc.list_all()]


@router.get("/by-module/{module_name}", response_model=list[PromptBundleOut])
async def list_for_module(
    module_name: str,
    svc: PromptService = Depends(get_prompt_service),
    user: User = Depends(require_user),
):
    return [_to_out(p) for p in await svc.list_for_module(module_name)]


@router.get("/{prompt_id}", response_model=PromptBundleOut)
async def get_one(
    prompt_id: UUID,
    svc: PromptService = Depends(get_prompt_service),
    user: User = Depends(require_user),
):
    p = await svc.get(prompt_id)
    if not p:
        raise HTTPException(404, "prompt não encontrado")
    return _to_out(p)


@router.post("/", response_model=PromptBundleOut, status_code=201)
async def save_new(
    body: SavePromptRequest,
    svc: PromptService = Depends(get_prompt_service),
    user: User = Depends(require_roles("admin", "supervisor")),
):
    p = await svc.save_new_version(
        name=body.name,
        input_guardrail=body.input_guardrail,
        system_prompt=body.system_prompt,
        output_guardrail=body.output_guardrail,
        module_names=body.module_names,
    )
    return _to_out(p)


@router.post("/{prompt_id}/promote", response_model=PromptBundleOut)
async def promote(
    prompt_id: UUID,
    svc: PromptService = Depends(get_prompt_service),
    user: User = Depends(require_roles("admin", "supervisor")),
):
    await svc.promote(prompt_id)
    p = await svc.get(prompt_id)
    return _to_out(p)


@router.patch("/{prompt_id}/modules", response_model=PromptBundleOut)
async def set_modules(
    prompt_id: UUID,
    body: UpdateModulesRequest,
    svc: PromptService = Depends(get_prompt_service),
    user: User = Depends(require_roles("admin", "supervisor")),
):
    """Atualiza a lista de módulos associados ao prompt (propaga para todas as versões com mesmo nome)."""
    try:
        await svc.set_modules(prompt_id, body.module_names)
    except ValueError as e:
        raise HTTPException(404, str(e))
    p = await svc.get(prompt_id)
    return _to_out(p)


@router.delete("/{prompt_id}")
async def delete_one(
    prompt_id: UUID,
    svc: PromptService = Depends(get_prompt_service),
    user: User = Depends(require_roles("admin", "supervisor")),
):
    await svc.delete(prompt_id)
    return {"ok": True}


@router.get("/{a_id}/diff/{b_id}")
async def diff(
    a_id: UUID,
    b_id: UUID,
    svc: PromptService = Depends(get_prompt_service),
    user: User = Depends(require_user),
):
    try:
        return await svc.diff(a_id, b_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
