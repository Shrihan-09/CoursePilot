"""Application settings.

Single source of configuration truth. Nothing else in the backend reads
`os.environ` directly — that keeps configuration testable and makes every
knob discoverable from one place.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from typing import Annotated

from pydantic import Field, field_validator, model_validator
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

    # --- Connection pooling (Phase 5.7) ---
    # Derived from the deployment's real limits, not picked to look fast.
    # Measured: PostgreSQL max_connections=100 (3 superuser-reserved), and
    # FastAPI's default worker threadpool is 40, which is the real ceiling on
    # CONCURRENT sync sessions. Per process the two engines can hold at most
    # (pool_size + max_overflow) each, so 5+10 twice = 30 - leaving room for
    # roughly three processes plus headroom for psql and migrations.
    #
    # Deliberately NOT sized to 40 to match the threadpool: past the pool
    # limit requests QUEUE, which is a bounded, recoverable wait. Sizing the
    # pool to the threadpool would instead push the bottleneck onto Postgres,
    # where exhaustion is a hard connection error for the whole deployment.
    db_pool_size: int = 5
    db_max_overflow: int = 10
    # Bounded wait rather than SQLAlchemy's 30s default: a request that has
    # queued ten seconds for a connection has already failed the user, and a
    # timeout is a far better signal than a hang.
    db_pool_timeout: float = 10.0
    # Recycle below any plausible server-side or proxy idle timeout, so a
    # connection is retired by us rather than found dead by a query.
    db_pool_recycle_seconds: int = 1800

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

    # --- OpenAI (Phase 5.13) ---
    # Server-side only. No request field reaches either of these: a client
    # that could choose the model could choose a cheaper, weaker or
    # differently-aligned one, and the bill is the operator's.
    openai_api_key: str | None = None
    #: The adopted explanation model. A configuration value rather than a
    #: constant so switching models - or correcting an id - is a deploy
    #: setting, not a code change.
    openai_model: str = "gpt-5.6-luna"

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

    @model_validator(mode="after")
    def _production_is_never_debug(self) -> "Settings":
        """Phase 5.12: `debug` cannot be true in production.

        `debug` defaulted to True and nothing lowered it for production,
        which had two consequences a deployment would have inherited
        silently:

          * `/docs` served publicly (`app/main.py` gates it on `debug`);
          * **`create_async_engine(echo=debug)`**, which makes SQLAlchemy log
            every statement WITH ITS PARAMETERS. Verified: a query bound to
            a catalog year emitted `{'y': '2026-2027'}` into the log. In a
            real deployment that stream would carry `external_ref`, account
            ids and academic values - exactly the second copy of sensitive
            data Phase 5.10 existed to prevent.

        Forced rather than validated-and-refused: a deployment that boots
        with debug quietly off is strictly better than one that refuses to
        boot, and there is no legitimate reason to want SQL echo in
        production. The override is logged by `audit_production_settings`.
        """
        if self.coursepilot_env is Environment.PRODUCTION and self.debug:
            object.__setattr__(self, "debug", False)
        return self

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


#: Configuration that is fine locally and dangerous in production. Reported
#: at startup rather than enforced, except where enforcement is free and
#: unambiguous (see `_production_is_never_debug`).
def audit_production_settings(settings: "Settings") -> list[str]:
    """Findings a production deployment should act on. Empty is good.

    Deliberately a report rather than a refusal for everything except SQL
    echo. Refusing to start on a permissive CORS list would strand a
    deployment over something an operator can fix in a minute; refusing to
    start with no authentication configured would be defensible but changes
    the documented fail-closed behaviour, which already refuses every
    credential rather than admitting anyone.
    """
    if settings.coursepilot_env is not Environment.PRODUCTION:
        return []

    findings: list[str] = []

    if (settings.auth_provider or "none").lower() == "none":
        findings.append(
            "auth_provider is 'none': every credential will be refused and no "
            "user can authenticate"
        )
    if settings.auth_provider == "dev":
        findings.append(
            "auth_provider is 'dev': development credentials are refused in "
            "production, so no user can authenticate"
        )
    if settings.dev_auth_enabled:
        findings.append("dev_auth_enabled is true in production")

    if (settings.auth_provider or "").lower() == "oidc":
        for name in ("oidc_issuer", "oidc_audience", "oidc_jwks_uri"):
            if not getattr(settings, name):
                findings.append(f"{name} is not configured for the oidc provider")

    if any("localhost" in origin or "127.0.0.1" in origin
           for origin in settings.cors_origins):
        findings.append(f"cors_origins contains a local origin: {settings.cors_origins}")
    if "*" in settings.cors_origins:
        findings.append("cors_origins contains '*' with credentials enabled")

    if not settings.rate_limit_enabled:
        findings.append("rate_limit_enabled is false")

    if settings.explanation_provider == "anthropic" and not settings.anthropic_api_key:
        findings.append(
            "explanation_provider is 'anthropic' but no API key is configured"
        )
    if settings.explanation_provider == "openai" and not settings.openai_api_key:
        findings.append(
            "explanation_provider is 'openai' but no API key is configured"
        )

    if settings.database_url_sync.startswith("sqlite"):
        findings.append(
            "database_url_sync is SQLite: the audit cache and search version "
            "triggers are PostgreSQL-only"
        )

    return findings
