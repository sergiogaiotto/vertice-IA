"""Testes exaustivos de Administrativo > API (API Endpoints).

Cobertura:

  * RBAC: admin vs analista vs anônimo em cada método
  * CRUD round-trip via HTTP + via service direto
  * Validação: campos obrigatórios, defaults, UNIQUE, 404
  * Filtros: ?only_active + ordenação
  * Endpoint /{id}/test: mock de httpx + auditoria em api_calls
  * Padrões: método normalizado (upper), JSONB headers, GET vs POST body

Mock de HTTP: monkeypatch em `httpx.AsyncClient.request`, devolve uma
resposta canned. Evita dependência de rede e de httpbin externo.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.adapters.db.postgres import connect, init_db
from app.adapters.db.repositories.user_repo import PgUserRepository
from app.core.services.api_endpoint_service import (
    ApiEndpointService,
    get_api_endpoint_service,
)
from app.core.services.auth_service import AuthService
from app.main import app


# ============================================================
# Fixtures e helpers
# ============================================================


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    """AsyncClient direto sobre o ASGI — mesmo pattern de test_rbac.py."""
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as c:
        yield c


async def _make_user(username: str, roles: list[str]) -> str:
    """Cria usuário + devolve Bearer token. Idempotente: re-registra com sufixo
    se já existir."""
    await init_db()
    auth = AuthService(PgUserRepository())
    user = await auth.register(username=username, password="vertice2026", roles=roles)
    return auth.issue_token(user)


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def admin_token() -> str:
    return await _make_user("ep_admin", ["admin"])


@pytest_asyncio.fixture
async def analista_token() -> str:
    return await _make_user("ep_analista", ["analista_n3"])


@pytest_asyncio.fixture
async def supervisor_token() -> str:
    # supervisor NÃO tem permissão (router checa especificamente "admin").
    return await _make_user("ep_supervisor", ["supervisor"])


def _new_endpoint_payload(name: str | None = None, **overrides) -> dict:
    """Payload válido para POST. `name` único por chamada via uuid hex curto."""
    base = {
        "name": name or f"ep_{uuid.uuid4().hex[:8]}",
        "url": "https://httpbin.example.com/post",
        "method": "POST",
        "description": "endpoint de teste",
        "headers": {"X-Test": "1"},
        "timeout_seconds": 15,
    }
    base.update(overrides)
    return base


# ============================================================
# Mock de httpx para o endpoint /test
# ============================================================


class _MockResponse:
    """Stand-in mínimo para httpx.Response — só o que `service.call()` usa."""

    def __init__(self, status_code: int, json_data=None, text: str = ""):
        self.status_code = status_code
        self._json = json_data
        self.text = text

    def json(self):
        if self._json is None:
            raise ValueError("not json")
        return self._json


def _patch_httpx(monkeypatch, response_or_exc, capture: dict | None = None):
    """Substitui httpx.AsyncClient.request por um stub.

    `response_or_exc` pode ser uma `_MockResponse` (devolvida) ou uma
    `Exception` (levantada). `capture`, se passado, recebe os kwargs da
    chamada (`method`, `url`, `json`, `params`, `headers`, `timeout`).

    *Bypass importante*: o pytest usa `AsyncClient(transport=ASGITransport(app))`
    pra chamar o nosso FastAPI in-process — esse client TAMBÉM é httpx.AsyncClient.
    Se patchassemos cegamente, qualquer `c.post('/api/...')` do teste viraria a
    mock response (quebrando setup de fixtures). Por isso só interceptamos URLs
    EXTERNAS (não-`http://test`).
    """
    original_request = httpx.AsyncClient.request

    async def fake_request(self, method, url, **kwargs):
        url_str = str(url)
        # O test client passa URL como path relativo (`/api/...`) — não tem
        # `https://`. O service.call passa URL absoluta (`https://srv.example...`).
        # Esse predicado isola o mock ao service sem tocar o tráfego ASGI.
        is_external = url_str.startswith(("http://", "https://")) and not url_str.startswith("http://test")
        if not is_external:
            return await original_request(self, method, url, **kwargs)
        if capture is not None:
            capture["method"] = method
            capture["url"] = url_str
            capture["json"] = kwargs.get("json")
            capture["params"] = kwargs.get("params")
            capture["headers"] = kwargs.get("headers")
            capture["timeout"] = getattr(self, "timeout", None)
        if isinstance(response_or_exc, Exception):
            raise response_or_exc
        return response_or_exc

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)


# ============================================================
# A. Service direto (unit) — independente de HTTP/RBAC
# ============================================================


@pytest.mark.asyncio
async def test_service_create_round_trip():
    svc = ApiEndpointService()
    ep = await svc.create(
        name="svc_direct_1",
        url="https://example.com/x",
        method="get",  # propositalmente minúsculo — deve virar upper
        description="d",
        headers={"k": "v"},
        timeout_seconds=20,
        created_by_user="tester",
    )
    assert ep is not None
    assert ep.name == "svc_direct_1"
    assert ep.method == "GET"
    assert ep.headers == {"k": "v"}
    assert ep.timeout_seconds == 20
    assert ep.is_active is True
    assert ep.created_by_user == "tester"

    got = await svc.get(ep.id)
    assert got is not None
    assert got.id == ep.id


@pytest.mark.asyncio
async def test_service_get_inexistente():
    svc = ApiEndpointService()
    assert await svc.get(str(uuid.uuid4())) is None


@pytest.mark.asyncio
async def test_service_update_inexistente_retorna_none():
    svc = ApiEndpointService()
    # update num id inexistente: a UPDATE não-encontra retorna 0 rows;
    # `get()` subsequente devolve None.
    result = await svc.update(
        endpoint_id=str(uuid.uuid4()),
        name="x", url="https://x.com", method="POST",
        description="", headers={}, timeout_seconds=30, is_active=True,
    )
    assert result is None


@pytest.mark.asyncio
async def test_service_list_filtra_inativos():
    svc = ApiEndpointService()
    active = await svc.create(name="lst_active", url="https://x.com", method="GET")
    inactive = await svc.create(name="lst_inactive", url="https://x.com", method="GET")
    await svc.update(
        endpoint_id=inactive.id, name=inactive.name, url=inactive.url,
        method=inactive.method, description="", headers={},
        timeout_seconds=30, is_active=False,
    )
    all_ = await svc.list_all(only_active=False)
    only_active = await svc.list_all(only_active=True)
    names_all = {e.name for e in all_}
    names_act = {e.name for e in only_active}
    assert "lst_active" in names_all and "lst_inactive" in names_all
    assert "lst_active" in names_act and "lst_inactive" not in names_act


@pytest.mark.asyncio
async def test_service_list_ordenada_por_nome():
    svc = ApiEndpointService()
    # Cria em ordem reversa pra garantir que ORDER BY name está vigente.
    await svc.create(name="ord_z", url="https://x.com", method="GET")
    await svc.create(name="ord_a", url="https://x.com", method="GET")
    await svc.create(name="ord_m", url="https://x.com", method="GET")
    rows = await svc.list_all()
    names = [e.name for e in rows if e.name.startswith("ord_")]
    assert names == sorted(names)


@pytest.mark.asyncio
async def test_service_delete():
    svc = ApiEndpointService()
    ep = await svc.create(name="to_del", url="https://x.com", method="GET")
    await svc.delete(ep.id)
    assert await svc.get(ep.id) is None


@pytest.mark.asyncio
async def test_service_create_nome_duplicado_falha():
    """UNIQUE(name) — segunda inserção deve levantar."""
    svc = ApiEndpointService()
    await svc.create(name="dup_name", url="https://x.com", method="GET")
    with pytest.raises(Exception):
        await svc.create(name="dup_name", url="https://y.com", method="POST")


@pytest.mark.asyncio
async def test_service_headers_jsonb_aceita_dict_complexo():
    """Headers viram JSONB — qualquer dict serializável deve sobreviver round-trip."""
    svc = ApiEndpointService()
    headers = {
        "Authorization": "Bearer abc",
        "X-Trace-Id": "trace-123",
        "Content-Type": "application/json",
    }
    ep = await svc.create(
        name="hdr_test", url="https://x.com", method="POST", headers=headers,
    )
    got = await svc.get(ep.id)
    assert got.headers == headers


# ============================================================
# B. RBAC via HTTP
# ============================================================


@pytest.mark.asyncio
async def test_rbac_sem_token_401(client: AsyncClient):
    """Sem Authorization, gate dispara antes do handler."""
    fake_id = str(uuid.uuid4())
    checks = [
        ("GET", "/api/api-endpoints/", None),
        ("GET", f"/api/api-endpoints/{fake_id}", None),
        ("POST", "/api/api-endpoints/", _new_endpoint_payload()),
        ("PATCH", f"/api/api-endpoints/{fake_id}", _new_endpoint_payload()),
        ("DELETE", f"/api/api-endpoints/{fake_id}", None),
        ("POST", f"/api/api-endpoints/{fake_id}/test", {"body": {}}),
    ]
    falhas = []
    for method, path, body in checks:
        r = await client.request(method, path, json=body)
        if r.status_code != 401:
            falhas.append(f"{method} {path} → {r.status_code} (esperado 401)")
    assert not falhas, "\n".join(falhas)


@pytest.mark.asyncio
async def test_rbac_analista_pode_ler_mas_nao_mutar(client: AsyncClient, analista_token, admin_token):
    """analista_n3 lê (list+get) mas é bloqueado em POST/PATCH/DELETE."""
    # admin cria 1 endpoint para o analista enxergar.
    r = await client.post(
        "/api/api-endpoints/",
        json=_new_endpoint_payload(name="rbac_read"),
        headers=_h(admin_token),
    )
    assert r.status_code == 201, r.text
    ep_id = r.json()["id"]

    # GET list e GET by-id devem passar.
    r = await client.get("/api/api-endpoints/", headers=_h(analista_token))
    assert r.status_code == 200
    assert any(e["id"] == ep_id for e in r.json())

    r = await client.get(f"/api/api-endpoints/{ep_id}", headers=_h(analista_token))
    assert r.status_code == 200

    # POST/PATCH/DELETE devem dar 403.
    r = await client.post(
        "/api/api-endpoints/",
        json=_new_endpoint_payload(name="rbac_nope"),
        headers=_h(analista_token),
    )
    assert r.status_code == 403

    r = await client.patch(
        f"/api/api-endpoints/{ep_id}",
        json=_new_endpoint_payload(name="rbac_read_mod"),
        headers=_h(analista_token),
    )
    assert r.status_code == 403

    r = await client.delete(f"/api/api-endpoints/{ep_id}", headers=_h(analista_token))
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_rbac_supervisor_tambem_e_bloqueado_em_mutacao(client: AsyncClient, supervisor_token):
    """Router checa especificamente 'admin' — supervisor NÃO basta."""
    r = await client.post(
        "/api/api-endpoints/",
        json=_new_endpoint_payload(),
        headers=_h(supervisor_token),
    )
    assert r.status_code == 403, "supervisor deveria ser bloqueado (só admin pode criar)"


@pytest.mark.asyncio
async def test_rbac_analista_pode_chamar_test_endpoint(
    client: AsyncClient, admin_token, analista_token, monkeypatch
):
    """POST /{id}/test só requer require_user — analista deve passar."""
    r = await client.post(
        "/api/api-endpoints/",
        json=_new_endpoint_payload(name="rbac_test_ep"),
        headers=_h(admin_token),
    )
    ep_id = r.json()["id"]

    _patch_httpx(monkeypatch, _MockResponse(200, {"ok": True}))
    r = await client.post(
        f"/api/api-endpoints/{ep_id}/test",
        json={"body": {"input": "hi"}},
        headers=_h(analista_token),
    )
    assert r.status_code == 200


# ============================================================
# C. CRUD HTTP — happy paths + edge cases
# ============================================================


@pytest.mark.asyncio
async def test_create_serializer_devolve_campos_esperados(client: AsyncClient, admin_token):
    payload = _new_endpoint_payload(name="ser_check")
    r = await client.post("/api/api-endpoints/", json=payload, headers=_h(admin_token))
    assert r.status_code == 201, r.text
    body = r.json()
    # Schema do _serialize() do router
    expected_keys = {
        "id", "name", "description", "url", "method", "headers",
        "timeout_seconds", "is_active", "created_by_user", "created_at",
    }
    assert expected_keys.issubset(body.keys())
    assert body["name"] == payload["name"]
    assert body["method"] == "POST"
    assert body["is_active"] is True
    assert body["created_by_user"] == "ep_admin"
    # created_at em ISO 8601
    datetime.fromisoformat(body["created_at"])


@pytest.mark.asyncio
async def test_create_method_uppercase_normalizado(client: AsyncClient, admin_token):
    payload = _new_endpoint_payload(name="upper_test", method="get")
    r = await client.post("/api/api-endpoints/", json=payload, headers=_h(admin_token))
    assert r.status_code == 201
    assert r.json()["method"] == "GET"


@pytest.mark.asyncio
async def test_create_sem_name_400(client: AsyncClient, admin_token):
    r = await client.post(
        "/api/api-endpoints/",
        json={"name": "", "url": "https://x.com"},
        headers=_h(admin_token),
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_create_sem_url_400(client: AsyncClient, admin_token):
    r = await client.post(
        "/api/api-endpoints/",
        json={"name": "no_url", "url": ""},
        headers=_h(admin_token),
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_create_defaults_aplicados(client: AsyncClient, admin_token):
    """method default POST, headers={}, timeout 30, is_active true."""
    r = await client.post(
        "/api/api-endpoints/",
        json={"name": "defaults_test", "url": "https://x.com"},
        headers=_h(admin_token),
    )
    assert r.status_code == 201
    body = r.json()
    assert body["method"] == "POST"
    assert body["headers"] == {}
    assert body["timeout_seconds"] == 30
    assert body["is_active"] is True


@pytest.mark.asyncio
async def test_get_inexistente_404(client: AsyncClient, admin_token):
    r = await client.get(
        f"/api/api-endpoints/{uuid.uuid4()}", headers=_h(admin_token)
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_patch_atualiza_todos_os_campos(client: AsyncClient, admin_token):
    r = await client.post(
        "/api/api-endpoints/",
        json=_new_endpoint_payload(name="patch_src"),
        headers=_h(admin_token),
    )
    ep_id = r.json()["id"]

    updated = {
        "name": "patch_dst",
        "url": "https://new.example.com",
        "method": "PUT",
        "description": "updated",
        "headers": {"X-New": "yes"},
        "timeout_seconds": 60,
        "is_active": False,
    }
    r = await client.patch(
        f"/api/api-endpoints/{ep_id}", json=updated, headers=_h(admin_token),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "patch_dst"
    assert body["url"] == "https://new.example.com"
    assert body["method"] == "PUT"
    assert body["description"] == "updated"
    assert body["headers"] == {"X-New": "yes"}
    assert body["timeout_seconds"] == 60
    assert body["is_active"] is False


@pytest.mark.asyncio
async def test_patch_inexistente_404(client: AsyncClient, admin_token):
    r = await client.patch(
        f"/api/api-endpoints/{uuid.uuid4()}",
        json=_new_endpoint_payload(),
        headers=_h(admin_token),
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_remove_e_404_apos(client: AsyncClient, admin_token):
    r = await client.post(
        "/api/api-endpoints/",
        json=_new_endpoint_payload(name="del_target"),
        headers=_h(admin_token),
    )
    ep_id = r.json()["id"]

    r = await client.delete(f"/api/api-endpoints/{ep_id}", headers=_h(admin_token))
    assert r.status_code == 200
    assert r.json() == {"ok": True}

    r = await client.get(f"/api/api-endpoints/{ep_id}", headers=_h(admin_token))
    assert r.status_code == 404


# ============================================================
# D. Filtros e listagem
# ============================================================


@pytest.mark.asyncio
async def test_list_only_active_filtra(client: AsyncClient, admin_token):
    # cria 2 — desativa um
    r = await client.post(
        "/api/api-endpoints/",
        json=_new_endpoint_payload(name="flt_active"),
        headers=_h(admin_token),
    )
    active_id = r.json()["id"]
    r = await client.post(
        "/api/api-endpoints/",
        json=_new_endpoint_payload(name="flt_inactive"),
        headers=_h(admin_token),
    )
    inactive_id = r.json()["id"]
    await client.patch(
        f"/api/api-endpoints/{inactive_id}",
        json=_new_endpoint_payload(name="flt_inactive", is_active=False),
        headers=_h(admin_token),
    )

    r = await client.get(
        "/api/api-endpoints/?only_active=true", headers=_h(admin_token)
    )
    ids = {e["id"] for e in r.json()}
    assert active_id in ids
    assert inactive_id not in ids

    r = await client.get(
        "/api/api-endpoints/?only_active=false", headers=_h(admin_token)
    )
    ids = {e["id"] for e in r.json()}
    assert active_id in ids
    assert inactive_id in ids


# ============================================================
# E. POST /{id}/test — endpoint de teste de conectividade
# ============================================================


@pytest.mark.asyncio
async def test_post_test_endpoint_404(client: AsyncClient, admin_token):
    r = await client.post(
        f"/api/api-endpoints/{uuid.uuid4()}/test",
        json={"body": {}},
        headers=_h(admin_token),
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_post_test_chama_url_correta_com_method_post(
    client: AsyncClient, admin_token, monkeypatch
):
    """POST devia mandar `body` como JSON, sem query params."""
    payload = _new_endpoint_payload(
        name="test_post",
        url="https://srv.example.com/x",
        method="POST",
        headers={"X-Tenant": "abc"},
    )
    r = await client.post("/api/api-endpoints/", json=payload, headers=_h(admin_token))
    ep_id = r.json()["id"]

    capture: dict = {}
    _patch_httpx(monkeypatch, _MockResponse(200, {"echo": True}), capture)

    r = await client.post(
        f"/api/api-endpoints/{ep_id}/test",
        json={"body": {"input": "hello"}},
        headers=_h(admin_token),
    )
    assert r.status_code == 200
    result = r.json()
    assert result["ok"] is True
    assert result["status"] == 200
    assert result["body"] == {"echo": True}
    assert result["error"] is None
    assert result["call_id"]
    assert result["duration_ms"] >= 0

    # Confere o que foi passado pro httpx
    assert capture["method"] == "POST"
    assert capture["url"] == "https://srv.example.com/x"
    assert capture["json"] == {"input": "hello"}
    assert capture["params"] is None
    assert capture["headers"] == {"X-Tenant": "abc"}


@pytest.mark.asyncio
async def test_post_test_get_envia_body_como_query(
    client: AsyncClient, admin_token, monkeypatch
):
    """GET deve mandar body como params, NÃO como json."""
    payload = _new_endpoint_payload(
        name="test_get", url="https://srv.example.com/q", method="GET",
    )
    r = await client.post("/api/api-endpoints/", json=payload, headers=_h(admin_token))
    ep_id = r.json()["id"]

    capture: dict = {}
    _patch_httpx(monkeypatch, _MockResponse(200, {"q": "ok"}), capture)

    r = await client.post(
        f"/api/api-endpoints/{ep_id}/test",
        json={"body": {"k": "v"}},
        headers=_h(admin_token),
    )
    assert r.status_code == 200
    assert capture["method"] == "GET"
    assert capture["params"] == {"k": "v"}
    assert capture["json"] is None


@pytest.mark.asyncio
async def test_post_test_response_nao_json_vira_text(
    client: AsyncClient, admin_token, monkeypatch
):
    """Quando o servidor devolve texto puro (não JSON), `body` vira string
    truncada e o audit grava como {"_text": ...}."""
    payload = _new_endpoint_payload(name="test_text")
    r = await client.post("/api/api-endpoints/", json=payload, headers=_h(admin_token))
    ep_id = r.json()["id"]

    _patch_httpx(monkeypatch, _MockResponse(200, json_data=None, text="just plain text"))
    r = await client.post(
        f"/api/api-endpoints/{ep_id}/test",
        json={"body": {}},
        headers=_h(admin_token),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["body"] == "just plain text"

    # api_calls registra como {"_text": ...} (não pode ser string crua em JSONB)
    async with connect() as db:
        row = await db.fetchrow(
            "SELECT response_body FROM api_calls WHERE id = $1::uuid",
            body["call_id"],
        )
    assert row is not None
    assert row["response_body"] == {"_text": "just plain text"}


@pytest.mark.asyncio
async def test_post_test_status_4xx_marca_ok_false(
    client: AsyncClient, admin_token, monkeypatch
):
    payload = _new_endpoint_payload(name="test_404")
    r = await client.post("/api/api-endpoints/", json=payload, headers=_h(admin_token))
    ep_id = r.json()["id"]

    _patch_httpx(monkeypatch, _MockResponse(404, {"detail": "not found"}))
    r = await client.post(
        f"/api/api-endpoints/{ep_id}/test",
        json={"body": {}},
        headers=_h(admin_token),
    )
    body = r.json()
    assert body["status"] == 404
    assert body["ok"] is False
    assert body["error"] is None  # 4xx não vira error — é resposta válida


@pytest.mark.asyncio
async def test_post_test_excecao_de_rede_marca_error(
    client: AsyncClient, admin_token, monkeypatch
):
    """Timeout/DNS/etc levantam exceção no httpx → vira `error` no result."""
    payload = _new_endpoint_payload(name="test_neterr")
    r = await client.post("/api/api-endpoints/", json=payload, headers=_h(admin_token))
    ep_id = r.json()["id"]

    _patch_httpx(monkeypatch, httpx.ConnectError("DNS fail"))
    r = await client.post(
        f"/api/api-endpoints/{ep_id}/test",
        json={"body": {}},
        headers=_h(admin_token),
    )
    body = r.json()
    assert body["ok"] is False
    assert body["status"] is None
    assert "ConnectError" in body["error"]
    assert "DNS fail" in body["error"]


# ============================================================
# F. Auditoria — toda chamada do /test grava em api_calls
# ============================================================


@pytest.mark.asyncio
async def test_api_calls_audita_chamada_de_test(
    client: AsyncClient, admin_token, monkeypatch
):
    payload = _new_endpoint_payload(name="audit_test")
    r = await client.post("/api/api-endpoints/", json=payload, headers=_h(admin_token))
    ep_id = r.json()["id"]

    _patch_httpx(monkeypatch, _MockResponse(200, {"saved": True}))
    r = await client.post(
        f"/api/api-endpoints/{ep_id}/test",
        json={"body": {"x": 1}},
        headers=_h(admin_token),
    )
    body = r.json()
    call_id = body["call_id"]

    async with connect() as db:
        row = await db.fetchrow(
            "SELECT id::text AS id, api_endpoint_id::text AS api_endpoint_id, "
            "       request_body, response_status, response_body, error, "
            "       duration_ms, called_at "
            "FROM api_calls WHERE id = $1::uuid",
            call_id,
        )
    assert row is not None
    assert row["api_endpoint_id"] == ep_id
    assert row["request_body"] == {"x": 1}
    assert row["response_status"] == 200
    assert row["response_body"] == {"saved": True}
    assert row["error"] is None
    assert row["duration_ms"] >= 0
    assert row["called_at"] is not None


@pytest.mark.asyncio
async def test_api_calls_audita_erro_de_rede(
    client: AsyncClient, admin_token, monkeypatch
):
    payload = _new_endpoint_payload(name="audit_err")
    r = await client.post("/api/api-endpoints/", json=payload, headers=_h(admin_token))
    ep_id = r.json()["id"]

    _patch_httpx(monkeypatch, httpx.TimeoutException("read timeout"))
    r = await client.post(
        f"/api/api-endpoints/{ep_id}/test",
        json={"body": {}},
        headers=_h(admin_token),
    )
    call_id = r.json()["call_id"]

    async with connect() as db:
        row = await db.fetchrow(
            "SELECT response_status, response_body, error "
            "FROM api_calls WHERE id = $1::uuid",
            call_id,
        )
    assert row["response_status"] is None
    assert row["response_body"] is None
    assert "TimeoutException" in row["error"]


# ============================================================
# G. DI / Singleton — get_api_endpoint_service devolve mesma instância
# ============================================================


def test_di_singleton():
    a = get_api_endpoint_service()
    b = get_api_endpoint_service()
    assert a is b


# ============================================================
# H. Smoke: estrutura de query string + paths
# ============================================================


@pytest.mark.asyncio
async def test_list_endpoints_vazia_devolve_lista(client: AsyncClient, admin_token):
    r = await client.get("/api/api-endpoints/", headers=_h(admin_token))
    assert r.status_code == 200
    assert isinstance(r.json(), list)


@pytest.mark.asyncio
async def test_post_test_propaga_timeout_configurado(
    client: AsyncClient, admin_token, monkeypatch
):
    """Cada endpoint tem timeout próprio — o AsyncClient deve usar esse valor."""
    payload = _new_endpoint_payload(name="timeout_pass", timeout_seconds=7)
    r = await client.post("/api/api-endpoints/", json=payload, headers=_h(admin_token))
    ep_id = r.json()["id"]

    capture: dict = {}
    _patch_httpx(monkeypatch, _MockResponse(200, {"ok": True}), capture)

    r = await client.post(
        f"/api/api-endpoints/{ep_id}/test",
        json={"body": {}},
        headers=_h(admin_token),
    )
    assert r.status_code == 200
    # `self.timeout` no AsyncClient capturado — deve refletir 7s.
    # httpx wrappa em `Timeout(7)`. Aceitamos a representação string.
    t = capture["timeout"]
    assert "7" in str(t)
