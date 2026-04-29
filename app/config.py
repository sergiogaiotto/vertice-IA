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

    # DB
    database_url: str = f"sqlite+aiosqlite:///{BASE_DIR / 'data' / 'vertice.db'}"

    # Auth
    jwt_algorithm: str = "HS256"
    jwt_expires_minutes: int = 480
    admin_bootstrap_user: str = "admin"
    admin_bootstrap_password: str = "vertice2026"

    # LLMs
    openai_api_key: str = ""
    openai_model: str = "gpt-4.1"

    maritaca_api_key: str = ""
    maritaca_model: str = "sabia-4"
    maritaca_base_url: str = "https://chat.maritaca.ai/api"

    gaia_api_key: str = ""
    gaia_model: str = "gaia-4b"
    gaia_base_url: str = ""

    # Router
    router_default_model: str = "sabia-4"
    router_fallback_model: str = "gpt-4.1"
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
    def db_path(self) -> Path:
        # extrai path do sqlite+aiosqlite:///<path>
        url = self.database_url
        if "sqlite" in url:
            return Path(url.split("///", 1)[-1])
        return BASE_DIR / "data" / "vertice.db"


@lru_cache
def get_settings() -> Settings:
    return Settings()
