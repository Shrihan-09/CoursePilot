"""API security: identity, isolation, rate limits, cost controls (Phase 5.3).

> Security wraps CoursePilot. Security does not redefine CoursePilot.

The load-bearing test in this file is
`test_a_caller_cannot_name_another_student`: it asserts the request schema
has no field capable of naming a student. That is a stronger guarantee than
an authorization check, because there is no code path to forget.

No network, no API key, no infrastructure.
"""

from __future__ import annotations

import logging

import pytest
from httpx import AsyncClient

from app.api.security import (
    MAX_SUBJECT_CHARS,
    Principal,
    RateLimitExceeded,
    SlidingWindowLimiter,
    get_principal,
)
from app.core.config import Environment

ENDPOINT = "/api/v1/explanations/recommendation"


def auth(subject: str = "subject-a") -> dict[str, str]:
    return {"Authorization": f"Bearer dev:{subject}"}


# ==========================================================================
# authentication
# ==========================================================================


async def test_missing_credentials_are_rejected(client: AsyncClient) -> None:
    response = await client.post(ENDPOINT, json={"course_key": "01:198:344"})
    assert response.status_code == 401
    assert response.headers.get("www-authenticate") == "Bearer"


async def test_malformed_scheme_is_rejected(client: AsyncClient) -> None:
    response = await client.post(
        ENDPOINT,
        json={"course_key": "01:198:344"},
        headers={"Authorization": "Basic abc123"},
    )
    assert response.status_code == 401


async def test_empty_bearer_is_rejected(client: AsyncClient) -> None:
    response = await client.post(
        ENDPOINT,
        json={"course_key": "01:198:344"},
        headers={"Authorization": "Bearer "},
    )
    assert response.status_code == 401


async def test_unknown_credential_format_is_rejected(client: AsyncClient) -> None:
    """A token that is not a dev token has no verifier, so it fails closed."""
    response = await client.post(
        ENDPOINT,
        json={"course_key": "01:198:344"},
        headers={"Authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.fake.jwt"},
    )
    assert response.status_code == 401


def test_dev_auth_is_off_by_default() -> None:
    """A fresh clone has no authentication enabled; tests opt in explicitly.

    Checks the DECLARED field default rather than constructing Settings:
    this suite sets DEV_AUTH_ENABLED in the environment, which would mask
    the very default being asserted.
    """
    from app.core.config import Settings

    assert Settings.model_fields["dev_auth_enabled"].default is False


def test_dev_auth_is_refused_in_production(settings) -> None:
    """Belt and braces: even DEV_AUTH_ENABLED=true must not work in prod."""
    from fastapi import HTTPException

    production = settings.model_copy(
        update={"dev_auth_enabled": True, "coursepilot_env": Environment.PRODUCTION}
    )
    with pytest.raises(HTTPException) as caught:
        get_principal(authorization="Bearer dev:someone", settings=production)
    assert caught.value.status_code == 401


def test_disabled_dev_auth_fails_closed(settings) -> None:
    from fastapi import HTTPException

    disabled = settings.model_copy(update={"dev_auth_enabled": False})
    with pytest.raises(HTTPException) as caught:
        get_principal(authorization="Bearer dev:someone", settings=disabled)
    assert caught.value.status_code == 401


def test_oversized_subject_is_rejected(settings) -> None:
    from fastapi import HTTPException

    enabled = settings.model_copy(update={"dev_auth_enabled": True})
    with pytest.raises(HTTPException):
        get_principal(
            authorization=f"Bearer dev:{'x' * 300}",
            settings=enabled,
        )


# ==========================================================================
# authorization / data isolation
# ==========================================================================


def test_a_caller_cannot_name_another_student() -> None:
    """THE isolation guarantee, and why it needs no ownership column.

    The request schema has no field that can name a student. Student A cannot
    request Student B's audit because there is nowhere to put "B" - an
    absence, not a check somebody has to remember to write.
    """
    from app.api.v1.routes.explanations import RecommendationExplanationRequest

    fields = set(RecommendationExplanationRequest.model_fields)
    assert fields == {"course_key", "explanation_type"}
    for naming in ("student_ref", "student_id", "user_id", "netid", "subject"):
        assert naming not in fields


async def test_student_ref_in_the_body_is_rejected(authenticated_client: AsyncClient) -> None:
    """Even attempting to supply one is a 422, not a silently ignored field."""
    response = await authenticated_client.post(
        ENDPOINT,
        json={"course_key": "01:198:344", "student_ref": "student-b"},
    )
    assert response.status_code == 422


