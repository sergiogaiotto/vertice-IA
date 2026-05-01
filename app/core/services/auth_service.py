"""Serviço de autenticação e RBAC."""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta
from uuid import UUID

from jose import JWTError, jwt

from app.config import get_settings
from app.core.domain.entities import User, new_uuid
from app.core.ports.repositories import UserRepository

settings = get_settings()


def _hash(password: str, salt: str) -> str:
    return hashlib.sha256((salt + password).encode("utf-8")).hexdigest()


def make_salt() -> str:
    return secrets.token_hex(16)


class AuthService:
    def __init__(self, users: UserRepository):
        self.users = users

    async def register(self, username: str, password: str, roles: list[str] | None = None) -> User:
        salt = make_salt()
        user = User(
            id=new_uuid(),
            username=username,
            hashed_password=_hash(password, salt),
            salt=salt,
            is_active=True,
            roles=roles or ["analista_n3"],
        )
        return await self.users.create(user)

    async def has_any_user(self) -> bool:
        return (await self.users.count_users()) > 0

    async def bootstrap_root(self, username: str, password: str) -> User:
        if await self.has_any_user():
            raise ValueError("bootstrap inicial indisponível")
        return await self.register(username=username, password=password, roles=["root", "admin"])

    async def authenticate(self, username: str, password: str) -> User | None:
        user = await self.users.get_by_username(username)
        if not user or not user.is_active:
            return None
        if _hash(password, user.salt) != user.hashed_password:
            return None
        return user

    def issue_token(self, user: User) -> str:
        exp = datetime.utcnow() + timedelta(minutes=settings.jwt_expires_minutes)
        payload = {
            "sub": str(user.id),
            "username": user.username,
            "roles": user.roles,
            "exp": exp,
        }
        return jwt.encode(payload, settings.app_secret_key, algorithm=settings.jwt_algorithm)

    def decode_token(self, token: str) -> dict | None:
        try:
            return jwt.decode(token, settings.app_secret_key, algorithms=[settings.jwt_algorithm])
        except JWTError:
            return None

    async def current_user(self, token: str) -> User | None:
        data = self.decode_token(token)
        if not data:
            return None
        try:
            uid = UUID(data["sub"])
        except (KeyError, ValueError):
            return None
        return await self.users.get_by_id(uid)
