"""Router HTTP do CRUD de Usuários.

Política de autorização (resumo executável — ver helpers abaixo):

  root:        tudo. Único papel que pode adicionar/remover 'root' e que
               pode mexer em outro usuário root.
  admin:       lista/cria/atualiza/desativa/exclui qualquer usuário, EXCETO:
               não atribui 'root', não mexe em usuários que já têm 'root',
               não altera/remove o papel 'root' de ninguém.
  supervisor:  só lista/cria/atualiza/desativa usuários cujas roles são
               EXCLUSIVAMENTE analista_n* (n1/n2/n3) E department igual ao
               do próprio supervisor. Não exclui. Precisa de department
               preenchido (caso contrário 403 com tooltip pedindo cadastro).
  demais:      403.

Os helpers `_authority`, `_assert_can_view`, `_assert_can_manage` e
`_assert_can_assign_roles` centralizam essas regras. Cada endpoint chama o
helper apropriado — evita drift entre rotas e mantém auditoria fácil.
"""

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


# ---------------------------------------------------------------
# Policy helpers
# ---------------------------------------------------------------


def _roles(u: User) -> set[str]:
    return set(u.roles or [])


def _is_root(u: User) -> bool:
    return "root" in _roles(u)


def _is_admin(u: User) -> bool:
    # admin é "true admin" só se NÃO for root (root tem mais poder e ramo
    # próprio nas decisões de policy). Aqui o teste é "tem o role admin".
    return "admin" in _roles(u)


def _is_supervisor(u: User) -> bool:
    return "supervisor" in _roles(u)


def _is_analista_only(roles: list[str] | set[str]) -> bool:
    """True se o set de roles é EXCLUSIVAMENTE analista_n* (sem outro role)."""
    rset = set(roles or [])
    if not rset:
        return False
    return all(r.startswith("analista_") for r in rset)


def _authority(u: User) -> str | None:
    """Identifica o nível de autoridade administrativa do usuário.

    Ordem de precedência: root > admin > supervisor > None. Quem tem
    múltiplos papéis (raro, mas possível) cai no de maior privilégio.
    """
    if _is_root(u):
        return "root"
    if _is_admin(u):
        return "admin"
    if _is_supervisor(u):
        return "supervisor"
    return None


def _assert_can_view(actor: User) -> str:
    """Gate de leitura para /api/users. Retorna a autoridade do actor.

    Levanta 403 se o usuário não tem papel administrativo. Usado por list,
    get, list_departments.
    """
    auth = _authority(actor)
    if not auth:
        raise HTTPException(403, "permissão negada")
    return auth


def _assert_can_manage(actor: User, target: User, *, allow_delete: bool = False) -> None:
    """Gate de mutação para um usuário-alvo específico.

    Aplica a matriz:
      root:        passa em tudo.
      admin:       passa, exceto se target tem role root.
      supervisor:  passa só se target é analista_n* puro E mesmo dept;
                   se ``allow_delete=True`` ainda assim bloqueia (supervisor
                   não exclui).
      demais:      403.

    Levanta HTTPException(403) com mensagem em PT-BR quando bloqueia.
    """
    auth = _authority(actor)
    if not auth:
        raise HTTPException(403, "permissão negada")

    if auth == "root":
        return

    target_roles = _roles(target)

    if auth == "admin":
        if "root" in target_roles:
            raise HTTPException(403, "apenas root pode gerenciar usuários root")
        return

    if auth == "supervisor":
        if allow_delete:
            raise HTTPException(403, "supervisor não pode excluir usuários")
        if not _is_analista_only(target_roles):
            raise HTTPException(
                403, "supervisor só gerencia usuários com papel analista_n*"
            )
        actor_dept = (actor.department or "").strip()
        if not actor_dept:
            raise HTTPException(
                403,
                "cadastre seu departamento em /usuarios antes de gerenciar usuários",
            )
        target_dept = (target.department or "").strip()
        if target_dept != actor_dept:
            raise HTTPException(
                403,
                "supervisor só gerencia analistas do próprio departamento",
            )
        return

    # safety net
    raise HTTPException(403, "permissão negada")


