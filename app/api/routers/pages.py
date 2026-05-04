"""Router de páginas HTML (template engine Jinja2)."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.api.deps import (
    current_user_optional,
    get_auth_service,
    get_bko_service,
    get_churn_service,
    get_failsafe_service,
    get_finops_service,
    get_prompt_service,
    get_radar_service,
    get_registry_service,
    get_skill_service,
    get_user_admin_service,
)
from app.core.domain.entities import User
from app.core.services.auth_service import AuthService
from app.core.services.churn_service import ChurnService
from app.core.services.failsafe_service import FailsafeService
from app.core.services.finops_service import FinOpsService
from app.core.services.prompt_service import PromptService
from app.core.services.radar_service import RadarService
from app.core.services.registry_service import RegistryService
from app.core.services.skill_service import SkillService
from app.core.services.user_admin_service import UserAdminService

BASE_DIR = Path(__file__).resolve().parents[2]
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

router = APIRouter()


def _ctx(request: Request, user: User | None, **extras):
    return {
        "request": request,
        "user": user,
        "active_module": extras.pop("active_module", None),
        **extras,
    }


def _require_any_role(user: User | None, allowed: list[str]) -> User:
    """Bloqueia acesso à página se o usuário não tem ao menos um dos roles.

    Usado como gate no servidor para os grupos Configurações/Monitoramento/
    Administrativo. analista_n3 só passa em rotas com role 'analista_n3' OR sem gate.
    """
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "autenticação requerida")
    if not any(r in allowed for r in (user.roles or [])):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"acesso restrito · requer um dos papéis: {', '.join(allowed)}"
        )
    return user


@router.get("/", response_class=HTMLResponse)
async def cockpit(
    request: Request,
    user: User | None = Depends(current_user_optional),
    reg: RegistryService = Depends(get_registry_service),
):
    if not user:
        return RedirectResponse("/login", status_code=status.HTTP_302_FOUND)

    from datetime import datetime
    from app.core.services.cockpit_service import CockpitService

    # Atividade pessoal do usuário logado (KPIs, heatmap, timeline, top módulos)
    cockpit_svc = CockpitService()
    activity = await cockpit_svc.user_activity(user_id=str(user.id), days=30)

    # Módulos disponíveis (catálogo, sem custos) — só para mostrar atalhos
    modules_all = await reg.list_all()
    modules_catalog = [
        {
            "id": str(m.id),
            "name": m.name,
            "description": m.description,
            "status": m.status.value,
        }
        for m in modules_all if m.status.value == "active"
    ]

    return templates.TemplateResponse(
        "cockpit/index.html",
        _ctx(
            request, user,
            active_module="cockpit",
            activity=activity,
            modules_catalog=modules_catalog,
            now=datetime.now().strftime("%d/%m/%Y %H:%M"),
        ),
    )


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, auth: AuthService = Depends(get_auth_service)):
    setup_mode = not await auth.has_any_user()
    return templates.TemplateResponse("auth/login.html", {"request": request, "error": None, "setup_mode": setup_mode})


@router.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    auth: AuthService = Depends(get_auth_service),
):
    if not await auth.has_any_user():
        try:
            user = await auth.bootstrap_root(username, password)
        except ValueError:
            user = None
        if user:
            token = auth.issue_token(user)
            request.session["token"] = token
            request.session["username"] = user.username
            return RedirectResponse("/", status_code=status.HTTP_302_FOUND)

    user = await auth.authenticate(username, password)
    if not user:
        return templates.TemplateResponse(
            "auth/login.html",
            {"request": request, "error": "Credenciais inválidas.", "setup_mode": False},
            status_code=401,
        )
    token = auth.issue_token(user)
    request.session["token"] = token
    request.session["username"] = user.username
    return RedirectResponse("/", status_code=status.HTTP_302_FOUND)


@router.get("/logout")
async def logout_page(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=status.HTTP_302_FOUND)


# ---------- Radar (BKO Inteligente) ----------

@router.get("/radar", response_class=HTMLResponse)
async def radar_page(
    request: Request,
    case: str | None = None,
    user: User | None = Depends(current_user_optional),
    bko=Depends(get_bko_service),
):
    if not user:
        return RedirectResponse("/login")
    detail = None
    selected_case = None
    transcript = None
    # Sem `?case=`, NÃO pré-seleciona — deixa o client decidir (via localStorage
    # da última seleção; senão, mostra "selecione...")
    if case:
        detail = await bko.get_case_with_transcript(case)

    if detail:
        selected_case = detail["case"]
        transcript = detail["transcript"]

    return templates.TemplateResponse(
        "radar/index.html",
        _ctx(
            request, user,
            active_module="radar",
            selected_case=selected_case,
            transcript=transcript,
            stats=await bko.stats(),
        ),
    )


@router.get("/radar/{case_number}", response_class=HTMLResponse)
async def radar_case_page(
    case_number: str,
    request: Request,
    user: User | None = Depends(current_user_optional),
    bko=Depends(get_bko_service),
):
    if not user:
        return RedirectResponse("/login")
    detail = await bko.get_case_with_transcript(case_number)
    if not detail:
        # caso pode ter sido excluído — redireciona para o estado vazio em vez de 404
        return RedirectResponse("/radar")
    return templates.TemplateResponse(
        "radar/index.html",
        _ctx(
            request, user,
            active_module="radar",
            selected_case=detail["case"],
            transcript=detail["transcript"],
            stats=await bko.stats(),
        ),
    )


# ---------- Raio X Cliente ----------

@router.get("/raiox", response_class=HTMLResponse)
async def raiox_page(
    request: Request,
    board: str | None = None,
    user: User | None = Depends(current_user_optional),
):
    if not user:
        return RedirectResponse("/login")
    # Todos os usuários autenticados acessam (analista_n3 em modo leitura).
    can_edit = any(r in {"admin", "supervisor"} for r in (user.roles or []))
    # Cache-busting do raiox.js: usa mtime do arquivo como version param,
    # garantindo que cada deploy serve a versão atual ao browser.
    import os
    js_path = BASE_DIR / "static" / "js" / "raiox.js"
    asset_v = str(int(os.path.getmtime(js_path))) if js_path.exists() else "1"
    return templates.TemplateResponse(
        "raiox/index.html",
        _ctx(
            request, user,
            active_module="raiox",
            can_edit=can_edit,
            initial_board_id=board,
            asset_v=asset_v,
        ),
    )


# ---------- Churn ----------

@router.get("/churn", response_class=HTMLResponse)
async def churn_page(
    request: Request,
    user: User | None = Depends(current_user_optional),
    svc: ChurnService = Depends(get_churn_service),
):
    if not user:
        return RedirectResponse("/login")
    roots = await svc.get_taxonomy()
    return templates.TemplateResponse(
        "churn/index.html",
        _ctx(request, user, active_module="churn", roots=roots),
    )


# ---------- Prompts ----------

@router.get("/prompts", response_class=HTMLResponse)
async def prompts_page(
    request: Request,
    user: User | None = Depends(current_user_optional),
    svc: PromptService = Depends(get_prompt_service),
):
    if not user:
        return RedirectResponse("/login")
    _require_any_role(user, ['admin', 'supervisor'])
    radar_prompts = await svc.list_for_module("radar")
    churn_prompts = await svc.list_for_module("churn")
    return templates.TemplateResponse(
        "prompts/index.html",
        _ctx(
            request, user,
            active_module="prompts",
            radar_prompts=radar_prompts,
            churn_prompts=churn_prompts,
        ),
    )


# ---------- FinOps ----------

@router.get("/finops", response_class=HTMLResponse)
async def finops_page(
    request: Request,
    user: User | None = Depends(current_user_optional),
    svc: FinOpsService = Depends(get_finops_service),
):
    if not user:
        return RedirectResponse("/login")
    _require_any_role(user, ['admin', 'supervisor', 'finops'])
    by_module = await svc.by_module()
    by_model = await svc.by_model()
    total_cost = sum(r["cost"] for r in by_model)
    total_calls = sum(r["calls"] for r in by_model)

    # Tarifas vivas de cada adapter, para a seção "Como o custo é calculado".
    from app.adapters.llm.factory import build_clients
    rates = []
    for model_name, client in build_clients().items():
        in_rate = float(getattr(client, "cost_per_1k_input", 0.0) or 0.0)
        cached_in_rate = float(getattr(client, "cost_per_1k_cached_input", 0.0) or 0.0)
        rates.append({
            "model": model_name,
            "in_per_1k": in_rate,
            "out_per_1k": float(getattr(client, "cost_per_1k_output", 0.0) or 0.0),
            "cached_in_per_1k": cached_in_rate,
            # economia percentual de usar cache vs input cobrado normalmente
            "cache_savings_pct": (
                round((1 - cached_in_rate / in_rate) * 100, 1)
                if in_rate > 0 and cached_in_rate < in_rate
                else 0.0
            ),
            "is_mock": client.__class__.__name__ == "MockLLMClient",
        })
    rates.sort(key=lambda r: r["model"])

    # Orçamentos avaliados (com gasto corrente vs limite + severidade).
    from app.adapters.db.repositories.finops_repo import (
        SqliteFinOpsBudgetRepository, SqliteFinOpsModelPolicyRepository,
    )
    from app.core.services.finops_service import (
        FinOpsBudgetService, FinOpsPolicyService,
    )
    budget_svc = FinOpsBudgetService(
        SqliteFinOpsBudgetRepository(), svc.repo,
    )
    policy_svc = FinOpsPolicyService(SqliteFinOpsModelPolicyRepository())
    budget_statuses = await budget_svc.evaluate_all()
    recent_alerts = await budget_svc.recent_alerts(10)
    policies = await policy_svc.list()

    # Showback multi-dimensional. Não falha a página se uma dimensão der erro.
    breakdowns: dict[str, list[dict]] = {}
    for dim in ("domain", "agent", "environment"):
        try:
            breakdowns[dim] = (await svc.by_dimension(dim))[:8]
        except ValueError:
            breakdowns[dim] = []

    # Conhecidos pela plataforma (vão alimentar selects de scope_value).
    known_models = sorted({m for m in (build_clients() or {}).keys()})
    known_modules = sorted({r["module"] for r in by_module if r["module"]})

    return templates.TemplateResponse(
        "finops/index.html",
        _ctx(
            request, user,
            active_module="finops",
            by_module=by_module, by_model=by_model,
            total_cost=total_cost, total_calls=total_calls,
            model_rates=rates,
            budget_statuses=budget_statuses,
            recent_alerts=recent_alerts,
            policies=policies,
            breakdowns=breakdowns,
            known_models=known_models,
            known_modules=known_modules,
        ),
    )


# ---------- Audit (Rastreabilidade) ----------

@router.get("/audit", response_class=HTMLResponse)
async def audit_page(
    request: Request,
    user: User | None = Depends(current_user_optional),
):
    if not user:
        return RedirectResponse("/login")
    _require_any_role(user, ['admin', 'supervisor', 'finops'])
    return templates.TemplateResponse(
        "audit/index.html",
        _ctx(request, user, active_module="audit"),
    )


# ---------- Failsafe ----------

@router.get("/failsafe", response_class=HTMLResponse)
async def failsafe_page(
    request: Request,
    user: User | None = Depends(current_user_optional),
    svc: FailsafeService = Depends(get_failsafe_service),
):
    if not user:
        return RedirectResponse("/login")
    _require_any_role(user, ['admin', 'supervisor', 'finops'])
    pending = await svc.list_pending()
    return templates.TemplateResponse(
        "failsafe/inbox.html",
        _ctx(request, user, active_module="failsafe", pending=pending),
    )


# ---------- Skills/Modules ----------

@router.get("/modules", response_class=HTMLResponse)
async def modules_page(
    request: Request,
    user: User | None = Depends(current_user_optional),
    svc: RegistryService = Depends(get_registry_service),
):
    if not user:
        return RedirectResponse("/login")
    _require_any_role(user, ['admin', 'supervisor'])
    raw = await svc.list_all()
    modules = [
        {
            "id": str(m.id),
            "name": m.name,
            "endpoint_url": m.endpoint_url,
            "status": m.status.value,
            "config_params": m.config_params,
            "description": m.description,
            "skill_path": m.skill_path,
            "response_type": getattr(m, "response_type", "text") or "text",
            "response_config": getattr(m, "response_config", {}) or {},
        }
        for m in raw
    ]
    return templates.TemplateResponse(
        "modules/index.html",
        _ctx(request, user, active_module="modules", modules=modules),
    )


# ---------- Users ----------

@router.get("/users", response_class=HTMLResponse)
async def users_page(
    request: Request,
    user: User | None = Depends(current_user_optional),
    svc: UserAdminService = Depends(get_user_admin_service),
):
    if not user:
        return RedirectResponse("/login")
    if "admin" not in user.roles:
        raise HTTPException(403, "apenas admin pode gerenciar usuários")
    users_raw = await svc.list_all()
    users = [
        {
            "id": str(u.id),
            "username": u.username,
            "full_name": getattr(u, "full_name", "") or "",
            "email": getattr(u, "email", "") or "",
            "phone": getattr(u, "phone", "") or "",
            "department": getattr(u, "department", "") or "",
            "title": getattr(u, "title", "") or "",
            "roles": u.roles,
            "is_active": u.is_active,
        }
        for u in users_raw
    ]
    return templates.TemplateResponse(
        "users/index.html",
        _ctx(request, user, active_module="users", users=users),
    )


# ---------- Galeria de Apresentações ----------

@router.get("/apis", response_class=HTMLResponse)
async def apis_page(
    request: Request,
    user: User | None = Depends(current_user_optional),
):
    if not user:
        return RedirectResponse("/login")
    if "admin" not in user.roles:
        raise HTTPException(403, "apenas admin pode gerenciar APIs")
    return templates.TemplateResponse(
        "apis/index.html",
        _ctx(request, user, active_module="apis"),
    )


@router.get("/gallery", response_class=HTMLResponse)
async def gallery_page(
    request: Request,
    user: User | None = Depends(current_user_optional),
):
    if not user:
        return RedirectResponse("/login")
    if "admin" not in user.roles:
        raise HTTPException(403, "apenas admin pode acessar a Galeria")
    return templates.TemplateResponse(
        "gallery/index.html",
        _ctx(request, user, active_module="gallery"),
    )


@router.get("/gallery/{presentation_id}", response_class=HTMLResponse)
async def gallery_detail_page(
    presentation_id: str,
    request: Request,
    user: User | None = Depends(current_user_optional),
):
    if not user:
        return RedirectResponse("/login")
    if "admin" not in user.roles:
        raise HTTPException(403, "apenas admin pode acessar a Galeria")
    return templates.TemplateResponse(
        "gallery/detail.html",
        _ctx(request, user, active_module="gallery", presentation_id=presentation_id),
    )


# ---------- Skills ----------

@router.get("/skills", response_class=HTMLResponse)
async def skills_page(
    request: Request,
    name: str | None = None,
    user: User | None = Depends(current_user_optional),
    svc: SkillService = Depends(get_skill_service),
):
    if not user:
        return RedirectResponse("/login")
    _require_any_role(user, ['admin', 'supervisor'])
    raw = svc.list_all()
    skills = [
        {
            "name": s.name, "title": s.title, "path": s.path,
            "sections": list(s.sections.keys()),
            "updated_at": s.updated_at.isoformat(),
            "size_bytes": s.size_bytes,
        }
        for s in raw
    ]
    selected_obj = svc.get(name) if name else (raw[0] if raw else None)
    selected = None
    if selected_obj:
        selected = {
            "name": selected_obj.name, "title": selected_obj.title, "path": selected_obj.path,
            "content": selected_obj.content, "sections": selected_obj.sections,
            "updated_at": selected_obj.updated_at.isoformat(),
            "size_bytes": selected_obj.size_bytes,
        }
    return templates.TemplateResponse(
        "skills/index.html",
        _ctx(request, user, active_module="skills", skills=skills, selected=selected),
    )


# ---------- Building Blocks (catálogo) ----------

@router.get("/blocks", response_class=HTMLResponse)
async def blocks_page(
    request: Request,
    user: User | None = Depends(current_user_optional),
    reg: RegistryService = Depends(get_registry_service),
    skills: SkillService = Depends(get_skill_service),
    prompts: PromptService = Depends(get_prompt_service),
):
    if not user:
        return RedirectResponse("/login")
    _require_any_role(user, ['admin', 'supervisor'])
    modules = await reg.list_all()
    all_prompts = await prompts.list_all()
    blocks = []
    for m in modules:
        skill_dict = None
        if m.skill_path:
            stem = m.skill_path.rsplit("/", 1)[-1].replace(".md", "")
            skill_obj = skills.get(stem)
            if skill_obj:
                skill_dict = {
                    "name": skill_obj.name,
                    "title": skill_obj.title,
                    "path": skill_obj.path,
                }
        cnt = sum(1 for p in all_prompts if p.module_name == m.name)
        blocks.append({
            "id": str(m.id),
            "name": m.name,
            "title": m.name.replace("_", " ").title(),
            "description": m.description or "Sem descrição.",
            "status": m.status.value,
            "skill_obj": skill_dict,
            "prompts_count": cnt,
            "config_params": m.config_params,
        })
    return templates.TemplateResponse(
        "blocks/index.html",
        _ctx(request, user, active_module="blocks", blocks=blocks),
    )


# ---------- Cards em tela (Administrativo) ----------
# Listagem global de TODOS os cards na tela "Voz do Cliente" — criadores,
# nível de visibilidade e quem pode ver. Apenas admin/supervisor.

@router.get("/admin/cards-em-tela", response_class=HTMLResponse)
async def cards_em_tela_page(
    request: Request,
    user: User | None = Depends(current_user_optional),
):
    if not user:
        return RedirectResponse("/login")
    _require_any_role(user, ['admin', 'supervisor'])

    from app.adapters.db.repositories.radar_card_visibility_repo import (
        SqliteRadarCardVisibilityRepository,
    )
    repo = SqliteRadarCardVisibilityRepository()
    rows = await repo.list_all()
    # enriquece com `who_can_see` igual ao endpoint /api/radar/admin/cards
    for r in rows:
        v = r.get("visibility") or "private"
        if v == "private":
            r["who_can_see"] = ["dono"]
        elif v == "public_lideranca":
            r["who_can_see"] = ["dono", "admin", "supervisor"]
        elif v == "public_analista":
            r["who_can_see"] = ["dono", "admin", "supervisor", "analista"]
        else:
            r["who_can_see"] = ["dono"]
    return templates.TemplateResponse(
        "admin/cards_em_tela.html",
        _ctx(request, user, active_module="cards_em_tela", cards=rows),
    )
