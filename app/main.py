"""Entrypoint FastAPI da plataforma Vértice."""

import logging
import warnings

# Silencia warning recorrente do Pydantic v2 ao processar TypedDicts internos
# do deepagents/langchain que usam `typing.NotRequired`. O Pydantic gera o aviso
# em CADA chamada que toca esses schemas — não impacta runtime, só polui logs.
warnings.filterwarnings(
    "ignore",
    message=r".*typing\.NotRequired is not a Python type.*",
    category=UserWarning,
)


# Silencia warning falso do deepagents.middleware.skills no Windows: o backend
# devolve paths com `\` (backslash), mas o validador interno usa `PurePosixPath`
# que não reconhece `\` como separador, retornando o caminho inteiro como `name`.
# Os SKILL.md do projeto estão corretos — o aviso é só ruído de log.
class _SkillSpecWarningFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if "does not follow Agent Skills specification" in msg and "must match directory name" in msg:
            return False
        return True


logging.getLogger("deepagents.middleware.skills").addFilter(_SkillSpecWarningFilter())

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app import __version__
from app.adapters.db.sqlite import init_db
from app.api.routers import (
    api_endpoints_router,
    auth_router,
    audit_router,
    blocks_router,
    churn_router,
    failsafe_router,
    finops_router,
    modules_router,
    pages,
    presentations_router,
    prompts_router,
    radar_router,
    raiox_router,
    skills_router,
    users_router,
)
from app.config import get_settings

settings = get_settings()
BASE_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    # bootstrap: garante schema, seed e admin user
    await init_db()
    yield


app = FastAPI(
    title=settings.app_name,
    version=__version__,
    description="Framework de Building Blocks de IA — Spec-Driven Development",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

app.add_middleware(SessionMiddleware, secret_key=settings.app_secret_key, max_age=60 * 60 * 12)

# AuditMiddleware: registra TODA chamada HTTP no audit trail. Adicionado DEPOIS
# do SessionMiddleware para que request.session esteja acessível no encadeamento.
from app.api.middleware.audit import AuditMiddleware  # noqa: E402
app.add_middleware(AuditMiddleware)

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

# Páginas (server-rendered)
app.include_router(pages.router)

# APIs
app.include_router(auth_router.router, prefix="/api/auth", tags=["auth"])
app.include_router(users_router.router, prefix="/api/users", tags=["users"])
app.include_router(radar_router.router, prefix="/api/radar", tags=["radar"])
app.include_router(raiox_router.router, prefix="/api/raiox", tags=["raiox"])
app.include_router(churn_router.router, prefix="/api/churn", tags=["churn"])
app.include_router(prompts_router.router, prefix="/api/prompts", tags=["prompts"])
app.include_router(finops_router.router, prefix="/api/finops", tags=["finops"])
app.include_router(failsafe_router.router, prefix="/api/failsafe", tags=["failsafe"])
app.include_router(modules_router.router, prefix="/api/modules", tags=["modules"])
app.include_router(skills_router.router, prefix="/api/skills", tags=["skills"])
app.include_router(blocks_router.router, prefix="/api/blocks", tags=["blocks"])
app.include_router(audit_router.router, prefix="/api/audit", tags=["audit"])
app.include_router(presentations_router.router, prefix="/api/presentations", tags=["presentations"])
app.include_router(api_endpoints_router.router, prefix="/api/api-endpoints", tags=["api-endpoints"])


@app.get("/health", include_in_schema=False)
async def health():
    return {"status": "ok", "service": settings.app_name, "version": __version__}


@app.exception_handler(404)
async def not_found(request: Request, exc):
    if request.url.path.startswith("/api"):
        return JSONResponse(status_code=404, content={"detail": "not found"})
    return HTMLResponse("<h1>404</h1>", status_code=404)
