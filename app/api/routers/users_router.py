"""Router HTTP do CRUD de Usuários."""

from __future__ import annotations

import hashlib
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import get_user_admin_service, require_user
from app.api.schemas.users import (
    ChangePasswordRequest,
    CreateUserRequest,
    ResetPasswordResponse,
    UpdateActiveRequest,
    UpdateProfileRequest,
    UpdateRolesRequest,
    UserDetail,
)
from app.core.domain.entities import User
from app.core.services.user_admin_service import UserAdminService

router = APIRouter()

# Paleta de cores determinísticas para avatares
_AVATAR_COLORS = ["#DC2626", "#534AB7", "#185FA5", "#993C1D", "#993556", "#854F0B", "#3B6D11"]


def _avatar_color(username: str) -> str:
    h = int(hashlib.md5(username.encode()).hexdigest(), 16)
    return _AVATAR_COLORS[h % len(_AVATAR_COLORS)]


def _initials(username: str) -> str:
    parts = username.replace(".", " ").replace("_", " ").split()
    if not parts:
        return "??"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def _to_detail(u: User) -> UserDetail:
    return UserDetail(
        id=str(u.id),
        username=u.username,
        full_name=u.full_name,
        email=u.email,
        phone=u.phone,
        department=u.department,
        title=u.title,
        roles=u.roles,
        is_active=u.is_active,
        initials=_initials(u.username),
        avatar_color=_avatar_color(u.username),
    )


def _require_admin(user: User) -> None:
    if "admin" not in user.roles and "root" not in user.roles:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="apenas admin")


@router.get("/", response_model=list[UserDetail])
async def list_users(
    svc: UserAdminService = Depends(get_user_admin_service),
    user: User = Depends(require_user),
):
    _require_admin(user)
    return [_to_detail(u) for u in await svc.list_all()]


@router.get("/departments")
async def list_departments(user: User = Depends(require_user)):
    """Lista distinct dos departamentos cadastrados em users — alimenta o
    combobox dos modais Novo/Editar usuário (input + datalist HTML5).
    Aceita texto livre na UI; este endpoint só sugere os existentes."""
    _require_admin(user)
    from app.adapters.db.sqlite import connect
    async with connect() as db:
        cur = await db.execute(
            "SELECT DISTINCT department FROM users "
            "WHERE department IS NOT NULL AND department != '' "
            "ORDER BY department"
        )
        rows = await cur.fetchall()
    return [r[0] for r in rows]


@router.get("/{user_id}", response_model=UserDetail)
async def get_user(
    user_id: UUID,
    svc: UserAdminService = Depends(get_user_admin_service),
    user: User = Depends(require_user),
):
    _require_admin(user)
    u = await svc.get(user_id)
    if not u:
        raise HTTPException(404, "usuário não encontrado")
    return _to_detail(u)


@router.post("/", response_model=UserDetail, status_code=201)
async def create_user(
    body: CreateUserRequest,
    svc: UserAdminService = Depends(get_user_admin_service),
    user: User = Depends(require_user),
):
    _require_admin(user)
    try:
        u = await svc.create(
            body.username,
            body.password,
            body.roles or None,
            full_name=body.full_name,
            email=body.email,
            phone=body.phone,
            department=body.department,
            title=body.title,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _to_detail(u)


@router.patch("/{user_id}/roles", response_model=UserDetail)
async def update_roles(
    user_id: UUID,
    body: UpdateRolesRequest,
    svc: UserAdminService = Depends(get_user_admin_service),
    user: User = Depends(require_user),
):
    _require_admin(user)
    await svc.update_roles(user_id, body.roles)
    u = await svc.get(user_id)
    return _to_detail(u)


@router.patch("/{user_id}/profile", response_model=UserDetail)
async def update_profile(
    user_id: UUID,
    body: UpdateProfileRequest,
    svc: UserAdminService = Depends(get_user_admin_service),
    user: User = Depends(require_user),
):
    """Atualiza nome, email, telefone, departamento, cargo de um usuário."""
    _require_admin(user)
    await svc.update_profile(
        user_id,
        full_name=body.full_name,
        email=body.email,
        phone=body.phone,
        department=body.department,
        title=body.title,
    )
    u = await svc.get(user_id)
    return _to_detail(u)


@router.patch("/{user_id}/active", response_model=UserDetail)
async def set_active(
    user_id: UUID,
    body: UpdateActiveRequest,
    svc: UserAdminService = Depends(get_user_admin_service),
    user: User = Depends(require_user),
):
    _require_admin(user)
    await svc.set_active(user_id, body.active)
    u = await svc.get(user_id)
    return _to_detail(u)


@router.post("/{user_id}/reset-password", response_model=ResetPasswordResponse)
async def reset_password(
    user_id: UUID,
    svc: UserAdminService = Depends(get_user_admin_service),
    user: User = Depends(require_user),
):
    _require_admin(user)
    pwd = await svc.reset_password(user_id)
    return ResetPasswordResponse(user_id=str(user_id), temporary_password=pwd)


@router.post("/{user_id}/password")
async def change_password(
    user_id: UUID,
    body: ChangePasswordRequest,
    svc: UserAdminService = Depends(get_user_admin_service),
    user: User = Depends(require_user),
):
    # usuário pode trocar a própria senha; admin pode trocar de qualquer um
    if str(user.id) != str(user_id) and "admin" not in user.roles and "root" not in user.roles:
        raise HTTPException(403, "permissão negada")
    await svc.change_password(user_id, body.new_password)
    return {"ok": True}


@router.delete("/{user_id}")
async def delete_user(
    user_id: UUID,
    svc: UserAdminService = Depends(get_user_admin_service),
    user: User = Depends(require_user),
):
    _require_admin(user)
    if str(user.id) == str(user_id):
        raise HTTPException(400, "não é permitido excluir o próprio usuário")
    await svc.delete(user_id)
    return {"ok": True}