def test_identity_comes_only_from_the_credential() -> None:
    """The subject is read from the credential, never from a request.

    Exercises the verifier directly: account resolution needs a database and
    is covered by the db-marked tests.
    """
    from app.api.auth import DevSubjectVerifier

    authenticated = DevSubjectVerifier().verify("dev:ru-abc")
    assert authenticated.subject == "ru-abc"
    assert authenticated.provider == "dev"


async def test_unlinked_account_is_a_controlled_state(
    authenticated_client: AsyncClient,
) -> None:
    """Authenticated, owning nothing: a normal state, reported explicitly."""
    response = await authenticated_client.post(
        ENDPOINT, json={"course_key": "01:198:344"}
    )
    assert response.status_code == 409


# ==========================================================================
# rate limiting
# ==========================================================================


def test_limiter_allows_below_the_limit() -> None:
    limiter = SlidingWindowLimiter(limit=3, window_seconds=60)
    for i in range(3):
        limiter.check("k", now=100.0 + i)


def test_limiter_rejects_above_the_limit() -> None:
    limiter = SlidingWindowLimiter(limit=2, window_seconds=60)
    limiter.check("k", now=100.0)
    limiter.check("k", now=100.1)
    with pytest.raises(RateLimitExceeded) as caught:
        limiter.check("k", now=100.2)
    assert caught.value.status_code == 429
    assert "Retry-After" in caught.value.headers


def test_limiter_window_slides() -> None:
    """A window that never expires is a quota, not a rate limit."""
    limiter = SlidingWindowLimiter(limit=1, window_seconds=10)
    limiter.check("k", now=100.0)
    with pytest.raises(RateLimitExceeded):
        limiter.check("k", now=105.0)
    limiter.check("k", now=111.0)      # the first hit has aged out


def test_limits_are_per_identity() -> None:
    """One caller must not be able to exhaust another's budget."""
    limiter = SlidingWindowLimiter(limit=1, window_seconds=60)
    limiter.check("a", now=100.0)
    limiter.check("b", now=100.0)
    with pytest.raises(RateLimitExceeded):
        limiter.check("a", now=100.1)


async def test_endpoint_returns_429_when_over_the_limit(
    authenticated_client: AsyncClient, settings
) -> None:
    from app.api import security

    security.reset_limiters()
    security._request_limiter = security.SlidingWindowLimiter(limit=2, window_seconds=60)

    body = {"course_key": "01:198:344"}
    first = await authenticated_client.post(ENDPOINT, json=body)
    second = await authenticated_client.post(ENDPOINT, json=body)
    third = await authenticated_client.post(ENDPOINT, json=body)

    # The first two get as far as ownership resolution (409: unlinked).
    assert first.status_code in (200, 404, 409)
    assert second.status_code in (200, 404, 409)
    assert third.status_code == 429
    assert third.headers.get("retry-after")
    security.reset_limiters()


def test_model_budget_is_separate_from_the_request_budget(settings) -> None:
    """Model calls cost money; deterministic explanations do not. One shared
    budget would be either too loose to protect spend or too tight to allow
    ordinary use."""
    assert settings.rate_limit_model_calls < settings.rate_limit_requests


# ==========================================================================
# AI cost controls
# ==========================================================================


def test_client_cannot_select_the_provider() -> None:
    from app.api.v1.routes.explanations import RecommendationExplanationRequest

    fields = set(RecommendationExplanationRequest.model_fields)
    assert "provider" not in fields
    assert "model" not in fields


async def test_provider_field_in_the_body_is_rejected(authenticated_client: AsyncClient) -> None:
    response = await authenticated_client.post(
        ENDPOINT, json={"course_key": "01:198:344", "provider": "anthropic"}
    )
    assert response.status_code == 422


async def test_token_overrides_are_rejected(authenticated_client: AsyncClient) -> None:
    for field in ("max_tokens", "temperature", "max_output_tokens"):
        response = await authenticated_client.post(
            ENDPOINT, json={"course_key": "01:198:344", field: 1_000_000}
        )
        assert response.status_code == 422, field


def test_output_tokens_are_server_owned(settings) -> None:
    from app.services.explanations.providers import build_explanation_model

    configured = settings.model_copy(
        update={"explanation_provider": "echo", "explanation_max_output_tokens": 321}
    )
    model = build_explanation_model(configured)
    assert model.max_output_tokens == 321


