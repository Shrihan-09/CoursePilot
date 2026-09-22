"""Application settings.

Single source of configuration truth. Nothing else in the backend reads
`os.environ` directly — that keeps configuration testable and makes every
knob discoverable from one place.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


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
    # NoDecode is required: pydantic-settings JSON-decodes complex types (list,
    # dict) from a dotenv file BEFORE field validators run, so the documented
    # comma-separated form (CORS_ORIGINS=http://a,http://b) would raise a
    # SettingsError instead of reaching _split_origins below. NoDecode hands
    # the raw string to the validator, which is what we want.
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )

    # --- LLM ---
    # "echo" is a deterministic no-network fake. It is the default so that a
    # fresh clone runs, and CI runs, without any API key present.
    llm_provider: str = "echo"
    llm_model_planner: str = "claude-opus-5"
    llm_model_router: str = "claude-opus-5"
    llm_effort: str = "high"
    anthropic_api_key: str | None = None

    # --- Grounded explanations (Phase 5.2) ---
    # Deliberately SEPARATE from llm_provider: the explanation layer and the
    # (future) agent layer are different consumers with different risk
    # profiles, and enabling one must not silently enable the other.
    # Credentials and model names are NOT duplicated - they are read from the
    # llm_* settings above.
    #
    # "none" is the default so a fresh clone and CI run with no API key and
    # take the deterministic path, which is Phase 5.1's safety property.
    #   none      - deterministic explanations only
    #   echo      - non-network fake provider, for exercising the model path
    #   anthropic - live provider (requires ANTHROPIC_API_KEY)
    explanation_provider: str = "none"
    explanation_timeout_seconds: float = 30.0
    # Upper bound on request bodies for the explanation endpoint. Prevents a
    # client from pushing unbounded text toward a provider.
    explanation_max_query_chars: int = 200
    # Server-owned model bounds. The client cannot override any of these -
    # none of them is a request field, by design.
    explanation_max_output_tokens: int = 1_500
    explanation_max_context_chars: int = 12_000
    # One provider call per request. SDK-level retries are bounded inside the
    # provider; this caps how many calls the application may initiate.
    explanation_max_provider_calls: int = 1
    # SDK-level retries for TRANSIENT failures only (connection, 408, 409,
    # 429, 5xx). Deliberately low: each retry is another billable call, so a
    # single user request must not fan out into several. Authentication and
    # validation failures are never retried - they would fail identically.
    explanation_provider_retries: int = 1

    # --- API security (Phase 5.3) ---
    # Development authentication. OFF by default, refused in production even
    # if switched on, and never a production mechanism.
    # Which verifier resolves a bearer credential.
    #   none - refuse every credential (fail closed; the default)
    #   dev  - NON-PRODUCTION subject tokens, requires dev_auth_enabled
    #   oidc - standards-based JWT verification against a JWKS endpoint
    auth_provider: str = "none"
    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    oidc_jwks_uri: str | None = None
    dev_auth_enabled: bool = False
    # In-memory, per-process. Does not coordinate across workers - see the
    # limitations note in app/api/security.py.
    rate_limit_enabled: bool = True
    rate_limit_requests: int = 60
    # Deliberately lower: every model call costs money, so spend gets its own
    # budget rather than sharing the request budget.
    rate_limit_model_calls: int = 10
    rate_limit_window_seconds: float = 60.0
    # --- Student linking (Phase 5.5) ---
    # Linking gets its own budget rather than reusing the 60-request one.
    # An administrator links a handful of people in a sitting, so a low
    # ceiling is generous for real use while sharply bounding a compromised
    # admin credential: reusing 60/min would let one leaked token reassign
    # sixty academic records a minute. It is not a brute-force control -
    # there is no secret to guess - it is a blast-radius control.
    rate_limit_link_operations: int = 10

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