def _assert_can_assign_roles(
    actor: User,
    new_roles: list[str],
    *,
    existing_roles: list[str] | None = None,
) -> None:
    """Valida que o conjunto de roles propostos é atribuível pelo actor.

    Regras:
      - Modificar (add/remove) o papel 'root' exige actor.role = root.
      - admin pode atribuir qualquer outro role exceto 'root'.
      - supervisor só pode atribuir roles analista_n*.
      - Outros: 403.

    `existing_roles` (opcional) é usado em UPDATEs: a diff entre antigo e
    novo determina se houve mudança envolvendo 'root'. Em CREATEs deixe
    None (= antigo vazio).
    """
    actor_set = _roles(actor)
    new_set = set(new_roles or [])
    old_set = set(existing_roles or [])
    role_diff = new_set.symmetric_difference(old_set)

    # 'root' só entra/sai por mão de root
    if "root" in role_diff and "root" not in actor_set:
        raise HTTPException(
            403, "apenas root pode adicionar ou remover o papel 'root'"
        )

    if "root" in actor_set:
        return  # root atribui qualquer papel

    if "admin" in actor_set:
        # admin já está protegido contra 'root' pelo check acima
        return

    if "supervisor" in actor_set:
        if not new_set:
            raise HTTPException(403, "supervisor deve atribuir ao menos um papel analista_n*")
        if not all(r.startswith("analista_") for r in new_set):
            raise HTTPException(403, "supervisor só atribui papéis analista_n*")
        return

    raise HTTPException(403, "permissão negada")


def _assert_can_create_with(actor: User, *, target_roles: list[str], target_department: str) -> None:
    """Gate específico de POST /api/users. Combina:
      - autoridade (admin/supervisor/root)
      - regra de role (via _assert_can_assign_roles)
      - regra de dept (supervisor: target_dept == actor.dept)
    """
    auth = _authority(actor)
    if not auth:
        raise HTTPException(403, "permissão negada")

    _assert_can_assign_roles(actor, target_roles, existing_roles=None)

    if auth == "supervisor":
        actor_dept = (actor.department or "").strip()
        if not actor_dept:
            raise HTTPException(
                403,
                "cadastre seu departamento em /usuarios antes de criar usuários",
            )
        if (target_department or "").strip() != actor_dept:
            raise HTTPException(
                403,
                "supervisor só cria usuários no próprio departamento",
            )


# ---------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------


@router.get("/", response_model=list[UserDetail])
async def list_users(
    svc: UserAdminService = Depends(get_user_admin_service),
    user: User = Depends(require_user),
):
    """Lista usuários visíveis ao actor.

    - root / admin: lista TODOS.
    - supervisor:   lista só usuários analista_n* do MESMO departamento.
                    Sem dept preenchido, devolve lista vazia (não é erro
                    para não quebrar a tela, mas a UI ainda mostra o aviso
                    pra cadastrar dept).
    """
    auth = _assert_can_view(user)
    all_users = await svc.list_all()

    if auth == "supervisor":
        actor_dept = (user.department or "").strip()
        if not actor_dept:
            return []
        all_users = [
            u for u in all_users
            if _is_analista_only(u.roles)
            and (u.department or "").strip() == actor_dept
        ]

    return [_to_detail(u) for u in all_users]


@router.get("/departments")
async def list_departments(user: User = Depends(require_user)):
    """Lista distinct dos departamentos cadastrados em users — alimenta o
    combobox dos modais Novo/Editar usuário (input + datalist HTML5).

    Visibilidade: admin/supervisor/root. Supervisor recebe só o próprio
    dept (não precisa "ver" outros para criar/editar dentro do seu).
    """
    auth = _assert_can_view(user)
    from app.adapters.db.postgres import connect
    async with connect() as db:
        rows = await db.fetch(
            "SELECT DISTINCT department FROM users "
            "WHERE department IS NOT NULL AND department <> '' "
            "ORDER BY department"
        )
    depts = [r["department"] for r in rows]
    if auth == "supervisor":
        actor_dept = (user.department or "").strip()
        return [actor_dept] if actor_dept and actor_dept in depts else ([actor_dept] if actor_dept else [])
    return depts


