"""Smoke test de TODAS as páginas HTML do menu — uma rota por entry do
`nav_left.html`, para cada papel que tem acesso esperado.

Objetivo: garantir que nenhuma página retorne 500 quando o usuário com
permissão correta a acessa. É uma rede contra regressões do tipo "campo
datetime quebra tojson", "expressão Alpine acessa null", "novo guard
bloqueia papel que devia passar". Reproduz no CI o smoke manual que o
operador faria clicando em cada menu.

Cada rota é testada com:

  * **root** — deve retornar 2xx (corolário "root supremo"). O bypass de
    root em ``pages._require_any_role`` torna esse caso o mais estrito:
    se ele falha, ou o helper regrediu, ou a página explodiu na render.
  * **admin** — deve retornar 2xx para o conjunto de páginas onde admin
    é o papel base.
  * **analista_n3** — deve retornar 403 nas páginas restritas, e 2xx nas
    abertas (Cockpit, Funcionalidade).

NÃO testa o CONTEÚDO renderizado (lógica de negócio fica em outros
testes); só status + ausência de exceção propagada.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.adapters.db.postgres import init_db
from app.adapters.db.repositories.user_repo import PgUserRepository
from app.core.services.auth_service import AuthService
from app.main import app


# ---------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    """ASGI client direto — mesmo padrão de test_rbac.

    `raise_app_exceptions=False`: queremos OBSERVAR o status code do
    handler (incluindo 500), não receber uma exceção do TestClient. Sem
    isso, um bug de tojson explodiria como pytest.fail antes de chegarmos
    ao assert — perderíamos a oportunidade de reportar "esse handler
    retornou 500 e está quebrado".
    """
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as c:
        yield c


async def _token(username: str, roles: list[str], department: str = "") -> str:
    """Cria usuário e devolve um JWT — combina init_db (idempotente) +
    register + issue_token em uma chamada. Cada role pega seu próprio
    usuário pra evitar contaminação entre testes.
    """
    await init_db()
    auth = AuthService(PgUserRepository())
    user = await auth.register(
        username=username, password="vertice2026", roles=roles
    )
    if department:
        # Profile update via repo direto — auth.register não aceita dept.
        await PgUserRepository().set_profile(
            user.id, full_name="", email="", phone="",
            department=department, title="",
        )
        # Re-fetch para atualizar o objeto antes de issue_token (token
        # carrega snapshot dos roles, dept não é necessário no claim).
        user = await PgUserRepository().get_by_id(user.id)
    return auth.issue_token(user)


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------
# Catálogo de páginas — espelha nav_left.html. Cada tupla:
#   (path, label, accessible_by_role_set)
# Notar:
#   - 'root' bypassa TODOS os gates (acessa tudo).
#   - 'all' = qualquer autenticado, incluindo analista_n3.
# ---------------------------------------------------------------

_PAGES = [
    # (path,                     label,                allowed_roles)
    ("/",                        "Cockpit",            {"all"}),
    ("/radar",                   "Voz do Cliente",     {"all"}),
    ("/raiox",                   "Raio X Cliente",     {"all"}),
    ("/prompts",                 "Prompts",            {"admin", "supervisor"}),
    ("/skills",                  "Skills",             {"admin", "supervisor"}),
    ("/modules",                 "Módulos",            {"admin", "supervisor"}),
    ("/blocks",                  "Inventário",         {"admin", "supervisor"}),
    ("/finops",                  "FinOps",             {"admin", "supervisor", "finops"}),
    ("/failsafe",                "Failsafe",           {"admin", "supervisor", "finops"}),
    ("/audit",                   "Rastreabilidade",    {"admin", "supervisor", "finops"}),
    ("/users",                   "Usuários",           {"admin", "supervisor"}),
    ("/apis",                    "APIs",               {"admin"}),
    ("/gallery",                 "Galeria",            {"admin"}),
    ("/admin/cards-em-tela",     "Cards em tela",      {"admin", "supervisor"}),
]


# ---------------------------------------------------------------
# Tests
# ---------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,label,allowed",
    _PAGES,
    ids=[f"root-{p[0]}" for p in _PAGES],
)
async def test_root_acessa_qualquer_pagina(
    client: AsyncClient, path: str, label: str, allowed: set[str]
):
    """Root deve passar em TODAS as páginas (root supremacy).

    Cobre o bug em que páginas administrativas com `_require_any_role`
    listando só admin/supervisor bloqueavam root com 403, contradizendo
    a política "root tem todos os poderes". O fix está em
    `pages._require_any_role`: bypass automático quando 'root' está nos
    roles do user.
    """
    token = await _token(f"root_{label.replace(' ', '_').replace('/', '')[:20]}", ["root"])
    r = await client.get(path, headers=_h(token))
    assert r.status_code < 400, (
        f"root recebeu {r.status_code} em {path} ({label}) — esperado 2xx. "
        f"Resposta: {r.text[:300]}"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,label,allowed",
    _PAGES,
    ids=[f"admin-{p[0]}" for p in _PAGES],
)
async def test_admin_acessa_paginas_permitidas(
    client: AsyncClient, path: str, label: str, allowed: set[str]
):
    """Admin acessa todas as páginas onde 'admin' está em ``allowed`` (ou
    em ``all``). Páginas restritas a 'finops' só (sem admin) NÃO existem
    no catálogo atual, mas o test é resiliente a isso.
    """
    if "admin" not in allowed and "all" not in allowed:
        pytest.skip(f"{path} não é destinada a admin")
    token = await _token(f"admin_{label.replace(' ', '_').replace('/', '')[:20]}", ["admin"])
    r = await client.get(path, headers=_h(token))
    assert r.status_code < 400, (
        f"admin recebeu {r.status_code} em {path} ({label}). "
        f"Resposta: {r.text[:300]}"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,label,allowed",
    [p for p in _PAGES if "all" not in p[2]],
    ids=[f"analista-{p[0]}" for p in _PAGES if "all" not in p[2]],
)
async def test_analista_403_em_paginas_administrativas(
    client: AsyncClient, path: str, label: str, allowed: set[str]
):
    """analista_n3 toma 403 em TODA página restrita.

    Páginas "all" (Cockpit, Funcionalidade) são testadas no caso `_all`
    abaixo. Aqui só verificamos que o gate funciona — não 500, não 200.
    """
    token = await _token(f"an_{label.replace(' ', '_').replace('/', '')[:20]}", ["analista_n3"])
    r = await client.get(path, headers=_h(token))
    assert r.status_code == 403, (
        f"analista_n3 deveria tomar 403 em {path} ({label}), recebeu {r.status_code}"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,label,allowed",
    [p for p in _PAGES if "all" in p[2]],
    ids=[f"all-{p[0]}" for p in _PAGES if "all" in p[2]],
)
async def test_analista_acessa_paginas_publicas(
    client: AsyncClient, path: str, label: str, allowed: set[str]
):
    """Páginas marcadas 'all' são acessíveis a analista_n3 também (modo
    leitura para Voz do Cliente e Raio X)."""
    token = await _token(f"an_pub_{label.replace(' ', '_').replace('/', '')[:20]}", ["analista_n3"])
    r = await client.get(path, headers=_h(token))
    assert r.status_code < 400, (
        f"analista_n3 deveria acessar {path} ({label}), recebeu {r.status_code}. "
        f"Resposta: {r.text[:300]}"
    )


# ---------------------------------------------------------------
# Tests focados em bugs encontrados — regressão guard
# ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_cards_em_tela_com_datetime_serializa_ok(client: AsyncClient):
    """Regressão: /admin/cards-em-tela retornava 500 quando um card tinha
    `visibility_changed_at` preenchido (datetime não é serializável por
    json.dumps puro; tojson do Jinja explodia).

    Cenário: cria um card via PUT /api/radar/state como admin, torna público
    (popula visibility_changed_at), depois acessa /admin/cards-em-tela.
    """
    from app.adapters.db.repositories.radar_card_visibility_repo import (
        PgRadarCardVisibilityRepository,
    )
    admin_token = await _token("adm_dt", ["admin"], department="suporte")

    # 1. Cria card via state PUT
    card_uid = "dt-regression-card-001"
    state = [
        {
            "id": "g-1",
            "title": "g",
            "cards": [
                {
                    "uid": card_uid,
                    "module_id": "mod-x",
                    "module_name": "x",
                    "module_description": "",
                    "visibility": "private",
                }
            ],
        }
    ]
    r = await client.put(
        "/api/radar/state",
        json={"state": state, "expected_version": None},
        headers=_h(admin_token),
    )
    assert r.status_code == 200, r.text

    # 2. Torna público — popula visibility_changed_at
    r = await client.put(
        f"/api/radar/cards/{card_uid}/visibility",
        json={"visibility": "public_lideranca"},
        headers=_h(admin_token),
    )
    assert r.status_code == 200, r.text

    # Confirma que o campo problemático está populado no repo
    repo = PgRadarCardVisibilityRepository()
    rec = await repo.get(card_uid)
    assert rec is not None
    assert rec["visibility_changed_at"] is not None, (
        "preparação do teste falhou: visibility_changed_at deveria estar populado"
    )

    # 3. Acessa /admin/cards-em-tela — DEVE renderizar (200), não 500
    r = await client.get("/admin/cards-em-tela", headers=_h(admin_token))
    assert r.status_code == 200, (
        f"/admin/cards-em-tela falhou com {r.status_code} quando havia card com "
        f"visibility_changed_at populado — provável regressão no datetime → ISO. "
        f"Resposta: {r.text[:300]}"
    )


@pytest.mark.asyncio
async def test_root_bypassa_gate_de_admin_supervisor(client: AsyncClient):
    """Regressão: root tomava 403 em /prompts, /apis, etc., porque os gates
    listavam só ['admin', 'supervisor']. O bypass automático em
    `_require_any_role` resolveu — este teste garante que o bypass continua
    valendo.
    """
    token = await _token("root_bypass", ["root"])
    # Página com gate restrito (admin only)
    r = await client.get("/apis", headers=_h(token))
    assert r.status_code == 200, (
        f"root foi bloqueado em /apis com {r.status_code} — root bypass quebrou. "
        f"Resposta: {r.text[:300]}"
    )
    # Outra página com gate ['admin', 'supervisor']
    r = await client.get("/prompts", headers=_h(token))
    assert r.status_code == 200, (
        f"root foi bloqueado em /prompts com {r.status_code} — root bypass quebrou."
    )
