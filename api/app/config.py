"""Settings, read from environment variables (see docker-compose.yml)."""
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache

APP_VERSION = "0.1.0"


def _csv(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(",") if part.strip())


@dataclass(frozen=True)
class Settings:
    db_host: str
    db_port: int
    db_name: str
    db_user: str
    db_password: str
    db_pool_min: int
    db_pool_max: int
    db_pool_timeout: float
    api_public_url: str
    docs_public_url: str
    cors_origins: tuple[str, ...]
    log_level: str

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        return cls(
            db_host=env.get("DB_HOST", "localhost"),
            db_port=int(env.get("DB_PORT", "5432")),
            db_name=env.get("DB_NAME", "buechertausch"),
            db_user=env.get("DB_USER", "app_user"),
            db_password=env.get("DB_PASSWORD", ""),
            db_pool_min=int(env.get("DB_POOL_MIN", "1")),
            db_pool_max=int(env.get("DB_POOL_MAX", "10")),
            db_pool_timeout=float(env.get("DB_POOL_TIMEOUT", "10")),
            api_public_url=env.get("API_PUBLIC_URL", "http://localhost:8000").rstrip("/"),
            docs_public_url=env.get("DOCS_PUBLIC_URL", "http://localhost:8080").rstrip("/"),
            cors_origins=_csv(
                env.get("CORS_ORIGINS", "http://localhost:8080,http://127.0.0.1:8080")
            ),
            log_level=env.get("LOG_LEVEL", "INFO").upper(),
        )


@lru_cache
def get_settings() -> Settings:
    return Settings.from_env()