@router.get("/{user_id}", response_model=UserDetail)
async def get_user(
    user_id: UUID,
    svc: UserAdminService = Depends(get_user_admin_service),
    user: User = Depends(require_user),
):
    _assert_can_view(user)
    u = await svc.get(user_id)
    if not u:
        raise HTTPException(404, "usuário não encontrado")
    # Supervisor só "vê" usuários gerenciáveis — 403 ao tentar acessar
    # alguém fora do dept ou que não é analista_n*.
    _assert_can_manage(user, u)
    return _to_detail(u)


@router.post("/", response_model=UserDetail, status_code=201)
async def create_user(
    body: CreateUserRequest,
    svc: UserAdminService = Depends(get_user_admin_service),
    user: User = Depends(require_user),
):
    new_roles = body.roles or ["analista_n3"]
    new_dept = body.department or ""
    _assert_can_create_with(user, target_roles=new_roles, target_department=new_dept)
    try:
        u = await svc.create(
            body.username,
            body.password,
            new_roles,
            full_name=body.full_name,
            email=body.email,
            phone=body.phone,
            department=new_dept,
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
    target = await svc.get(user_id)
    if not target:
        raise HTTPException(404, "usuário não encontrado")
    _assert_can_manage(user, target)
    _assert_can_assign_roles(user, body.roles, existing_roles=target.roles)
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
    target = await svc.get(user_id)
    if not target:
        raise HTTPException(404, "usuário não encontrado")
    _assert_can_manage(user, target)
    # Supervisor não pode mover usuário pra OUTRO dept (gerência só dentro
    # do próprio). Se tentar trocar dept, bloqueia.
    if _authority(user) == "supervisor":
        actor_dept = (user.department or "").strip()
        new_dept = (body.department or "").strip()
        if new_dept != actor_dept:
            raise HTTPException(
                403,
                "supervisor não pode mover usuário para outro departamento",
            )
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
    target = await svc.get(user_id)
    if not target:
        raise HTTPException(404, "usuário não encontrado")
    _assert_can_manage(user, target)
    # Trava extra: ninguém desativa a si mesmo (evita "lockout" acidental).
    if str(user.id) == str(user_id) and not body.active:
        raise HTTPException(400, "não é permitido desativar o próprio usuário")
    await svc.set_active(user_id, body.active)
    u = await svc.get(user_id)
    return _to_detail(u)


@router.post("/{user_id}/reset-password", response_model=ResetPasswordResponse)
async def reset_password(
    user_id: UUID,
    svc: UserAdminService = Depends(get_user_admin_service),
    user: User = Depends(require_user),
):
    target = await svc.get(user_id)
    if not target:
        raise HTTPException(404, "usuário não encontrado")
    _assert_can_manage(user, target)
    pwd = await svc.reset_password(user_id)
    return ResetPasswordResponse(user_id=str(user_id), temporary_password=pwd)


@router.post("/{user_id}/password")
async def change_password(
    user_id: UUID,
    body: ChangePasswordRequest,
    svc: UserAdminService = Depends(get_user_admin_service),
    user: User = Depends(require_user),
):
    """Troca de senha. Caminhos:

      - usuário troca a PRÓPRIA senha: sempre permitido (independente de role)
      - actor admin/supervisor/root troca senha de alheio: aplica _assert_can_manage
    """
    if str(user.id) == str(user_id):
        await svc.change_password(user_id, body.new_password)
        return {"ok": True}
    target = await svc.get(user_id)
    if not target:
        raise HTTPException(404, "usuário não encontrado")
    _assert_can_manage(user, target)
    await svc.change_password(user_id, body.new_password)
    return {"ok": True}


@router.delete("/{user_id}")
async def delete_user(
    user_id: UUID,
    svc: UserAdminService = Depends(get_user_admin_service),
    user: User = Depends(require_user),
):
    target = await svc.get(user_id)
    if not target:
        raise HTTPException(404, "usuário não encontrado")
    if str(user.id) == str(user_id):
        raise HTTPException(400, "não é permitido excluir o próprio usuário")
    _assert_can_manage(user, target, allow_delete=True)
    await svc.delete(user_id)
    return {"ok": True}
