"""Phase 7.1 identity: OIDC verification, issuer-scoped accounts, institution policy, /me.

Driven through the REAL path: `OIDCTokenVerifier` against a local HTTP issuer
publishing a real JWKS (`tests/support/oidc_issuer.py`), the real
`get_principal`, real account resolution in PostgreSQL. What this does NOT
establish: any real identity provider (Google, Rutgers) - those need a
registered client and a real sign-in.
"""

from __future__ import annotations

import datetime as dt
import sys
import uuid
from pathlib import Path

import jwt
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).parent))

from support.oidc_issuer import LocalOIDCIssuer  # noqa: E402

from app.api.auth import (  # noqa: E402
    AuthenticationError,
    InstitutionStatus,
    OIDCTokenVerifier,
    build_verifier,
    institution_status,
)
from app.core.config import Environment, Settings, get_settings  # noqa: E402
from app.models import UserAccount  # noqa: E402

from .test_multi_program import _session, requires_db  # noqa: E402

AUDIENCE = "coursepilot-web"


@pytest.fixture
def issuer():
    with LocalOIDCIssuer() as live:
        yield live


def _settings(issuer, **over) -> Settings:
    return Settings(auth_provider="oidc", oidc_issuer=issuer.issuer_url,
                    oidc_audience=AUDIENCE, oidc_jwks_uri=issuer.jwks_uri,
                    dev_auth_enabled=False, rate_limit_enabled=False, **over)


def _app(settings: Settings):
    from app.main import create_app

    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    return app


def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _subject() -> str:
    return f"oidc-{uuid.uuid4().hex}"


def _accounts(subject: str) -> list[UserAccount]:
    with _session() as s:
        return list(s.scalars(select(UserAccount).where(UserAccount.external_subject == subject)))


# --------------------------------------------------------------------------
# verification (no database)
# --------------------------------------------------------------------------


@pytest.mark.parametrize("missing", ["sub", "iss", "aud", "exp", "iat"])
def test_a_token_missing_a_required_claim_is_rejected(issuer, missing) -> None:
    now = dt.datetime.now(dt.UTC)
    payload = {"iss": issuer.issuer_url, "aud": AUDIENCE, "sub": "s", "iat": now,
               "exp": now + dt.timedelta(minutes=5)}
    payload.pop(missing)
    signer = issuer.keys[0]
    token = jwt.encode(payload, signer.private_pem, algorithm="RS256",
                       headers={"kid": signer.kid})
    verifier = OIDCTokenVerifier(issuer=issuer.issuer_url, audience=AUDIENCE,
                                 jwks_uri=issuer.jwks_uri)
    with pytest.raises(AuthenticationError):
        verifier.verify(token)


def test_wrong_issuer_audience_expired_and_garbage_are_rejected(issuer) -> None:
    verifier = OIDCTokenVerifier(issuer=issuer.issuer_url, audience=AUDIENCE,
                                 jwks_uri=issuer.jwks_uri)
    assert verifier.verify(issuer.mint(audience=AUDIENCE)).subject == "netid-test"
    for bad in (issuer.mint(audience=AUDIENCE, issuer="https://evil.example"),
                issuer.mint(audience="someone-else"),
                issuer.mint(audience=AUDIENCE, expires_in=-3600),
                "not.a.jwt", ""):
        with pytest.raises(AuthenticationError):
            verifier.verify(bad)


def test_only_institution_claims_survive_verification(issuer) -> None:
    verifier = OIDCTokenVerifier(issuer=issuer.issuer_url, audience=AUDIENCE,
                                 jwks_uri=issuer.jwks_uri)
    p = verifier.verify(issuer.mint(audience=AUDIENCE, email="x@example.edu", name="X Y",
                                    picture="https://x", hd="example.edu",
                                    email_verified=True, groups=["admin"]))
    assert p.claims == {"hd": "example.edu", "email_verified": True}


# --------------------------------------------------------------------------
# institution policy - signed claims only
# --------------------------------------------------------------------------


def _policy(**over) -> Settings:
    return Settings(auth_provider="none", **over)


def test_institution_status_is_decided_by_the_signed_hd_claim_only() -> None:
    s = _policy(auth_institution_domains="Example.edu, other.example")
    assert s.auth_institution_domains == ["example.edu", "other.example"]
    assert institution_status({"hd": "example.edu", "email_verified": True}, s) == \
        InstitutionStatus.VERIFIED
    assert institution_status({"hd": "EXAMPLE.EDU"}, s) == InstitutionStatus.VERIFIED
    for claims in ({"hd": "evil.example", "email_verified": True},
                   {"email_verified": True},                 # personal account: no hd
                   {},
                   {"hd": "example.edu", "email_verified": False}):  # unverified email
        assert institution_status(claims, s) == InstitutionStatus.NOT_VERIFIED, claims
    # Nothing configured: nothing claimed, whatever the token says.
    assert institution_status({"hd": "example.edu"}, _policy()) == \
        InstitutionStatus.NOT_CONFIGURED


