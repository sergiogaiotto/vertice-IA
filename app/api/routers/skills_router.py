"""Router HTTP do CRUD de Skills (SKILL.md filesystem-based)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import get_skill_service, get_skill_wizard_service, require_user
from app.api.schemas.skills import (
    CreateSkillRequest,
    SaveSkillRequest,
    SkillDetail,
    SkillSummary,
)
from app.core.domain.entities import User
from app.core.services.skill_service import SkillService
from app.core.services.skill_wizard_service import SkillWizardService

router = APIRouter()


class SkillWizardRequest(BaseModel):
    prompt: str = Field(..., min_length=5, max_length=2000)


class SkillWizardResponse(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    name: str
    title: str
    content: str
    output_format: str
    reasoning: str
    source: str
    tokens_input: int = 0
    tokens_output: int = 0
    cost_estimated: float = 0.0
    model_used: str = ""


def _to_summary(s) -> SkillSummary:
    return SkillSummary(
        name=s.name,
        title=s.title,
        path=s.path,
        sections=list(s.sections.keys()),
        updated_at=s.updated_at,
        size_bytes=s.size_bytes,
    )


def _to_detail(s) -> SkillDetail:
    return SkillDetail(
        name=s.name,
        title=s.title,
        path=s.path,
        content=s.content,
        sections=s.sections,
        updated_at=s.updated_at,
        size_bytes=s.size_bytes,
    )


@router.get("/", response_model=list[SkillSummary])
async def list_skills(
    svc: SkillService = Depends(get_skill_service),
    user: User = Depends(require_user),
):
    return [_to_summary(s) for s in svc.list_all()]


@router.get("/template")
async def get_template(
    svc: SkillService = Depends(get_skill_service),
    user: User = Depends(require_user),
):
    return {"content": svc.template()}


@router.post("/wizard/suggest", response_model=SkillWizardResponse)
async def wizard_suggest(
    body: SkillWizardRequest,
    wiz: SkillWizardService = Depends(get_skill_wizard_service),
    user: User = Depends(require_user),
):
    """Gera SKILL.md a partir de descrição em linguagem natural.

    Path literal antes de `/{name}` para FastAPI não tentar parsear como nome de skill.
    """
    try:
        suggestion = await wiz.suggest(body.prompt)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return SkillWizardResponse(
        name=suggestion.name,
        title=suggestion.title,
        content=suggestion.content,
        output_format=suggestion.output_format,
        reasoning=suggestion.reasoning,
        source=suggestion.source,
        tokens_input=suggestion.tokens_input,
        tokens_output=suggestion.tokens_output,
        cost_estimated=suggestion.cost_estimated,
        model_used=suggestion.model_used,
    )


@router.get("/{name}", response_model=SkillDetail)
async def get_skill(
    name: str,
    svc: SkillService = Depends(get_skill_service),
    user: User = Depends(require_user),
):
    s = svc.get(name)
    if not s:
        raise HTTPException(404, "skill não encontrada")
    return _to_detail(s)


@router.post("/", response_model=SkillDetail, status_code=201)
async def create_skill(
    body: CreateSkillRequest,
    svc: SkillService = Depends(get_skill_service),
    user: User = Depends(require_user),
):
    if svc.get(body.name):
        raise HTTPException(400, f"skill '{body.name}' já existe")
    content = body.content or svc.template()
    s = svc.save(body.name, content)
    return _to_detail(s)


@router.put("/{name}", response_model=SkillDetail)
async def save_skill(
    name: str,
    body: SaveSkillRequest,
    svc: SkillService = Depends(get_skill_service),
    user: User = Depends(require_user),
):
    s = svc.save(name, body.content)
    return _to_detail(s)


@router.delete("/{name}")
async def delete_skill(
    name: str,
    svc: SkillService = Depends(get_skill_service),
    user: User = Depends(require_user),
):
    if not svc.delete(name):
        raise HTTPException(404, "skill não encontrada")
    return {"ok": True}
