"""Testes da matriz "Funcionalidades por Perfil".

Cobre:
  - Service ``can_access``: políticas de matching (root bypass, default
    allow, deny explícito, wildcard de dept, mais específico vence).
  - Endpoints ``/api/access/*``: gates (admin read-only, só root edita)
    e shape de resposta.
  - Page guards em ``/radar`` e ``/raiox``: usuário sem permissão na
    matriz toma 403 mesmo se o role normalmente passaria.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.adapters.db.postgres import init_db
from app.adapters.db.repositories.feature_access_repo import (
    PgFeatureAccessRepository,
)
from app.adapters.db.repositories.user_repo import PgUserRepository
from app.core.services.auth_service import AuthService
from app.core.services.feature_access_service import FeatureAccessService
from app.main import app


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as c:
        yield c


async def _make_user(
    username: str, roles: list[str], department: str = ""
) -> tuple[str, str]:
    """Cria usuário + (opcionalmente) cadastra dept. Devolve (id, token)."""
    await init_db()
    auth = AuthService(PgUserRepository())
    user = await auth.register(
        username=username, password="vertice2026", roles=roles
    )
    if department:
        await PgUserRepository().set_profile(
            user.id, full_name="", email="", phone="",
            department=department, title="",
        )
    return str(user.id), auth.issue_token(user)


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ============================================================
# Service: políticas de matching
# ============================================================


@pytest.mark.asyncio
async def test_can_access_root_bypassa_tudo():
    """Root sempre acessa, mesmo com regra deny explícita.

    Garante o invariante "root tem todos os poderes": nenhuma regra
    administrativa consegue barrar root.
    """
    await init_db()
    svc = FeatureAccessService()
    # Cria regra deny pra TODO mundo na feature radar
    await svc.set_rule(role="root", department="", feature_key="radar", access=False)
    # Mesmo assim, user com role 'root' acessa (bypass acontece ANTES da consulta)
    assert await svc.can_access(["root"], "", "radar") is True
    assert await svc.can_access(["root"], "qualquer_dept", "radar") is True


@pytest.mark.asyncio
async def test_can_access_default_allow_sem_regra():
    """Sem regra cadastrada → ALLOW para usuário com algum role.

    Migrate-safe: instalações existentes não perdem acesso no primeiro
    deploy. Usuário SEM role (caso de borda — auth_service nem emite
    token, mas o service não assume isso) retorna DENY: ausência de role
    é tratada como ausência de identidade autorizada.
    """
    await init_db()
    svc = FeatureAccessService()
    # Banco vazio (TRUNCATE entre testes) — qualquer role com regra
    # não-cadastrada passa.
    assert await svc.can_access(["analista_n3"], "vendas", "radar") is True
    assert await svc.can_access(["supervisor"], "", "raiox") is True
    assert await svc.can_access(["analista_n1"], "qualquer_dept", "radar") is True
    # Sem nenhum role → DENY (segurança: ausência de identidade não cria acesso)
    assert await svc.can_access([], "", "radar") is False


@pytest.mark.asyncio
async def test_can_access_deny_explicito_para_role_dept():
    """Regra deny específica para (role, dept, feature) bloqueia."""
    await init_db()
    svc = FeatureAccessService()
    await svc.set_rule(
        role="analista_n3", department="vendas", feature_key="raiox", access=False
    )
    assert await svc.can_access(["analista_n3"], "vendas", "raiox") is False
    # Outro dept não é afetado
    assert await svc.can_access(["analista_n3"], "suporte", "raiox") is True
    # Outra feature não é afetada
    assert await svc.can_access(["analista_n3"], "vendas", "radar") is True
    # Outro role não é afetado
    assert await svc.can_access(["supervisor"], "vendas", "raiox") is True


@pytest.mark.asyncio
async def test_can_access_wildcard_dept_aplica_a_todos():
    """Regra com `department=''` é wildcard — vale pra qualquer dept."""
    await init_db()
    svc = FeatureAccessService()
    await svc.set_rule(
        role="analista_n3", department="", feature_key="raiox", access=False
    )
    assert await svc.can_access(["analista_n3"], "vendas", "raiox") is False
    assert await svc.can_access(["analista_n3"], "suporte", "raiox") is False
    assert await svc.can_access(["analista_n3"], "", "raiox") is False


@pytest.mark.asyncio
async def test_can_access_dept_especifico_vence_wildcard():
    """Mais específico vence: regra (analista_n3, vendas, raiox, ALLOW)
    sobrepõe regra (analista_n3, '', raiox, DENY) — analista_n3 em vendas
    acessa, em outro dept não.
    """
    await init_db()
    svc = FeatureAccessService()
    await svc.set_rule(
        role="analista_n3", department="", feature_key="raiox", access=False
    )
    await svc.set_rule(
        role="analista_n3", department="vendas", feature_key="raiox", access=True
    )
    # dept exato bate primeiro → ALLOW
    assert await svc.can_access(["analista_n3"], "vendas", "raiox") is True
    # dept não-vendas → cai no wildcard → DENY
    assert await svc.can_access(["analista_n3"], "suporte", "raiox") is False


@pytest.mark.asyncio
async def test_can_access_remove_rule_volta_ao_default():
    """Após remove_rule, comportamento volta ao default (ALLOW)."""
    await init_db()
    svc = FeatureAccessService()
    await svc.set_rule(
        role="analista_n3", department="", feature_key="raiox", access=False
    )
    assert await svc.can_access(["analista_n3"], "x", "raiox") is False
    await svc.remove_rule(role="analista_n3", department="", feature_key="raiox")
    assert await svc.can_access(["analista_n3"], "x", "raiox") is True


@pytest.mark.asyncio
async def test_can_access_feature_fora_do_catalogo_sempre_passa():
    """`can_access` para feature NÃO controlada retorna True — esse
    helper só fala sobre features que estão na matriz."""
    await init_db()
    svc = FeatureAccessService()
    # Mesmo com role/dept restritos, feature_key fora de CONTROLLED_FEATURES
    # passa direto. Page guards de outras telas usam role gate, não este helper.
    assert await svc.can_access(["analista_n3"], "vendas", "feature_inexistente") is True


# ============================================================
# Endpoints — gates
# ============================================================


@pytest.mark.asyncio
async def test_get_matrix_admin_e_root_veem(client: AsyncClient):
    """admin e root acessam GET /api/access/matrix."""
    for role in ("admin", "root"):
        _, tok = await _make_user(f"matrix_view_{role}", [role])
        r = await client.get("/api/access/matrix", headers=_h(tok))
        assert r.status_code == 200, f"{role}: {r.text}"
        body = r.json()
        assert "features" in body
        assert "rules" in body
        assert "can_edit" in body


@pytest.mark.asyncio
async def test_get_matrix_admin_can_edit_false(client: AsyncClient):
    """admin recebe `can_edit=False` — flag pro frontend desabilitar UI."""
    _, tok = await _make_user("matrix_admin_ro", ["admin"])
    r = await client.get("/api/access/matrix", headers=_h(tok))
    assert r.status_code == 200
    assert r.json()["can_edit"] is False


@pytest.mark.asyncio
async def test_get_matrix_root_can_edit_true(client: AsyncClient):
    """root recebe `can_edit=True`."""
    _, tok = await _make_user("matrix_root_rw", ["root"])
    r = await client.get("/api/access/matrix", headers=_h(tok))
    assert r.status_code == 200
    assert r.json()["can_edit"] is True


@pytest.mark.asyncio
async def test_get_matrix_supervisor_403(client: AsyncClient):
    """supervisor não vê a matriz — admin/root only."""
    _, tok = await _make_user("matrix_sup", ["supervisor"])
    r = await client.get("/api/access/matrix", headers=_h(tok))
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_get_matrix_analista_403(client: AsyncClient):
    _, tok = await _make_user("matrix_an", ["analista_n3"])
    r = await client.get("/api/access/matrix", headers=_h(tok))
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_put_rule_admin_403(client: AsyncClient):
    """admin NÃO pode editar — só visualizar."""
    _, tok = await _make_user("rule_admin", ["admin"])
    r = await client.put(
        "/api/access/rule",
        json={"role": "analista_n3", "department": "", "feature_key": "radar", "access": False},
        headers=_h(tok),
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_put_rule_root_cria(client: AsyncClient):
    _, tok = await _make_user("rule_root", ["root"])
    r = await client.put(
        "/api/access/rule",
        json={"role": "analista_n3", "department": "vendas", "feature_key": "raiox", "access": False},
        headers=_h(tok),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["rule"]["role"] == "analista_n3"
    assert body["rule"]["access"] is False


@pytest.mark.asyncio
async def test_put_rule_root_atualiza_existente(client: AsyncClient):
    """Segundo PUT com mesma (role, dept, feature) atualiza access."""
    _, tok = await _make_user("rule_root_upd", ["root"])
    payload = {"role": "analista_n3", "department": "", "feature_key": "radar", "access": False}
    r = await client.put("/api/access/rule", json=payload, headers=_h(tok))
    assert r.status_code == 200

    payload["access"] = True
    r = await client.put("/api/access/rule", json=payload, headers=_h(tok))
    assert r.status_code == 200
    assert r.json()["rule"]["access"] is True


@pytest.mark.asyncio
async def test_put_rule_feature_inexistente_400(client: AsyncClient):
    """feature_key fora de CONTROLLED_FEATURES é rejeitada."""
    _, tok = await _make_user("rule_bad_feat", ["root"])
    r = await client.put(
        "/api/access/rule",
        json={"role": "x", "department": "", "feature_key": "feature_inexistente", "access": True},
        headers=_h(tok),
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_delete_rule_root_remove(client: AsyncClient):
    _, tok = await _make_user("rule_del_root", ["root"])
    # cria
    payload = {"role": "analista_n3", "department": "", "feature_key": "raiox", "access": False}
    await client.put("/api/access/rule", json=payload, headers=_h(tok))
    # remove
    r = await client.request(
        "DELETE", "/api/access/rule",
        json={"role": "analista_n3", "department": "", "feature_key": "raiox"},
        headers=_h(tok),
    )
    assert r.status_code == 200
    assert r.json()["removed"] is True


@pytest.mark.asyncio
async def test_delete_rule_admin_403(client: AsyncClient):
    _, tok = await _make_user("rule_del_admin", ["admin"])
    r = await client.request(
        "DELETE", "/api/access/rule",
        json={"role": "x", "department": "", "feature_key": "radar"},
        headers=_h(tok),
    )
    assert r.status_code == 403


# ============================================================
# Page guards — matriz bloqueia /radar e /raiox
# ============================================================


@pytest.mark.asyncio
async def test_radar_page_bloqueado_por_matriz(client: AsyncClient):
    """analista_n3 com regra deny explícita toma 403 em /radar mesmo
    sendo papel autenticado (que normalmente acessa funcionalidades
    "all"). Confirma que a matriz tem prioridade sobre role gate de
    nav_left."""
    # Cria regra deny pra todos analistas_n3 em radar
    svc = FeatureAccessService()
    await svc.set_rule(role="analista_n3", department="", feature_key="radar", access=False)

    _, tok = await _make_user("radar_blocked", ["analista_n3"], department="x")
    r = await client.get("/radar", headers=_h(tok))
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_radar_page_root_bypassa_matriz(client: AsyncClient):
    """Root sempre acessa, mesmo com regra deny. Página /radar testada
    porque o bypass acontece em duas camadas (can_access + nav)."""
    svc = FeatureAccessService()
    await svc.set_rule(role="root", department="", feature_key="radar", access=False)

    _, tok = await _make_user("radar_root_bypass", ["root"])
    r = await client.get("/radar", headers=_h(tok))
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_raiox_page_dept_especifico_bloqueia(client: AsyncClient):
    """Regra (analista_n3, vendas, raiox, DENY) só bloqueia analistas
    em vendas. Em outros depts continua passando (default allow)."""
    svc = FeatureAccessService()
    await svc.set_rule(
        role="analista_n3", department="vendas", feature_key="raiox", access=False
    )
    _, tok_vendas = await _make_user("raiox_vendas", ["analista_n3"], department="vendas")
    r = await client.get("/raiox", headers=_h(tok_vendas))
    assert r.status_code == 403

    _, tok_suporte = await _make_user("raiox_suporte", ["analista_n3"], department="suporte")
    r = await client.get("/raiox", headers=_h(tok_suporte))
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_access_page_admin_acessa_e_root_tambem(client: AsyncClient):
    """A própria tela /access é acessível a admin/root (admin em
    read-only, root edita)."""
    _, tok_admin = await _make_user("access_pg_admin", ["admin"])
    r = await client.get("/access", headers=_h(tok_admin))
    assert r.status_code == 200

    _, tok_root = await _make_user("access_pg_root", ["root"])
    r = await client.get("/access", headers=_h(tok_root))
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_access_page_analista_403(client: AsyncClient):
    """analista_n3 não acessa /access."""
    _, tok = await _make_user("access_pg_an", ["analista_n3"])
    r = await client.get("/access", headers=_h(tok))
    assert r.status_code == 403
