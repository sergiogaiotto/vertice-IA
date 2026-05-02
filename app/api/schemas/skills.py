"""Schemas Pydantic do CRUD de Skills."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class SkillSummary(BaseModel):
    name: str
    title: str
    path: str
    sections: list[str]
    updated_at: datetime
    size_bytes: int


class SkillDetail(BaseModel):
    name: str
    title: str
    path: str
    content: str
    sections: dict[str, str]
    updated_at: datetime
    size_bytes: int


class SaveSkillRequest(BaseModel):
    content: str
    new_name: str | None = None


class CreateSkillRequest(BaseModel):
    name: str
    content: str | None = None
