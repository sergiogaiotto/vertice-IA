"""Testes RBAC dos endpoints de mutação.

Garante que o gate de papel está no BACKEND — i.e., um usuário com papel
``analista_n3`` recebe 403 ao tentar mutar recursos administrativos, mesmo
que tenha token válido. O gate antes vivia só nos templates (pages.py); a
API direta passava com qualquer usuário autenticado.

Critério usado em cada caso: chamar com analista → **403**. Chamar com
admin → **qualquer coisa, exceto 401/403** (pode ser 200/201/400/404/422
dependendo do payload — o ponto é que o gate liberou).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.adapters.db.postgres import init_db
from app.adapters.db.repositories.user_repo import PgUserRepository
from app.core.services.auth_service import AuthService
from app.main import app


_SKILLS_DIR = Path(__file__).resolve().parents[1] / "app" / "skills"


@pytest.fixture(autouse=True)
def _cleanup_filesystem_skill():
    """Skills moram no filesystem (não no Postgres). Quando o teste de admin
    cria um skill 'x' para provar que passou no gate, o arquivo persiste —
    limpa aqui pra não sujar o repo."""
    yield
    leftover = _SKILLS_DIR / "x.md"
    if leftover.exists():
        leftover.unlink()


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    """AsyncClient direto sobre o ASGI app — compartilha o mesmo event loop
    que os repositórios assíncronos, evitando colisão no pool asyncpg que o
    TestClient síncrono provoca em testes async.

    ``raise_app_exceptions=False`` faz com que exceções não-tratadas no app
    virem 500 normalmente (o que queremos pra teste de gate — bugs de payload
    em handlers downstream não devem mascarar o que estamos verificando aqui)."""
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as c:
        yield c


async def _make_user(username: str, roles: list[str]) -> str:
    """Cria usuário com `roles` e devolve um Bearer token JWT."""
    await init_db()
    auth = AuthService(PgUserRepository())
    user = await auth.register(username=username, password="vertice2026", roles=roles)
    return auth.issue_token(user)


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# Endpoints P0 e o gate esperado para cada um. Espelha pages.py:
#   /failsafe → admin/supervisor/finops
#   /modules, /prompts, /churn, /skills, /radar uploads → admin/supervisor
#   /finops budgets/policies → admin/supervisor/finops
_FAKE_UUID = "00000000-0000-0000-0000-000000000000"

_PROTECTED = [
    # ----- Failsafe (admin/supervisor/finops) -----
    ("POST",   "/api/failsafe/",                 {"module_name": "x", "description": "x", "payload": {}, "confidence": 0.5}),
    ("PATCH",  f"/api/failsafe/{_FAKE_UUID}",    {"description": "x"}),
    ("DELETE", f"/api/failsafe/{_FAKE_UUID}",    None),
    ("POST",   f"/api/failsafe/{_FAKE_UUID}/decide", {"approve": True}),
    # ----- Modules (admin/supervisor) -----
    ("POST",   "/api/modules/",                  {"name": "x", "endpoint_url": "/x", "description": "x", "config_params": {}}),
    ("PATCH",  f"/api/modules/{_FAKE_UUID}",     {"description": "y"}),
    ("POST",   f"/api/modules/{_FAKE_UUID}/pause",   None),
    ("POST",   f"/api/modules/{_FAKE_UUID}/resume",  None),
    ("DELETE", f"/api/modules/{_FAKE_UUID}",     None),
    # ----- Prompts (admin/supervisor) -----
    ("POST",   "/api/prompts/",                  {"name": "x", "input_guardrail": "", "system_prompt": "x", "output_guardrail": "", "module_names": ["radar"]}),
    ("POST",   f"/api/prompts/{_FAKE_UUID}/promote", None),
    ("PATCH",  f"/api/prompts/{_FAKE_UUID}/modules", {"module_names": ["radar"]}),
    ("DELETE", f"/api/prompts/{_FAKE_UUID}",     None),
    # ----- FinOps mutation (admin/supervisor/finops) -----
    ("POST",   "/api/finops/budgets",            {"name": "x", "scope_type": "global", "scope_value": None, "period": "monthly", "limit_brl": 100.0}),
    ("PATCH",  f"/api/finops/budgets/{_FAKE_UUID}", {"limit_brl": 200.0}),
    ("DELETE", f"/api/finops/budgets/{_FAKE_UUID}", None),
    ("POST",   "/api/finops/policies",           {"model_name": "x", "risk_tier": "low", "value_tier": "low"}),
    ("DELETE", f"/api/finops/policies/{_FAKE_UUID}", None),
    # ----- FinOps read (admin/supervisor/finops) — PR-2 -----
    ("GET",    "/api/finops/summary",            None),
    ("GET",    "/api/finops/by-dimension?dim=module", None),
    ("GET",    "/api/finops/budgets",            None),
    ("GET",    "/api/finops/policies",           None),
    ("GET",    "/api/finops/alerts",             None),
    # ----- Audit (admin/supervisor/finops) — PR-2 -----
    ("GET",    "/api/audit/",                    None),
    ("GET",    "/api/audit/stats",               None),
    ("GET",    f"/api/audit/{_FAKE_UUID}",       None),
    # ----- Churn (admin/supervisor) -----
    ("POST",   "/api/churn/nodes",               {"label": "x"}),
    ("PATCH",  f"/api/churn/nodes/{_FAKE_UUID}", {"label": "y"}),
    ("DELETE", f"/api/churn/nodes/{_FAKE_UUID}", None),
    # ----- Skills (admin/supervisor) -----
    ("POST",   "/api/skills/",                   {"name": "x", "content": "# x"}),
    ("PUT",    "/api/skills/some-name",          {"content": "# y"}),
    ("DELETE", "/api/skills/some-name",          None),
]


async def _do(client: AsyncClient, method: str, path: str, body, headers):
    if body is None:
        return await client.request(method, path, headers=headers)
    return await client.request(method, path, json=body, headers=headers)


@pytest.mark.asyncio
async def test_analista_403_em_endpoints_p0(client: AsyncClient):
    """analista_n3 não pode chamar nenhum dos endpoints de mutação P0."""
    token = await _make_user("an_rbac", ["analista_n3"])
    falhas = []
    for method, path, body in _PROTECTED:
        r = await _do(client, method, path, body, _h(token))
        if r.status_code != 403:
            falhas.append(f"{method} {path} → {r.status_code} (esperado 403)")
    assert not falhas, "Endpoints sem gate adequado:\n" + "\n".join(falhas)


@pytest.mark.asyncio
async def test_admin_passa_no_gate_dos_endpoints_p0(client: AsyncClient):
    """admin não pode receber 401/403 — passou no gate (mesmo se 4xx por payload)."""
    token = await _make_user("ad_rbac", ["admin"])
    falhas = []
    for method, path, body in _PROTECTED:
        r = await _do(client, method, path, body, _h(token))
        if r.status_code in (401, 403):
            falhas.append(f"{method} {path} → {r.status_code} (admin deveria passar)")
    assert not falhas, "Endpoints recusaram admin:\n" + "\n".join(falhas)


@pytest.mark.asyncio
async def test_sem_token_recebe_401(client: AsyncClient):
    """Sem Authorization, o gate de autenticação dispara antes do de papel."""
    falhas = []
    for method, path, body in _PROTECTED:
        r = await _do(client, method, path, body, headers={})
        # 401 sem token. (Alguns endpoints podem dar 422 se faltar query/body
        # parseado antes do dep — aceitamos isso desde que não seja 200/201.)
        if r.status_code not in (401, 422):
            falhas.append(f"{method} {path} → {r.status_code} (esperado 401)")
    assert not falhas, "Endpoints sem auth-gate:\n" + "\n".join(falhas)


# ---- Actor tracking em mudanças de visibility (PR-2) --------------------

@pytest.mark.asyncio
async def test_visibility_change_registra_actor(client: AsyncClient):
    """Mudança administrativa de visibility grava actor_id/username/timestamp.

    Sem isso, /admin/cards-em-tela mostra `previous_visibility` mas não diz
    QUEM fez o ato administrativo — gap de auditoria.
    """
    from app.adapters.db.repositories.radar_card_visibility_repo import (
        PgRadarCardVisibilityRepository,
    )

    await init_db()
    auth = AuthService(PgUserRepository())
    # admin que vai fazer a mudança
    admin = await auth.register(username="vis_admin", password="vertice2026", roles=["admin"])
    token = auth.issue_token(admin)
    # dono original do card
    owner = await auth.register(username="vis_owner", password="vertice2026", roles=["analista_n3"])

    repo = PgRadarCardVisibilityRepository()
    card_uid = "uid-test-vis-tracking"
    await repo.upsert(
        card_uid=card_uid,
        owner_id=str(owner.id),
        owner_username=owner.username,
        group_id=None, group_title=None,
        module_id=None, module_name=None, module_description=None,
        visibility="private",
        card_json={},
    )

    r = await client.put(
        f"/api/radar/cards/{card_uid}/visibility",
        json={"visibility": "public_lideranca"},
        headers=_h(token),
    )
    assert r.status_code == 200, r.text

    record = await repo.get(card_uid)
    assert record is not None
    assert record["visibility"] == "public_lideranca"
    assert record["previous_visibility"] == "private"
    assert record["visibility_changed_by_id"] == str(admin.id)
    assert record["visibility_changed_by_username"] == admin.username
    assert record["visibility_changed_at"] is not None


# ---- Radar uploads exigem multipart; testa apenas se gate de papel rejeita -

@pytest.mark.asyncio
async def test_radar_uploads_exigem_papel(client: AsyncClient):
    """analista_n3 não pode chamar /api/radar/upload-cases nem /upload-transcripts."""
    token = await _make_user("an_radar", ["analista_n3"])
    falhas = []
    for path in ("/api/radar/upload-cases", "/api/radar/upload-transcripts"):
        # Multipart com arquivo dummy — o gate de papel deve disparar antes do parse.
        r = await client.post(path, files={"file": ("dummy.xlsx", b"x")}, headers=_h(token))
        if r.status_code != 403:
            falhas.append(f"POST {path} → {r.status_code} (esperado 403)")
    assert not falhas, "Uploads radar sem gate:\n" + "\n".join(falhas)
