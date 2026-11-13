"""Application settings, loaded from environment variables (and `.env` locally)."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy.engine import URL, make_url

Environment = Literal["development", "test", "staging", "production"]
DatabaseSSL = Literal["disable", "allow", "prefer", "require", "verify-ca", "verify-full"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    environment: Environment = "development"
    log_level: str = "INFO"
    port: int = 8000

    # Postgres (Supabase Supavisor pooler). Session pooler (5432) recommended;
    # transaction pooler (6543) also works because statement caching is disabled.
    database_url: SecretStr | None = None
    database_ssl: DatabaseSSL = "prefer"
    db_pool_size: int = Field(default=5, ge=1)
    db_max_overflow: int = Field(default=5, ge=0)
    db_pool_timeout_s: float = Field(default=10.0, gt=0)
    db_pool_recycle_s: int = Field(default=1800, gt=0)
    db_statement_timeout_ms: int = Field(default=15_000, ge=0)

    # Supabase Auth
    supabase_url: str | None = None
    supabase_jwt_secret: SecretStr | None = None
    jwt_leeway_s: int = Field(default=10, ge=0, le=120)
    jwks_cache_lifespan_s: int = Field(default=600, gt=0)

    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)
    enable_docs: bool | None = None

    # Optimizer (runs in-process off the event loop).
    optimizer_solver: Literal["gurobi", "cpsat"] = "cpsat"
    optimizer_time_limit_s: float = Field(default=20.0, gt=0, le=300)
    optimizer_workers: int = Field(default=4, ge=1, le=32)
    # Queued/running runs older than this are treated as orphaned (server restarted).
    optimizer_stale_after_s: int = Field(default=180, ge=30)

    # Optional server-side AI intake; never expose this secret to the browser.
    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-4o-mini"

    # Injected by Railway at build/deploy time.
    railway_git_commit_sha: str | None = None

    # ------------------------------------------------------------------ validators
    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [o.strip().rstrip("/") for o in value.split(",") if o.strip()]
        return value

    @field_validator("cors_origins")
    @classmethod
    def _no_wildcard(cls, value: list[str]) -> list[str]:
        if "*" in value:
            raise ValueError("CORS_ORIGINS must list explicit origins; '*' is not allowed")
        return value

    @field_validator("supabase_url")
    @classmethod
    def _strip_slash(cls, value: str | None) -> str | None:
        return value.rstrip("/") if value else value

    @field_validator("log_level")
    @classmethod
    def _upper_level(cls, value: str) -> str:
        return value.upper()

    @model_validator(mode="after")
    def _stale_after_exceeds_solve(self) -> Settings:
        # A run takes at most ~2x the time limit (solve + infeasibility explanation).
        if self.optimizer_stale_after_s <= 2 * self.optimizer_time_limit_s:
            raise ValueError("OPTIMIZER_STALE_AFTER_S must exceed 2 x OPTIMIZER_TIME_LIMIT_S")
        return self

    @model_validator(mode="after")
    def _require_in_production(self) -> Settings:
        if self.environment == "production":
            missing = [
                name
                for name, val in (
                    ("DATABASE_URL", self.database_url),
                    ("SUPABASE_URL", self.supabase_url),
                )
                if not val
            ]
            if missing:
                raise ValueError(f"Missing required settings in production: {', '.join(missing)}")
        return self

    # ------------------------------------------------------------------ derived
    @property
    def version(self) -> str:
        return self.railway_git_commit_sha or "dev"

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def docs_enabled(self) -> bool:
        if self.enable_docs is not None:
            return self.enable_docs
        return not self.is_production

    @property
    def jwt_issuer(self) -> str | None:
        return f"{self.supabase_url}/auth/v1" if self.supabase_url else None

    @property
    def jwks_url(self) -> str | None:
        return f"{self.supabase_url}/auth/v1/.well-known/jwks.json" if self.supabase_url else None

    def sqlalchemy_database_url(self) -> tuple[URL, DatabaseSSL] | None:
        """Return the asyncpg URL plus the effective SSL mode.

        Accepts the plain `postgresql://` URL Supabase shows in the dashboard.
        `sslmode` is stripped from the query (asyncpg doesn't understand it)
        and passed through as the `ssl` connect arg instead.
        """
        if self.database_url is None:
            return None
        url = make_url(self.database_url.get_secret_value())
        if url.drivername in (
            "postgres",
            "postgresql",
            "postgresql+psycopg",
            "postgresql+psycopg2",
        ):
            url = url.set(drivername="postgresql+asyncpg")
        ssl: DatabaseSSL = self.database_ssl
        query_ssl = url.query.get("sslmode") or url.query.get("ssl")
        if isinstance(query_ssl, str) and query_ssl in (
            "disable",
            "allow",
            "prefer",
            "require",
            "verify-ca",
            "verify-full",
        ):
            ssl = query_ssl  # type: ignore[assignment]
        url = url.difference_update_query(["sslmode", "ssl"])
        return url, ssl


@lru_cache
def get_settings() -> Settings:
    return Settings()
