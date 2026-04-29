"""Schemas Pydantic FinOps."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# Pydantic v2 reserva o namespace `model_` (model_dump, model_validate, etc.)
# e avisa quando schemas têm campos como `model_name`/`model`. Como o domínio
# FinOps usa esses nomes naturalmente (modelo de LLM != Pydantic model),
# liberamos o namespace explicitamente.
_NO_PROTECTED_NS = ConfigDict(protected_namespaces=())


class AggregateRow(BaseModel):
    key: str
    tokens_input: int
    tokens_output: int
    cost: float
    calls: int


class FinOpsSummary(BaseModel):
    by_module: list[AggregateRow]
    by_model: list[AggregateRow]
    total_cost: float
    total_calls: int


class DimensionRow(BaseModel):
    bucket: str
    tokens_input: int
    tokens_output: int
    cost: float
    calls: int
    avg_latency_ms: float | None = None


# ---------- Orçamentos ----------


class BudgetCreateRequest(BaseModel):
    name: str = Field(min_length=1)
    scope_type: str  # 'global'|'module'|'model'|'user'|'domain'|'environment'|'agent'
    scope_value: str | None = None
    period: str = "monthly"  # 'daily'|'weekly'|'monthly'
    limit_brl: float = Field(ge=0)
    warning_threshold: float = Field(default=0.8, gt=0.0, le=1.0)
    hard_stop: bool = False
    notes: str | None = None


class BudgetUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1)
    limit_brl: float | None = Field(default=None, ge=0)
    warning_threshold: float | None = Field(default=None, gt=0.0, le=1.0)
    hard_stop: bool | None = None
    notes: str | None = None


class BudgetOut(BaseModel):
    id: str
    name: str
    scope_type: str
    scope_value: str | None
    period: str
    limit_brl: float
    warning_threshold: float
    hard_stop: bool
    notes: str | None = None


class BudgetStatusOut(BaseModel):
    budget: BudgetOut
    spent: float
    remaining: float
    pct_used: float
    severity: str  # 'ok'|'warning'|'critical'


class AlertOut(BaseModel):
    id: str
    budget_id: str
    severity: str
    cost_observed: float
    limit_reference: float
    triggered_at: datetime


class ImportRowError(BaseModel):
    row: int
    error: str


class ImportResultOut(BaseModel):
    imported: int
    errors: list[ImportRowError] = Field(default_factory=list)


# ---------- Políticas de modelo ----------


class PolicyUpsertRequest(BaseModel):
    model_config = _NO_PROTECTED_NS
    model_name: str = Field(min_length=1)
    risk_tier: str = "medium"
    value_tier: str = "medium"
    max_cost_per_call: float | None = Field(default=None, ge=0)
    max_tokens_per_call: int | None = Field(default=None, ge=0)
    allowed_features: list[str] | None = None
    rationale: str | None = None
    enabled: bool = True


class PolicyOut(BaseModel):
    model_config = _NO_PROTECTED_NS
    id: str
    model_name: str
    risk_tier: str
    value_tier: str
    max_cost_per_call: float | None
    max_tokens_per_call: int | None
    allowed_features: list[str] | None
    rationale: str | None
    enabled: bool


# ---------- Cost-aware routing ----------


class RouteCandidate(BaseModel):
    model_config = _NO_PROTECTED_NS
    model: str
    estimated_cost: float = Field(ge=0)


class RouteRequest(BaseModel):
    candidates: list[RouteCandidate]
    feature: str | None = None
    min_value_tier: str = "low"  # 'low'|'medium'|'high'


class RouteResponse(BaseModel):
    model_config = _NO_PROTECTED_NS
    model: str | None
    reason: str
    blocked: list[dict[str, Any]] = Field(default_factory=list)
