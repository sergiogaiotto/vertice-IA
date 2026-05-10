"""Router HTTP do CRUD de Módulos (Building Blocks).

Atenção: rotas com paths literais (ex: 'wizard/suggest', 'skills/availability')
DEVEM vir ANTES das rotas com path params (ex: '{module_id}'). Caso contrário
FastAPI tenta parsear 'wizard' como UUID e devolve 422.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import (
    get_module_wizard_service,
    get_registry_service,
    get_skill_service,
    require_roles,
    require_user,
)
from app.api.schemas.modules import (
    CreateModuleRequest,
    HealthCheckResult,
    ModuleOut,
    UpdateModuleRequest,
)
from app.core.domain.entities import ModuleStatus, User
from app.core.services.module_wizard_service import ModuleWizardService
from app.core.services.registry_service import RegistryService
from app.core.services.skill_service import SkillService

router = APIRouter()


def _to_out(m) -> ModuleOut:
    return ModuleOut(
        id=str(m.id),
        name=m.name,
        endpoint_url=m.endpoint_url,
        status=m.status.value,
        config_params=m.config_params,
        description=m.description,
        skill_path=m.skill_path,
        response_type=getattr(m, "response_type", "text") or "text",
        response_config=getattr(m, "response_config", {}) or {},
    )


# ============================================================
# Rotas literais (PRECISAM vir antes das rotas com {module_id})
# ============================================================


class WizardRequest(BaseModel):
    prompt: str
    llm_preference: str | None = None


class WizardResponse(BaseModel):
    name: str
    endpoint_url: str
    description: str
    config_params: dict
    suggested_skill: str | None
    reasoning: str
    source: str


@router.post("/wizard/suggest", response_model=WizardResponse)
async def wizard_suggest(
    body: WizardRequest,
    wiz: ModuleWizardService = Depends(get_module_wizard_service),
    user: User = Depends(require_user),
):
    """Wizard 'IA, me ajuda' — sugere setup completo a partir de descrição livre."""
    suggestion = await wiz.suggest(body.prompt, body.llm_preference)
    return WizardResponse(**ModuleWizardService.to_dict(suggestion))


class SkillAvailability(BaseModel):
    name: str
    title: str
    skill_path: str
    used_by: str | None  # nome do módulo que já usa, ou None se livre


@router.get("/skills/availability", response_model=list[SkillAvailability])
async def skills_availability(
    reg: RegistryService = Depends(get_registry_service),
    skills: SkillService = Depends(get_skill_service),
    user: User = Depends(require_user),
):
    """Lista todas as skills com flag de uso — front filtra para o combo."""
    all_skills = skills.list_all()
    modules = await reg.list_all()
    used_map: dict[str, str] = {}
    for m in modules:
        if m.skill_path:
            used_map[m.skill_path] = m.name
    return [
        SkillAvailability(
            name=s.name,
            title=s.title,
            skill_path=s.path,
            used_by=used_map.get(s.path),
        )
        for s in all_skills
    ]


# ============================================================
# CRUD principal
# ============================================================


@router.get("/", response_model=list[ModuleOut])
async def list_modules(
    include_inactive: bool = False,
    svc: RegistryService = Depends(get_registry_service),
    user: User = Depends(require_user),
):
    items = await (svc.list_all() if include_inactive else svc.list_active())
    return [_to_out(m) for m in items]


@router.post("/", response_model=ModuleOut, status_code=201)
async def create_module(
    body: CreateModuleRequest,
    svc: RegistryService = Depends(get_registry_service),
    user: User = Depends(require_roles("admin", "supervisor")),
):
    existing = await svc.get_by_name(body.name)
    if existing:
        raise HTTPException(400, f"módulo '{body.name}' já existe")
    m = await svc.register(
        name=body.name,
        endpoint_url=body.endpoint_url,
        description=body.description,
        config_params=body.config_params,
        skill_path=body.skill_path,
        response_type=body.response_type,
        response_config=body.response_config,
    )
    return _to_out(m)


@router.get("/{module_id}", response_model=ModuleOut)
async def get_module(
    module_id: UUID,
    svc: RegistryService = Depends(get_registry_service),
    user: User = Depends(require_user),
):
    m = await svc.get(module_id)
    if not m:
        raise HTTPException(404, "módulo não encontrado")
    return _to_out(m)


@router.patch("/{module_id}", response_model=ModuleOut)
async def update_module(
    module_id: UUID,
    body: UpdateModuleRequest,
    svc: RegistryService = Depends(get_registry_service),
    user: User = Depends(require_roles("admin", "supervisor")),
):
    try:
        m = await svc.update(
            module_id,
            endpoint_url=body.endpoint_url,
            description=body.description,
            config_params=body.config_params,
            skill_path=body.skill_path,
            status=body.status,
            response_type=body.response_type,
            response_config=body.response_config,
        )
    except ValueError as e:
        raise HTTPException(404, str(e))
    return _to_out(m)


@router.post("/{module_id}/pause", response_model=ModuleOut)
async def pause_module(
    module_id: UUID,
    svc: RegistryService = Depends(get_registry_service),
    user: User = Depends(require_roles("admin", "supervisor")),
):
    try:
        m = await svc.set_status(module_id, ModuleStatus.paused)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return _to_out(m)


@router.post("/{module_id}/resume", response_model=ModuleOut)
async def resume_module(
    module_id: UUID,
    svc: RegistryService = Depends(get_registry_service),
    user: User = Depends(require_roles("admin", "supervisor")),
):
    try:
        m = await svc.set_status(module_id, ModuleStatus.active)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return _to_out(m)


@router.delete("/{module_id}")
async def delete_module(
    module_id: UUID,
    svc: RegistryService = Depends(get_registry_service),
    user: User = Depends(require_roles("admin", "supervisor")),
):
    try:
        await svc.delete(module_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return {"ok": True}


@router.get("/{module_id}/health", response_model=HealthCheckResult)
async def health_check(
    module_id: UUID,
    svc: RegistryService = Depends(get_registry_service),
    user: User = Depends(require_user),
):
    try:
        result = await svc.health_check(module_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return HealthCheckResult(**result)
