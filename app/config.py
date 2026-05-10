"""Configurações centralizadas via pydantic-settings."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # App
    app_name: str = "Vértice"
    app_env: str = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    app_secret_key: str = "change-me"
    app_base_url: str = "http://localhost:8000"

    # DB — PostgreSQL via asyncpg
    # Aceita tanto DSN puro (postgresql://user:pass@host:port/db) quanto a
    # forma SQLAlchemy (postgresql+asyncpg://...). O método `pg_dsn` normaliza
    # para o formato esperado por asyncpg (sem o sufixo +asyncpg).
    database_url: str = "postgresql://vertice:vertice@localhost:5432/vertice-ia"

    # Pool de conexões — calibrado para throughput.
    # min_size: conexões "warm" mantidas no pool (latência baixa em pico)
    # max_size: teto. Em produção, ajustar conforme `max_connections` do PG
    # (ver: SHOW max_connections; típico 100). Cada worker uvicorn carrega
    # o seu próprio pool — dimensionar como pool_max * num_workers <= 80%
    # de max_connections deixando folga para conexões administrativas.
    pg_pool_min_size: int = 5
    pg_pool_max_size: int = 20
    pg_pool_max_inactive_connection_lifetime: float = 300.0  # 5 min — recicla conexões ociosas
    pg_command_timeout: float = 30.0                          # timeout default por query
    pg_statement_cache_size: int = 1024                       # cache de prepared statements por conexão

    # Auth
    # Bootstrap do primeiro usuário: NÃO há credenciais default. Quando a
    # tabela `users` está vazia, a primeira submissão em /login (qualquer
    # username/senha que o operador escolher) cria o usuário ROOT.
    # Fluxo em app/api/routers/pages.py:login_submit.
    jwt_algorithm: str = "HS256"
    jwt_expires_minutes: int = 480

    # LLMs — Azure OpenAI (gpt-4o)
    azure_openai_api_key: str = ""
    azure_openai_endpoint: str = ""           # ex: https://meu-recurso.openai.azure.com
    azure_openai_api_version: str = "2024-08-01-preview"
    azure_openai_deployment: str = "gpt-4o"   # nome do deployment no Azure (usado como model)

    maritaca_api_key: str = ""
    maritaca_model: str = "sabia-4"
    maritaca_base_url: str = "https://chat.maritaca.ai/api"

    gaia_api_key: str = ""
    gaia_model: str = "gaia-4b"
    gaia_base_url: str = ""

    # Router
    router_default_model: str = "sabia-4"
    router_fallback_model: str = "gpt-4o"
    router_cheap_model: str = "gaia-4b"

    # Observability
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"
    mlflow_tracking_uri: str = ""
    otel_exporter_otlp_endpoint: str = ""
    otel_service_name: str = "vertice"

    # Policy
    opa_url: str = ""

    # Guardrails
    guardrail_input_max_chars: int = 20000
    guardrail_injection_block: bool = True
    guardrail_pii_redact: bool = True

    @property
    def pg_dsn(self) -> str:
        """Normaliza o DSN para o formato aceito por `asyncpg.connect`/`create_pool`.

        - `postgresql+asyncpg://...`  → `postgresql://...`  (asyncpg não usa o sufixo)
        - `postgres://...`            → `postgresql://...`  (alias compatível)
        """
        url = self.database_url.strip()
        if url.startswith("postgresql+asyncpg://"):
            url = "postgresql://" + url[len("postgresql+asyncpg://"):]
        elif url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://"):]
        return url


@lru_cache
def get_settings() -> Settings:
    return Settings()
