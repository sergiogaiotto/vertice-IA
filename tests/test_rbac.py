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


# ---- Anti-leak E2E: cenário completo reportado pelo usuário -------------

@pytest.mark.asyncio
async def test_e2e_cards_nao_vazam_entre_usuarios_no_mesmo_browser(client: AsyncClient):
    """Reproduz o bug reportado: sergio.gaiotto adiciona card; analista
    loga no mesmo browser; o cliente do analista (com localStorage residual,
    ou maliciosamente) tenta gravar o card do sergio no próprio state.

    O servidor deve:
      - Filtrar o UID alheio antes de salvar o state_json do analista.
      - Não atualizar `radar_card_visibility` — dono continua sendo sergio.

    Resultado esperado:
      - sergio.gaiotto continua dono em /admin/cards-em-tela.
      - state_json do analista NÃO contém o card do sergio (próximo GET
        /api/radar/state retorna sem ele).
    """
    from app.adapters.db.repositories.radar_card_visibility_repo import (
        PgRadarCardVisibilityRepository,
    )
    from app.adapters.db.repositories.radar_state_repo import (
        PgRadarStateRepository,
    )

    await init_db()
    auth = AuthService(PgUserRepository())
    sergio = await auth.register(
        username="sergio.gaiotto", password="vertice2026", roles=["admin"]
    )
    analista = await auth.register(
        username="analista", password="vertice2026", roles=["analista_n3"]
    )
    sergio_token = auth.issue_token(sergio)
    analista_token = auth.issue_token(analista)

    # ----- Sergio adiciona um card via PUT /api/radar/state ------------
    card_uid = "card-sergio-001"
    sergio_state = [
        {
            "id": "grp-1",
            "title": "Análise principal",
            "cards": [
                {
                    "uid": card_uid,
                    "module_id": "mod-radar",
                    "module_name": "radar_intent",
                    "module_description": "Análise de intenção",
                    "visibility": "private",
                }
            ],
        }
    ]
    r = await client.put(
        "/api/radar/state",
        json={"state": sergio_state, "expected_version": None},
        headers=_h(sergio_token),
    )
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True

    # Confirma sidecar: sergio é o dono.
    vis_repo = PgRadarCardVisibilityRepository()
    record = await vis_repo.get(card_uid)
    assert record is not None
    assert record["owner_id"] == str(sergio.id)
    assert record["owner_username"] == "sergio.gaiotto"

    # ----- Analista loga e o cliente (bugado / com localStorage residual)
    # tenta fazer PUT com o card do sergio incluído -----------------------
    analista_state = [
        {
            "id": "grp-A",
            "title": "Análise principal",
            "cards": [
                # Card RESIDUAL do sergio que o cliente do analista mandou
                {
                    "uid": card_uid,
                    "module_id": "mod-radar",
                    "module_name": "radar_intent",
                    "visibility": "private",
                },
                # Card legítimo do analista
                {
                    "uid": "card-analista-001",
                    "module_id": "mod-radar",
                    "module_name": "radar_intent",
                    "visibility": "private",
                },
            ],
        }
    ]
    r = await client.put(
        "/api/radar/state",
        json={"state": analista_state, "expected_version": None},
        headers=_h(analista_token),
    )
    assert r.status_code == 200, r.text

    # ----- Asserts da defesa ------------------------------------------

    # 1) Sidecar do sergio intacto.
    record = await vis_repo.get(card_uid)
    assert record["owner_id"] == str(sergio.id), \
        "owner_id do card do sergio foi sobrescrito"
    assert record["owner_username"] == "sergio.gaiotto", \
        "owner_username do card do sergio foi sobrescrito (bug original)"

    # 2) GET /api/radar/state do analista NÃO devolve o card do sergio.
    r = await client.get("/api/radar/state", headers=_h(analista_token))
    assert r.status_code == 200
    body = r.json()
    all_uids = [
        c.get("uid")
        for g in (body.get("state") or [])
        for c in (g.get("cards") or [])
    ]
    assert card_uid not in all_uids, \
        f"vazamento: card do sergio apareceu no state do analista: {all_uids}"
    # Mas o card legítimo do analista PRECISA estar lá.
    assert "card-analista-001" in all_uids

    # 3) state_json persistido também não contém o card alheio.
    state_repo = PgRadarStateRepository()
    rec = await state_repo.get(str(analista.id))
    import json as _json
    saved = _json.loads(rec["state_json"])
    saved_uids = [
        c.get("uid") for g in saved for c in (g.get("cards") or [])
        if isinstance(c, dict)
    ]
    assert card_uid not in saved_uids
    assert "card-analista-001" in saved_uids

    # 4) /admin/cards via /api/radar/admin/cards mostra sergio.gaiotto
    #    como dono — não analista.
    admin_token = sergio_token  # sergio é admin
    r = await client.get("/api/radar/admin/cards", headers=_h(admin_token))
    assert r.status_code == 200
    items = r.json()["items"]
    target = [i for i in items if i["card_uid"] == card_uid]
    assert len(target) == 1
    assert target[0]["owner_username"] == "sergio.gaiotto"


