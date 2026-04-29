"""Schemas Pydantic do CRUD de Usuários."""

from __future__ import annotations

from pydantic import BaseModel


class CreateUserRequest(BaseModel):
    username: str
    password: str
    roles: list[str] = []


class UpdateRolesRequest(BaseModel):
    roles: list[str]


class UpdateActiveRequest(BaseModel):
    active: bool


class ChangePasswordRequest(BaseModel):
    new_password: str


class ResetPasswordResponse(BaseModel):
    user_id: str
    temporary_password: str


class UserDetail(BaseModel):
    id: str
    username: str
    roles: list[str]
    is_active: bool
    initials: str
    avatar_color: str
