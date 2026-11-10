"""Application settings, loaded from environment variables / the .env file.

pydantic-settings reads `.env` (via python-dotenv) and real environment variables.
Real environment variables win, which is how the test suite points the app at the
isolated test database without touching `.env`.
"""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    database_url: str

    # LLM: "openai_compatible" (Ollama, company gateway, ...) or "mock"
    llm_provider: str = "mock"
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_timeout_seconds: float = 60.0

    max_upload_mb: int = 50
    upload_dir: str = "data/uploads"
    log_level: str = "INFO"

    @property
    def upload_path(self) -> Path:
        path = Path(self.upload_dir)
        return path if path.is_absolute() else PROJECT_ROOT / path

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def safe_database_target(self) -> str:
        """host:port/db without credentials - safe to log or return from /health."""
        from sqlalchemy.engine import make_url

        url = make_url(self.database_url)
        return f"{url.host}:{url.port}/{url.database}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