# ---- Anti-leak: upsert NÃO sobrescreve dono entre usuários --------------

@pytest.mark.asyncio
async def test_upsert_rejeita_cross_user(client: AsyncClient):
    """Quando user A cria um card e user B tenta upsert do mesmo UID, o repo
    rejeita silenciosamente (retorna False) e o `owner_*` continua sendo A.

    Bug original: dois usuários compartilhando o mesmo browser; o segundo
    fazia PUT /api/radar/state contendo cards do primeiro (residuais em
    localStorage). O sync_owner_cards chamava upsert que executava
    ``ON CONFLICT DO UPDATE SET owner_username = EXCLUDED.owner_username`` —
    `owner_id` ficava inalterado mas `owner_username` virava o invasor.
    """
    from app.adapters.db.repositories.radar_card_visibility_repo import (
        PgRadarCardVisibilityRepository,
    )

    await init_db()
    auth = AuthService(PgUserRepository())
    a = await auth.register("leak_a", "vertice2026", roles=["analista_n3"])
    b = await auth.register("leak_b", "vertice2026", roles=["analista_n3"])

    repo = PgRadarCardVisibilityRepository()
    card_uid = "uid-leak-test"

    # A cria o card.
    ok_a = await repo.upsert(
        card_uid=card_uid, owner_id=str(a.id), owner_username=a.username,
        group_id=None, group_title=None,
        module_id=None, module_name=None, module_description=None,
        visibility="private", card_json={"x": 1},
    )
    assert ok_a is True

    # B tenta upsert (simula vazamento via localStorage compartilhado).
    ok_b = await repo.upsert(
        card_uid=card_uid, owner_id=str(b.id), owner_username=b.username,
        group_id=None, group_title=None,
        module_id=None, module_name=None, module_description=None,
        visibility="private", card_json={"x": 2},
    )
    assert ok_b is False, "upsert cross-user deveria ser rejeitado"

    # Estado no DB: ainda é de A, sem corrupção.
    record = await repo.get(card_uid)
    assert record["owner_id"] == str(a.id)
    assert record["owner_username"] == a.username
    assert record["created_by_id"] == str(a.id)
    assert record["card_json"] == {"x": 1}, "card_json de A não pode ser tocado"


# ---- Preferências do Radar persistidas no Postgres (PR-3) ---------------

