"""Pydantic schemas do Raio X Cliente."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


# ---- Boards ----

class BoardOut(BaseModel):
    id: UUID
    name: str
    description: str = ""
    owner_id: str | None = None
    is_shared: bool = True
    layout: dict[str, Any] = Field(default_factory=dict)
    filters: dict[str, Any] = Field(default_factory=dict)
    cover_emoji: str = "🩻"
    created_at: datetime
    updated_at: datetime


class BoardCreate(BaseModel):
    name: str
    description: str = ""
    is_shared: bool = True
    cover_emoji: str = "🩻"

    @field_validator("name")
    @classmethod
    def _name_required(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("name é obrigatório")
        if len(v) > 120:
            raise ValueError("name muito longo (máx 120)")
        return v


class BoardUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    layout: dict[str, Any] | None = None
    filters: dict[str, Any] | None = None
    is_shared: bool | None = None


# ---- Charts ----

class FilterPredicate(BaseModel):
    column: str
    op: str = "="
    value: str | int | float | bool | None = None


class JoinSpec(BaseModel):
    """Join entre duas tabelas. Whitelistado contra raiox_relationships."""
    from_table: str
    from_column: str
    to_table: str
    to_column: str


class QuerySpec(BaseModel):
    """Especificação de série para 1 chart.

    - F0: table + label_col + agg + value_col opcional.
    - F1: + filters (crossfilter + globais) + joins (whitelist).
    """
    table: str
    label_column: str
    value_column: str = ""
    aggregate: str = "count"  # 'count'|'sum'|'avg'|'min'|'max'|'none'
    order_by: str = "value_desc"  # 'value_desc'|'value_asc'|'label_asc'|'label_desc'
    limit: int = 30
    filters: list[FilterPredicate] = Field(default_factory=list)
    joins: list[JoinSpec] = Field(default_factory=list)


class ChartIn(BaseModel):
    chart_type: str  # 'bar'|'line'|'scatter'|'pie'|'histogram'|'box'
    title: str = ""
    position_row: int = 0
    position_col: int = 0
    span_cols: int = 1
    span_rows: int = 1
    query_spec: QuerySpec
    plotly_config: dict[str, Any] = Field(default_factory=dict)


class ChartUpdate(BaseModel):
    chart_type: str | None = None
    title: str | None = None
    position_row: int | None = None
    position_col: int | None = None
    span_cols: int | None = None
    span_rows: int | None = None
    query_spec: QuerySpec | None = None
    plotly_config: dict[str, Any] | None = None


class ChartOut(BaseModel):
    id: UUID
    board_id: UUID
    chart_type: str
    title: str = ""
    position_row: int
    position_col: int
    span_cols: int
    span_rows: int
    query_spec: dict[str, Any]
    plotly_config: dict[str, Any]
    created_by_ai: bool = False
    created_at: datetime
    updated_at: datetime


# ---- Relationships ----

class RelationshipOut(BaseModel):
    id: UUID
    table_a: str
    column_a: str
    table_b: str
    column_b: str
    kind: str = "one_to_many"
    confidence: float = 0.0
    confirmed_by_user: str | None = None
    confirmed_at: datetime | None = None


class RelationshipIn(BaseModel):
    table_a: str
    column_a: str
    table_b: str
    column_b: str
    kind: str = "one_to_many"


# ---- Series (resultado de query) ----

class SeriesOut(BaseModel):
    labels: list[str]
    values: list[float]
    aggregate: str
    label_column: str
    value_column: str
    total_rows: int
    rows_returned: int
