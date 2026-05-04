"""Schemas Pydantic do módulo Churn."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class NodeOut(BaseModel):
    id: str
    label: str
    parent_id: Optional[str] = None
    depth: int
    occurrences: int
    children: list["NodeOut"] = []


NodeOut.model_rebuild()


class CreateNodeRequest(BaseModel):
    label: str
    parent_id: Optional[str] = None


class RenameNodeRequest(BaseModel):
    label: str


class ClassifyRequest(BaseModel):
    contract_number: str
    transcript: str


class ClassificationOut(BaseModel):
    contract_number: str
    path: list[str]
    confidence: float
    rationale: str
