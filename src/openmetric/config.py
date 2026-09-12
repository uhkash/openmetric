"""Runtime configuration, loaded from environment variables or a local .env file."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All knobs are prefixed OPENMETRIC_ in the environment."""

    model_config = SettingsConfigDict(
        env_prefix="OPENMETRIC_",
        env_file=os.environ.get("OPENMETRIC_ENV_FILE", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    secret_key: str = ""
    database_url: str = "sqlite:///./openmetric.db"

    host: str = "127.0.0.1"
    port: int = 8099

    admin_token: str = ""

    log_request_bodies: bool = False
    log_response_bodies: bool = False
    redact_logs: bool = True

    timeout_seconds: float = 600.0
    event_retention_days: int = 0

    @property
    def env_file_path(self) -> Path:
        return Path(os.environ.get("OPENMETRIC_ENV_FILE", ".env"))

    @property
    def bodies_are_logged(self) -> bool:
        return self.log_request_bodies or self.log_response_bodies


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    """Used by the CLI and tests after mutating the environment."""
    get_settings.cache_clear()
