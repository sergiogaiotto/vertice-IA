"""Testes do AuthService."""

import pytest

from app.adapters.db.postgres import init_db
from app.adapters.db.repositories.user_repo import PgUserRepository
from app.core.services.auth_service import AuthService


@pytest.mark.asyncio
async def test_register_and_authenticate():
    await init_db()
    auth = AuthService(PgUserRepository())

    await auth.bootstrap_root("admin", "vertice2026")
    user = await auth.authenticate("admin", "vertice2026")
    assert user is not None
    assert user.username == "admin"
    assert "admin" in user.roles


@pytest.mark.asyncio
async def test_wrong_password_fails():
    await init_db()
    auth = AuthService(PgUserRepository())
    await auth.bootstrap_root("admin", "vertice2026")
    user = await auth.authenticate("admin", "errada")
    assert user is None


@pytest.mark.asyncio
async def test_token_roundtrip():
    await init_db()
    auth = AuthService(PgUserRepository())
    await auth.bootstrap_root("admin", "vertice2026")
    user = await auth.authenticate("admin", "vertice2026")
    token = auth.issue_token(user)
    assert token

    decoded = await auth.current_user(token)
    assert decoded is not None
    assert decoded.id == user.id
