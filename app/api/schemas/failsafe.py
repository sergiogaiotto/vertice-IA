"""Schemas Pydantic Failsafe."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class FailsafeOut(BaseModel):
    id: str
    module_name: str
    description: str
    payload: dict[str, Any]
    confidence: float
    status: str
    requested_by: str | None = None
    decided_by: str | None = None
    created_at: datetime


class DecideRequest(BaseModel):
    approve: bool


class FailsafeCreateRequest(BaseModel):
    module_name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)


class FailsafeUpdateRequest(BaseModel):
    """Todos os campos são opcionais — só os enviados são alterados."""
    description: str | None = Field(default=None, min_length=1)
    payload: dict[str, Any] | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class FailsafeListResponse(BaseModel):
    items: list[FailsafeOut]
    total: int
    page: int
    per_page: int


class FailsafeStatsOut(BaseModel):
    by_status: dict[str, int]
    total: int
