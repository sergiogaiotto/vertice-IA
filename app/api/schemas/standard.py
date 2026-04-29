"""Standard Module Contract — schemas Pydantic conforme spec OpenAPI 3.0.3."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ConfigOverride(BaseModel):
    threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    sanitization: bool | None = None


class FinOpsMetadata(BaseModel):
    tag: str = ""
    user_id: str | None = None


class StandardRequest(BaseModel):
    input_data: dict[str, Any]
    config_override: ConfigOverride | None = None
    finops_metadata: FinOpsMetadata | None = None


class StandardResponse(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    output_data: dict[str, Any]
    model_used: str | None = None
    tokens_input: int = 0
    tokens_output: int = 0
    cost_estimated: float = 0.0
    flags: list[str] = []