def test_an_email_address_is_never_evidence_of_membership(issuer) -> None:
    """A user can have an address on any domain; only `hd` is trusted."""
    verifier = OIDCTokenVerifier(issuer=issuer.issuer_url, audience=AUDIENCE,
                                 jwks_uri=issuer.jwks_uri)
    p = verifier.verify(issuer.mint(audience=AUDIENCE, email="someone@example.edu",
                                    email_verified=True))
    assert institution_status(p.claims, _policy(auth_institution_domains="example.edu")) \
        == InstitutionStatus.NOT_VERIFIED


# --------------------------------------------------------------------------
# through the API (real PostgreSQL)
# --------------------------------------------------------------------------


@requires_db
async def test_me_requires_authentication_and_reveals_no_identifier(issuer) -> None:
    app = _app(_settings(issuer))
    subject = _subject()
    async with _client(app) as ac:
        assert (await ac.get("/api/v1/me")).status_code == 401
        assert (await ac.get("/api/v1/me", headers={"Authorization": "Bearer junk"})
                ).status_code == 401
        token = issuer.mint(subject=subject, audience=AUDIENCE, email="p@example.edu")
        r = await ac.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    assert body == {"sign_in_method": "oidc", "institution": {"status": "not_configured"},
                    "has_academic_profile": False, "onboarding_complete": False}
    [account] = _accounts(subject)
    for secret in (subject, "p@example.edu", str(account.id), issuer.issuer_url):
        assert secret not in r.text


@requires_db
async def test_repeated_sign_in_maps_to_the_same_account(issuer) -> None:
    app = _app(_settings(issuer))
    subject = _subject()
    async with _client(app) as ac:
        for _ in range(3):
            token = issuer.mint(subject=subject, audience=AUDIENCE)
            r = await ac.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
            assert r.status_code == 200
    [account] = _accounts(subject)
    assert (account.identity_provider, account.identity_issuer) == ("oidc", issuer.issuer_url)


@requires_db
async def test_the_same_subject_at_another_issuer_is_another_person(issuer) -> None:
    """Providers can be added or changed without one provider's user landing on
    another provider's account: accounts are keyed by issuer too."""
    subject = _subject()
    with LocalOIDCIssuer() as second:
        for idp in (issuer, second):
            async with _client(_app(_settings(idp))) as ac:
                token = idp.mint(subject=subject, audience=AUDIENCE)
                assert (await ac.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
                        ).status_code == 200
        issuers = sorted(a.identity_issuer for a in _accounts(subject))
        assert issuers == sorted([issuer.issuer_url, second.issuer_url])


@requires_db
async def test_required_institution_refuses_before_any_account_exists(issuer) -> None:
    app = _app(_settings(issuer, auth_institution_domains="example.edu",
                         auth_require_institution=True))
    outsider, member = _subject(), _subject()
    async with _client(app) as ac:
        token = issuer.mint(subject=outsider, audience=AUDIENCE, hd="gmail-like.example",
                            email="o@example.edu", email_verified=True)
        r = await ac.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "authorization_error"
        assert _accounts(outsider) == []                     # nothing created
        token = issuer.mint(subject=member, audience=AUDIENCE, hd="example.edu",
                            email_verified=True)
        r = await ac.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200 and r.json()["institution"] == {"status": "verified"}
    assert len(_accounts(member)) == 1


@requires_db
async def test_institution_status_is_reported_without_being_required(issuer) -> None:
    app = _app(_settings(issuer, auth_institution_domains="example.edu"))
    async with _client(app) as ac:
        token = issuer.mint(subject=_subject(), audience=AUDIENCE)        # no hd
        r = await ac.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200 and r.json()["institution"] == {"status": "not_verified"}


def test_production_never_accepts_development_sign_in() -> None:
    s = Settings(coursepilot_env=Environment.PRODUCTION, auth_provider="dev",
                 dev_auth_enabled=True, debug=False)
    assert build_verifier(s) is None


async def test_production_dev_credential_gets_401() -> None:
    s = Settings(coursepilot_env=Environment.PRODUCTION, auth_provider="dev",
                 dev_auth_enabled=True, debug=False, rate_limit_enabled=False)
    async with _client(_app(s)) as ac:
        r = await ac.get("/api/v1/me", headers={"Authorization": "Bearer dev:anyone"})
    assert r.status_code == 401