@pytest.mark.asyncio
async def test_radar_preferences_roundtrip(client: AsyncClient):
    """GET retorna {} para usuário novo; PUT mescla; PUT com null remove.

    Substitui os 3 keys de localStorage (lastSelectedCase, lastAutoRunTx,
    lastChatCase) por persistência cross-device no banco.
    """
    token = await _make_user("prefs_user", ["analista_n3"])

    # Estado inicial: dict vazio (usuário sem registro em radar_user_state).
    r = await client.get("/api/radar/preferences", headers=_h(token))
    assert r.status_code == 200
    assert r.json() == {}

    # PUT inicial — grava 2 chaves.
    r = await client.put(
        "/api/radar/preferences",
        json={"lastSelectedCase": "123", "lastAutoRunTx": "tx-abc"},
        headers=_h(token),
    )
    assert r.status_code == 200
    assert r.json() == {"lastSelectedCase": "123", "lastAutoRunTx": "tx-abc"}

    # Merge — adiciona uma terceira sem perder as anteriores.
    r = await client.put(
        "/api/radar/preferences",
        json={"lastChatCase": "123"},
        headers=_h(token),
    )
    assert r.json() == {
        "lastSelectedCase": "123",
        "lastAutoRunTx": "tx-abc",
        "lastChatCase": "123",
    }

    # null remove só a chave alvo.
    r = await client.put(
        "/api/radar/preferences",
        json={"lastAutoRunTx": None},
        headers=_h(token),
    )
    assert r.json() == {"lastSelectedCase": "123", "lastChatCase": "123"}

    # GET reflete o estado final (round-trip pelo banco).
    r = await client.get("/api/radar/preferences", headers=_h(token))
    assert r.json() == {"lastSelectedCase": "123", "lastChatCase": "123"}


@pytest.mark.asyncio
async def test_radar_preferences_isolated_por_usuario(client: AsyncClient):
    """Cada usuário enxerga só suas próprias preferências."""
    token_a = await _make_user("prefs_a", ["analista_n3"])
    token_b = await _make_user("prefs_b", ["analista_n3"])

    await client.put(
        "/api/radar/preferences", json={"lastSelectedCase": "A"}, headers=_h(token_a)
    )
    await client.put(
        "/api/radar/preferences", json={"lastSelectedCase": "B"}, headers=_h(token_b)
    )

    r_a = await client.get("/api/radar/preferences", headers=_h(token_a))
    r_b = await client.get("/api/radar/preferences", headers=_h(token_b))
    assert r_a.json() == {"lastSelectedCase": "A"}
    assert r_b.json() == {"lastSelectedCase": "B"}


# ---- Actor tracking em mudança de DONO (admin/cards/{uid}/owner) ---------

@pytest.mark.asyncio
async def test_change_owner_registra_actor(client: AsyncClient):
    """Transferência administrativa de dono grava actor_id/username/at.

    Simétrico ao tracking de visibility — fecha gap de auditoria onde
    /admin/cards-em-tela mudava o dono sem registrar quem.
    """
    from app.adapters.db.repositories.radar_card_visibility_repo import (
        PgRadarCardVisibilityRepository,
    )

    await init_db()
    auth = AuthService(PgUserRepository())
    admin = await auth.register("owner_admin", "vertice2026", roles=["admin"])
    old_owner = await auth.register("owner_orig", "vertice2026", roles=["analista_n3"])
    new_owner = await auth.register("owner_dest", "vertice2026", roles=["analista_n3"])
    token = auth.issue_token(admin)

    repo = PgRadarCardVisibilityRepository()
    card_uid = "uid-change-owner-test"
    await repo.upsert(
        card_uid=card_uid,
        owner_id=str(old_owner.id), owner_username=old_owner.username,
        group_id=None, group_title=None,
        module_id=None, module_name=None, module_description=None,
        visibility="private", card_json={"uid": card_uid},
    )

    r = await client.put(
        f"/api/radar/admin/cards/{card_uid}/owner",
        json={"new_owner_username": new_owner.username},
        headers=_h(token),
    )
    assert r.status_code == 200, r.text

    record = await repo.get(card_uid)
    assert record["owner_id"] == str(new_owner.id)
    assert record["owner_username"] == new_owner.username
    # created_by preservado
    assert record["created_by_id"] == str(old_owner.id)
    # actor da transferência
    assert record["owner_changed_by_id"] == str(admin.id)
    assert record["owner_changed_by_username"] == admin.username
    assert record["owner_changed_at"] is not None


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
