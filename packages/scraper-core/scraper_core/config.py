"""Config loading via pydantic-settings. All configuration comes from .env / env vars.

These variable names are the single source of truth and MUST stay in sync with
`.env.example` at the repo root and with the secret names used in
`infra/provision.sh` / `infra/add-user.sh`.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Turso / libSQL ---
    turso_database_url: str | None = Field(default=None, alias="TURSO_DATABASE_URL")
    turso_auth_token: str | None = Field(default=None, alias="TURSO_AUTH_TOKEN")

    # --- Local SQLite ---
    local_sqlite_path: str = Field(default="./data/local.db", alias="LOCAL_SQLITE_PATH")

    # --- Healthchecks.io (optional, no-op if unset) ---
    healthcheck_url: str | None = Field(default=None, alias="HEALTHCHECK_URL")

    # --- Logging ---
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # --- Scraper-specific ---
    scrape_source_url: str = Field(
        default="https://jsonplaceholder.typicode.com/posts",
        alias="SCRAPE_SOURCE_URL",
    )

    @property
    def turso_configured(self) -> bool:
        """True only when both URL and token are present - used for graceful
        fallback so the demo works fully offline without a Turso account."""
        return bool(self.turso_database_url and self.turso_auth_token)


def get_settings() -> Settings:
    """Small indirection so tests can monkeypatch/override easily."""
    return Settings()
