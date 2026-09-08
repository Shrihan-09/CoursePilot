"""Application settings.

Single source of configuration truth. Nothing else in the backend reads
`os.environ` directly — that keeps configuration testable and makes every
knob discoverable from one place.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    LOCAL = "local"
    CI = "ci"
    STAGING = "staging"
    PRODUCTION = "production"


class RetrievalStrategy(StrEnum):
    """Selectable so we can A/B these against an eval set rather than
    assuming semantic search wins. See docs/RAG_ARCHITECTURE.md."""

    BM25 = "bm25"
    VECTOR = "vector"
    HYBRID = "hybrid"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Read the repo-root .env, not a backend-local one, so the frontend,
        # backend, and compose stack all share a single file.
        env_file=("../.env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Core ---
    coursepilot_env: Environment = Environment.LOCAL
    debug: bool = True
    log_level: str = "INFO"

    # --- Database ---
    database_url: str = "postgresql+psycopg://coursepilot:coursepilot@localhost:5432/coursepilot"
    database_url_sync: str = "postgresql+psycopg://coursepilot:coursepilot@localhost:5432/coursepilot"

    # --- API ---
    api_v1_prefix: str = "/api/v1"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    # --- LLM ---
    # "echo" is a deterministic no-network fake. It is the default so that a
    # fresh clone runs, and CI runs, without any API key present.
    llm_provider: str = "echo"
    llm_model_planner: str = "claude-opus-5"
    llm_model_router: str = "claude-opus-5"
    llm_effort: str = "high"
    anthropic_api_key: str | None = None

    # --- Retrieval (not implemented yet) ---
    retrieval_strategy: RetrievalStrategy = RetrievalStrategy.HYBRID
    reranker_enabled: bool = False
    embedding_dim: int = 1024

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v


@lru_cache
def get_settings() -> Settings:
    """Cached so settings are parsed once per process. Tests that need to
    vary configuration should call `get_settings.cache_clear()`."""
    return Settings()
