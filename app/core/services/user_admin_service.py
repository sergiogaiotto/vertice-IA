"""Use case: administração de usuários (CRUD + roles + reset de senha)."""

from __future__ import annotations

import secrets
from uuid import UUID

from app.core.domain.entities import User, new_uuid
from app.core.ports.repositories import UserRepository
from app.core.services.auth_service import _hash, make_salt


class UserAdminService:
    """CRUD administrativo de usuários — separado do AuthService para clareza."""

    def __init__(self, users: UserRepository):
        self.users = users

    async def list_all(self) -> list[User]:
        return await self.users.list_all()

    async def get(self, user_id: UUID) -> User | None:
        return await self.users.get_by_id(user_id)

    async def create(
        self,
        username: str,
        password: str,
        roles: list[str] | None = None,
        full_name: str = "",
        email: str = "",
        phone: str = "",
        department: str = "",
        title: str = "",
    ) -> User:
        existing = await self.users.get_by_username(username)
        if existing:
            raise ValueError(f"usuário '{username}' já existe")
        salt = make_salt()
        user = User(
            id=new_uuid(),
            username=username,
            hashed_password=_hash(password, salt),
            salt=salt,
            full_name=full_name,
            email=email,
            phone=phone,
            department=department,
            title=title,
            is_active=True,
            roles=roles or ["analista_n3"],
        )
        return await self.users.create(user)

    async def update_roles(self, user_id: UUID, roles: list[str]) -> None:
        await self.users.set_roles(user_id, roles)

    async def set_active(self, user_id: UUID, active: bool) -> None:
        await self.users.set_active(user_id, active)

    async def reset_password(self, user_id: UUID) -> str:
        """Gera senha temporária aleatória e devolve em texto puro (única vez)."""
        new_password = secrets.token_urlsafe(12)
        salt = make_salt()
        await self.users.set_password(user_id, _hash(new_password, salt), salt)
        return new_password

    async def change_password(self, user_id: UUID, new_password: str) -> None:
        salt = make_salt()
        await self.users.set_password(user_id, _hash(new_password, salt), salt)

    async def delete(self, user_id: UUID) -> None:
        await self.users.delete(user_id)
