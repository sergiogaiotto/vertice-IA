"""Router de autenticação."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm

from app.api.deps import get_auth_service, require_user
from app.api.schemas.auth import TokenResponse, UserOut
from app.config import get_settings
from app.core.domain.entities import User
from app.core.services.auth_service import AuthService

router = APIRouter()
settings = get_settings()


@router.post("/login", response_model=TokenResponse)
async def login(
    request: Request,
    form: OAuth2PasswordRequestForm = Depends(),
    auth: AuthService = Depends(get_auth_service),
):
    user = await auth.authenticate(form.username, form.password)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="credenciais inválidas")
    token = auth.issue_token(user)
    request.session["token"] = token
    request.session["username"] = user.username
    return TokenResponse(access_token=token, expires_in=settings.jwt_expires_minutes * 60)


@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(require_user)):
    return UserOut(id=str(user.id), username=user.username, roles=user.roles, is_active=user.is_active)