def test_context_is_truncated_to_a_server_bound(settings, caplog) -> None:
    """A pathological catalog entry costs tokens, not correctness."""
    from app.services.explanations.model import ModelRequest
    from app.services.explanations.providers import LLMProviderExplanationModel

    captured: dict[str, str] = {}

    class Recording:
        name = "recording"

        async def complete(self, *, role, messages, system=None, schema=None, max_tokens=0):
            from app.llm.base import Completion

            captured["context"] = messages[0].content
            captured["max_tokens"] = max_tokens
            return Completion(text="{}", model="m", provider="recording")

    model = LLMProviderExplanationModel(
        Recording(), max_output_tokens=99, max_context_chars=50
    )
    model.generate(
        ModelRequest(system_prompt="s", context="x" * 5_000, explanation_type=None)
    )
    assert len(captured["context"]) == 50
    assert captured["max_tokens"] == 99


def test_retry_budget_is_low_and_configured(settings) -> None:
    """Every retry is another billable call, so one user request must not fan
    out into several."""
    assert settings.explanation_provider_retries <= 2
    assert settings.explanation_max_provider_calls == 1


def test_one_request_makes_at_most_one_provider_call() -> None:
    """A rejected response must not trigger a second, 'corrective' call."""
    from app.services.explanations import RecommendationExplanationService, ScriptedModel

    model = ScriptedModel(["not json", '{"summary": "second chance"}'])
    from tests.test_explanation_api import _evidence

    _evidence_obj, audit = _evidence()
    service = RecommendationExplanationService(documents_by_key={}, model=model)
    outcome = service.explain_recommendation(audit, "01:198:344")

    assert len(model.calls) == 1          # not two
    assert outcome.used_model is False
    assert len(model.responses) == 1      # the second was never consumed


# ==========================================================================
# provider timeout and failure
# ==========================================================================


def test_provider_timeout_never_produces_an_invented_explanation() -> None:
    from app.services.explanations import RecommendationExplanationService
    from tests.test_explanation_api import _evidence

    class Hanging:
        name = "hanging"

        def is_available(self):
            return True

        def generate(self, request):
            raise TimeoutError("deadline exceeded")

    _evidence_obj, audit = _evidence()
    outcome = RecommendationExplanationService(
        documents_by_key={}, model=Hanging()
    ).explain_recommendation(audit, "01:198:344")

    assert outcome.used_model is False
    assert outcome.explanation.generated_by == "deterministic"
    assert outcome.explanation.reasons


def test_timeout_is_bounded_by_configuration(settings) -> None:
    assert 0 < settings.explanation_timeout_seconds <= 60


# ==========================================================================
# privacy
# ==========================================================================


def test_principal_is_hashed_for_logs() -> None:
    import uuid

    account_id = uuid.UUID(int=7)
    principal = Principal(
        account_id=account_id, subject="ru-netid-123", issuer="i", provider="dev"
    )
    redacted = principal.redacted()
    assert "ru-netid-123" not in redacted
    assert str(account_id) not in redacted
    assert len(redacted) == 12
    # Stable, so requests can still be correlated.
    assert redacted == Principal(
        account_id=account_id, subject="other", issuer="j", provider="oidc"
    ).redacted()


async def test_logs_omit_credentials_and_academic_content(
    authenticated_client: AsyncClient, caplog
) -> None:
    caplog.set_level(logging.INFO)
    await authenticated_client.post(ENDPOINT, json={"course_key": "01:198:344"})
    text = "\n".join(
        record.getMessage() + str(getattr(record, "__dict__", {})) for record in caplog.records
    )
    assert "secret-netid" not in text
    assert "devtoken" not in text
    assert "Bearer" not in text


def test_no_secret_appears_in_a_provider_error() -> None:
    from app.llm.base import ProviderNotConfiguredError
    from app.llm.providers.anthropic import AnthropicProvider

    with pytest.raises(ProviderNotConfiguredError) as caught:
        AnthropicProvider(api_key=None, model="m")
    assert "sk-" not in str(caught.value)


# ==========================================================================
# CORS surface
# ==========================================================================


def test_cors_is_not_a_wildcard(settings) -> None:
    """allow_origins=["*"] with allow_credentials=True is the classic mistake."""
    assert "*" not in settings.cors_origins
    assert settings.cors_origins == ["http://localhost:3000"]


# ==========================================================================
# the deterministic boundary still holds
# ==========================================================================


def test_security_layer_does_not_import_the_degree_engine() -> None:
    """Security wraps CoursePilot; it does not redefine it."""
    import ast
    import pathlib

    from app.api import security

    tree = ast.parse(pathlib.Path(security.__file__).read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert not any(name.startswith("app.services.audit") for name in imported)
    assert not any(name.startswith("app.services.explanations") for name in imported)
