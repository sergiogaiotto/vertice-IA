"""Schemas Pydantic do módulo de Prompts."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class PromptBundleOut(BaseModel):
    id: str
    name: str
    module_names: list[str]
    version: int
    input_guardrail: str
    system_prompt: str
    output_guardrail: str
    is_active: bool
    created_at: datetime
    # legado: primeiro módulo da lista — mantido para clientes antigos que iteram por module_name
    module_name: str = ""


class SavePromptRequest(BaseModel):
    name: str
    system_prompt: str
    input_guardrail: str = ""
    output_guardrail: str = ""
    module_names: list[str] = []


class UpdateModulesRequest(BaseModel):
    module_names: list[str]
