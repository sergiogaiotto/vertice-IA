"""Router HTTP da matriz "Funcionalidades por Perfil".

Endpoints:
  GET    /api/access/matrix        — devolve matriz inteira (admin/root vê)
  GET    /api/access/features      — devolve catálogo de features controláveis
  PUT    /api/access/rule          — cria/atualiza regra (SÓ root)
  DELETE /api/access/rule          — remove regra (SÓ root)

Política (espelha a doutrina geral do projeto):
  - root:  vê matriz e EDITA (criar/remover regras).
  - admin: vê matriz, mas NÃO edita (read-only). Frontend desabilita controles.
  - demais: 403 em qualquer endpoint deste router.

Por que admin é read-only e não tem permissão de edit? A política de
acesso é decisão de governança — root é o ator supremo que define
política. Admin executa operação dentro da política. Mesma lógica que
"só root pode criar root user" — admin não escala privilégios.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import require_user
from app.core.domain.entities import User
from app.core.services.feature_access_service import (
    CONTROLLED_FEATURES,
    FeatureAccessService,
)

router = APIRouter()


# ---------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------


class FeatureAccessRule(BaseModel):
    """Uma linha da matriz, serializada para JSON. Datetimes viram ISO
    strings via Pydantic default; o frontend faz `new Date(...)` em cima.
    """
    model_config = ConfigDict(from_attributes=True)

    id: str | None = None
    role: str
    department: str = ""
    feature_key: str
    access: bool
    created_by_id: str | None = None
    created_by_username: str | None = None
    updated_by_id: str | None = None
    updated_by_username: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class SetRuleRequest(BaseModel):
    role: str = Field(..., min_length=1)
    department: str = ""
    feature_key: str = Field(..., min_length=1)
    access: bool


class DeleteRuleRequest(BaseModel):
    role: str = Field(..., min_length=1)
    department: str = ""
    feature_key: str = Field(..., min_length=1)


# ---------------------------------------------------------------
# Policy helpers
# ---------------------------------------------------------------


def _is_root(user: User) -> bool:
    return "root" in (user.roles or [])


def _is_admin_or_root(user: User) -> bool:
    roles = user.roles or []
    return "root" in roles or "admin" in roles


def _assert_can_view_matrix(user: User) -> None:
    if not _is_admin_or_root(user):
        raise HTTPException(
            403, "apenas admin/root pode visualizar a matriz de acesso"
        )


def _assert_can_edit_matrix(user: User) -> None:
    if not _is_root(user):
        raise HTTPException(
            403, "apenas root pode editar a matriz de acesso"
        )


# ---------------------------------------------------------------
# Helpers de serialização
# ---------------------------------------------------------------


def _rule_to_dto(row: dict) -> dict:
    """Converte datetime → ISO string para evitar erro de tojson na
    galaxia Pydantic / Jinja consumers. Também garante que IDs UUIDs
    venham como str.
    """
    out = dict(row)
    for k in ("created_at", "updated_at"):
        ts = out.get(k)
        if hasattr(ts, "isoformat"):
            out[k] = ts.isoformat()
    if out.get("id") is not None and not isinstance(out["id"], str):
        out["id"] = str(out["id"])
    return out


# ---------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------


def _svc() -> FeatureAccessService:
    return FeatureAccessService()


@router.get("/matrix")
async def get_matrix(user: User = Depends(require_user)):
    """Devolve a matriz inteira + catálogo de features para o frontend
    renderizar a grid."""
    _assert_can_view_matrix(user)
    svc = _svc()
    rules = await svc.list_matrix()
    return {
        "features": list(CONTROLLED_FEATURES),
        "rules": [_rule_to_dto(r) for r in rules],
        "can_edit": _is_root(user),
    }


@router.get("/features")
async def get_features(user: User = Depends(require_user)):
    """Catálogo de feature_keys controláveis. Lista pequena e estática,
    mas exposta via API para o frontend não precisar duplicar."""
    _assert_can_view_matrix(user)
    return {"features": list(CONTROLLED_FEATURES)}


@router.put("/rule")
async def put_rule(
    body: SetRuleRequest,
    user: User = Depends(require_user),
):
    """Cria ou atualiza uma regra. UNIQUE (role, dept, feature) faz com
    que segundo PUT no mesmo trio atualize `access` em vez de duplicar.
    Apenas root."""
    _assert_can_edit_matrix(user)
    svc = _svc()
    try:
        rule = await svc.set_rule(
            role=body.role,
            department=body.department,
            feature_key=body.feature_key,
            access=body.access,
            actor_id=str(user.id),
            actor_username=user.username,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "rule": _rule_to_dto(rule)}


@router.delete("/rule")
async def delete_rule(
    body: DeleteRuleRequest,
    user: User = Depends(require_user),
):
    """Remove regra → volta ao default (allow). Idempotente: deletar
    regra inexistente retorna ``removed=False`` mas não erra."""
    _assert_can_edit_matrix(user)
    svc = _svc()
    removed = await svc.remove_rule(
        role=body.role,
        department=body.department,
        feature_key=body.feature_key,
    )
    return {"ok": True, "removed": removed}
