"""Schemas Pydantic do CRUD de Módulos."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class ModuleOut(BaseModel):
    id: str
    name: str
    endpoint_url: str
    status: str
    config_params: dict[str, Any]
    description: str
    skill_path: str | None
    response_type: str = "text"
    response_config: dict[str, Any] = {}


class CreateModuleRequest(BaseModel):
    name: str
    endpoint_url: str
    description: str = ""
    config_params: dict[str, Any] = {}
    skill_path: str | None = None
    response_type: str = "text"          # 'text' | 'api' | 'table'
    response_config: dict[str, Any] = {}


class UpdateModuleRequest(BaseModel):
    endpoint_url: str | None = None
    description: str | None = None
    config_params: dict[str, Any] | None = None
    skill_path: str | None = None
    status: str | None = None
    response_type: str | None = None
    response_config: dict[str, Any] | None = None


class HealthCheckResult(BaseModel):
    ok: bool
    status_code: int | None
    latency_ms: float
    url: str
    error: str | None = None
