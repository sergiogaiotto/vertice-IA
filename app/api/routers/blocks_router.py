"""Router HTTP do Building Blocks — catálogo agregado de módulos + skills + prompts."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.deps import (
    get_prompt_service,
    get_registry_service,
    get_skill_service,
    require_user,
)
from app.core.domain.entities import User
from app.core.services.prompt_service import PromptService
from app.core.services.registry_service import RegistryService
from app.core.services.skill_service import SkillService

router = APIRouter()


class BlockSummary(BaseModel):
    id: str
    name: str
    title: str
    description: str
    status: str
    skill_name: str | None
    prompts_count: int
    config_params: dict[str, Any]
    cover_seed: str       # para gerar gradiente determinístico no front


@router.get("/", response_model=list[BlockSummary])
async def list_blocks(
    reg: RegistryService = Depends(get_registry_service),
    skills: SkillService = Depends(get_skill_service),
    prompts: PromptService = Depends(get_prompt_service),
    user: User = Depends(require_user),
):
    """Devolve cada módulo enriquecido com a skill associada e contagem de prompts."""
    modules = await reg.list_all()
    all_prompts = await prompts.list_all()
    out: list[BlockSummary] = []
    for m in modules:
        skill_name = None
        if m.skill_path:
            stem = m.skill_path.rsplit("/", 1)[-1].replace(".md", "")
            s = skills.get(stem)
            skill_name = s.name if s else stem
        cnt = sum(1 for p in all_prompts if p.module_name == m.name)
        out.append(
            BlockSummary(
                id=str(m.id),
                name=m.name,
                title=m.name.replace("_", " ").title(),
                description=m.description or "Sem descrição.",
                status=m.status.value,
                skill_name=skill_name,
                prompts_count=cnt,
                config_params=m.config_params,
                cover_seed=m.name,
            )
        )
    return out
