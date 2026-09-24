"""OIDC verification against a REAL HTTP issuer (Phase 5.12, Parts 3-5).

Phase 5.4 verified token validation with `StaticKeyVerifier`, which is handed
a key object directly. That never exercises `PyJWKClient`, the HTTP fetch,
the JWKS document format, `kid` selection, or the refresh-on-unknown-kid
path - precisely the parts that break in a real deployment.

These tests drive `OIDCTokenVerifier` against a real local HTTP issuer
serving a real JWKS document (`tests/support/oidc_issuer.py`).

**What this does NOT establish.** Rutgers' production SSO has not been
contacted. Rutgers publishes CAS/Shibboleth/LDAP rather than OIDC, no client
registration exists for this project, and no credential is available in this
environment. Every result here is "the OIDC code path is correct against a
real issuer", never "Rutgers works".
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from support.oidc_issuer import LocalOIDCIssuer  # noqa: E402

from app.api.auth import (  # noqa: E402
    ALLOWED_ALGORITHMS,
    AuthenticationError,
    OIDCTokenVerifier,
)

AUDIENCE = "coursepilot"


@pytest.fixture
def issuer():
    with LocalOIDCIssuer() as live:
        yield live


def _verifier(issuer: LocalOIDCIssuer, **overrides) -> OIDCTokenVerifier:
    kwargs = {
        "issuer": issuer.issuer_url,
        "audience": AUDIENCE,
        "jwks_uri": issuer.jwks_uri,
    }
    kwargs.update(overrides)
    return OIDCTokenVerifier(**kwargs)


# ==========================================================================
# the happy path, over real HTTP
# ==========================================================================


def test_a_real_token_is_verified_through_a_real_jwks_fetch(issuer) -> None:
    verifier = _verifier(issuer)
    before = issuer.requests

    principal = verifier.verify(issuer.mint(subject="netid-abc"))

    assert principal.subject == "netid-abc"
    assert principal.issuer == issuer.issuer_url
    assert principal.provider == "oidc"
    assert issuer.requests > before, "the JWKS endpoint was never contacted"


def test_only_a_minimal_claim_subset_survives(issuer) -> None:
    """A claim that reaches the application is one something may trust."""
    principal = _verifier(issuer).verify(
        issuer.mint(email="a@example.edu", groups=["admin"], is_admin=True,
                    netid="real-netid")
    )
    assert principal.claims == {"email": "a@example.edu"}
    assert "groups" not in principal.claims
    assert "is_admin" not in principal.claims
    assert "netid" not in principal.claims


def test_the_subject_is_hashed_for_logging(issuer) -> None:
    principal = _verifier(issuer).verify(issuer.mint(subject="netid-secret"))
    assert "netid-secret" not in principal.redacted_subject()


# ==========================================================================
# Part 3 - every invalid case must fail closed
# ==========================================================================


def test_a_token_signed_by_an_unpublished_key_is_rejected(issuer) -> None:
    """The signature check, exercised through a real key lookup."""
    rogue = LocalOIDCIssuer()
    rogue.add_key(kid=issuer.keys[0].kid)      # same kid, different key
    forged = rogue.mint(issuer=issuer.issuer_url, audience=AUDIENCE)

    with pytest.raises(AuthenticationError):
        _verifier(issuer).verify(forged)


def test_a_wrong_issuer_is_rejected(issuer) -> None:
    with pytest.raises(AuthenticationError):
        _verifier(issuer).verify(issuer.mint(issuer="https://evil.example.com"))


def test_a_wrong_audience_is_rejected(issuer) -> None:
    with pytest.raises(AuthenticationError):
        _verifier(issuer).verify(issuer.mint(audience="some-other-app"))


def test_an_expired_token_is_rejected(issuer) -> None:
    with pytest.raises(AuthenticationError):
        _verifier(issuer).verify(issuer.mint(expires_in=-3600))


def test_a_not_yet_valid_token_is_rejected(issuer) -> None:
    """`nbf` beyond the 60 s skew allowance."""
    with pytest.raises(AuthenticationError):
        _verifier(issuer).verify(issuer.mint(not_before=3600))


def test_a_token_within_the_clock_skew_allowance_is_accepted(issuer) -> None:
    """Skew is deliberately small, but it must actually work."""
    principal = _verifier(issuer).verify(issuer.mint(not_before=-10))
    assert principal.subject


def test_a_token_without_a_subject_is_rejected(issuer) -> None:
    with pytest.raises(AuthenticationError):
        _verifier(issuer).verify(issuer.mint(subject=None))


def test_an_unsupported_algorithm_is_rejected(issuer) -> None:
    """`alg: none`, assembled by hand because PyJWT will not encode it here."""
    import base64
    import json

    header = base64.urlsafe_b64encode(
        json.dumps({"alg": "none", "typ": "JWT",
                    "kid": issuer.keys[0].kid}).encode()).rstrip(b"=")
    payload = base64.urlsafe_b64encode(
        json.dumps({"iss": issuer.issuer_url, "aud": AUDIENCE, "sub": "x",
                    "exp": 9999999999}).encode()).rstrip(b"=")
    unsigned = (header + b"." + payload + b".").decode()

    with pytest.raises(AuthenticationError):
        _verifier(issuer).verify(unsigned)


def test_malformed_tokens_are_rejected(issuer) -> None:
    verifier = _verifier(issuer)
    for junk in ("", "not.a.jwt", "aaa.bbb", "....", "Bearer", "a" * 500):
        with pytest.raises(AuthenticationError):
            verifier.verify(junk)


def test_a_token_with_no_kid_is_rejected_when_keys_are_identified_by_kid(
    issuer,
) -> None:
    """A real JWKS with a `kid` requires the token to name one."""
    with pytest.raises(AuthenticationError):
        _verifier(issuer).verify(issuer.mint(include_kid=False))


def test_only_asymmetric_algorithms_are_accepted() -> None:
    assert "none" not in ALLOWED_ALGORITHMS
    assert not any(a.startswith("HS") for a in ALLOWED_ALGORITHMS)


# ==========================================================================
# Part 5 - key rotation, against a real rotating issuer
# ==========================================================================


def test_a_newly_published_key_is_not_verifiable_during_the_refresh_cooldown(
    issuer,
) -> None:
    """**A real finding, and deliberate upstream behaviour.**

    PyJWKClient refreshes the JWK set when it meets an unknown `kid` - but
    only outside a `cooldown_duration` window (30 s by default, measured
    from the last successful fetch). Inside that window the refresh is
    suppressed and the token is refused.

    That cooldown is a DoS protection: without it, anyone could force
    unbounded JWKS fetches by sending tokens with random `kid` values.
    Removing it to make rotation instant would weaken validation
    infrastructure to buy convenience, so it is documented, not changed.

    Operationally this is fine because identity providers publish a new key
    before they begin signing with it. It is a finding to be aware of, not a
    defect to fix here.
    """
    verifier = _verifier(issuer)
    original = issuer.keys[0]
    assert verifier.verify(issuer.mint(key=original)).subject   # fetch + cache

    rotated = issuer.add_key()                                  # the IdP rotates

    with pytest.raises(AuthenticationError):
        verifier.verify(issuer.mint(key=rotated))


def test_a_newly_published_key_becomes_verifiable_once_the_cooldown_elapses(
    issuer,
) -> None:
    """**The key-rotation property the brief actually states.**

    A valid token signed by a newly published legitimate key must
    *eventually* verify, without weakening validation and without a restart.

    The elapsed cooldown is simulated rather than waited out - the test
    advances the client's notion of when it last fetched, which is the same
    thing 30 s of wall clock would do. Nothing about validation is relaxed:
    the signature, issuer, audience and expiry are all still checked.
    """
    verifier = _verifier(issuer)
    original = issuer.keys[0]
    verifier.verify(issuer.mint(key=original))

    rotated = issuer.add_key()
    fetches_before = issuer.requests

    # Simulate the cooldown having elapsed.
    verifier._jwks._last_successful_fetch = None

    principal = verifier.verify(issuer.mint(key=rotated))

    assert principal.subject
    assert issuer.requests > fetches_before, (
        "an unknown kid outside the cooldown must trigger a JWKS refetch"
    )
    # The old key still verifies while both are published - an overlap
    # window is how real rotations avoid dropping in-flight tokens.
    assert verifier.verify(issuer.mint(key=original)).subject

    # And validation is still doing its job after the refresh.
    with pytest.raises(AuthenticationError):
        verifier.verify(issuer.mint(key=rotated, audience="wrong-audience"))


def test_the_refresh_cooldown_is_what_bounds_rotation_latency(issuer) -> None:
    """Pin the upstream defaults this behaviour depends on.

    If a dependency upgrade changes them, the rotation window changes with
    them, and this fails rather than silently shifting.
    """
    verifier = _verifier(issuer)
    # The window inside which an unknown kid will NOT trigger a refetch.
    assert verifier._jwks.cooldown_duration == 30
    # The JWKS fetch bound on the request path - inherited, not chosen.
    assert verifier._jwks.timeout == 30


def test_a_retired_key_stops_verifying(issuer) -> None:
    """Rotation is only useful if the old key can actually be withdrawn."""
    original = issuer.keys[0]
    rotated = issuer.add_key()
    issuer.retire_all_but(rotated)

    # A fresh verifier models a process that has never cached the old key.
    # The retired key must not verify - otherwise withdrawal is impossible
    # and rotation buys nothing.
    with pytest.raises(AuthenticationError):
        _verifier(issuer).verify(issuer.mint(key=original))

    # The surviving key still does.
    assert _verifier(issuer).verify(issuer.mint(key=rotated)).subject


def test_keys_are_cached_so_steady_state_does_not_refetch(issuer) -> None:
    """Caching is what makes per-request verification affordable."""
    verifier = _verifier(issuer)
    verifier.verify(issuer.mint())
    after_first = issuer.requests

    for _ in range(10):
        verifier.verify(issuer.mint())

    assert issuer.requests == after_first, (
        f"expected no refetch in steady state, saw "
        f"{issuer.requests - after_first} extra"
    )


# ==========================================================================
# Part 4 - JWKS failure matrix, with real HTTP faults
# ==========================================================================


def test_jwks_unavailable_fails_closed(issuer) -> None:
    """A verifier that cannot fetch keys must refuse, never admit."""
    verifier = _verifier(issuer)
    token = issuer.mint()
    issuer.mode = "http_500"

    fresh = _verifier(issuer)                      # no cached keys
    with pytest.raises(AuthenticationError):
        fresh.verify(token)


def test_malformed_jwks_fails_closed(issuer) -> None:
    issuer.mode = "malformed"
    with pytest.raises(AuthenticationError):
        _verifier(issuer).verify(issuer.mint())


def test_an_empty_jwks_fails_closed(issuer) -> None:
    token = issuer.mint()
    issuer.mode = "empty_keys"
    with pytest.raises(AuthenticationError):
        _verifier(issuer).verify(token)


def test_a_dead_issuer_endpoint_fails_closed() -> None:
    """Nothing listening at all - DNS/connection failure."""
    dead = LocalOIDCIssuer().start()
    url, jwks = dead.issuer_url, dead.jwks_uri
    token = dead.mint()
    dead.stop()                                    # the socket is now closed

    verifier = OIDCTokenVerifier(issuer=url, audience=AUDIENCE, jwks_uri=jwks)
    with pytest.raises(AuthenticationError):
        verifier.verify(token)


def test_a_cached_key_survives_a_temporary_jwks_outage(issuer) -> None:
    """Availability without weakening validation.

    A verifier that has already fetched the key keeps verifying while the
    endpoint is down. The token's signature, issuer, audience and expiry are
    all still checked - the outage changes nothing about the decision, only
    about whether a network call is needed to make it.
    """
    verifier = _verifier(issuer)
    verifier.verify(issuer.mint())                 # caches the key

    issuer.mode = "http_500"
    principal = verifier.verify(issuer.mint())
    assert principal.subject

    # ... and an invalid token is still refused during the outage.
    with pytest.raises(AuthenticationError):
        verifier.verify(issuer.mint(audience="wrong"))


def test_a_slow_jwks_endpoint_does_not_hang_forever(issuer) -> None:
    """A JWKS fetch on the request path must be bounded.

    Measured finding: PyJWKClient defaults to `timeout=30` seconds, and
    CoursePilot does not override it. So the fetch IS bounded - but 30 s is
    a long time to hold a request thread, and it is the default rather than
    a decision anyone made. Recorded in the deployment findings.
    """
    import time

    issuer.delay_seconds = 1.0
    verifier = _verifier(issuer)
    started = time.perf_counter()
    verifier.verify(issuer.mint())
    elapsed = time.perf_counter() - started

    assert elapsed >= 1.0
    assert elapsed < 30.0
    # Pin the inherited bound so a dependency change is visible.
    assert verifier._jwks.timeout == 30


# ==========================================================================
# the full chain, over a real issuer
# ==========================================================================


def test_build_verifier_wires_a_real_oidc_verifier(issuer, settings) -> None:
    from app.api.auth import build_verifier

    configured = settings.model_copy(update={
        "auth_provider": "oidc",
        "oidc_issuer": issuer.issuer_url,
        "oidc_audience": AUDIENCE,
        "oidc_jwks_uri": issuer.jwks_uri,
    })
    verifier = build_verifier(configured)
    assert isinstance(verifier, OIDCTokenVerifier)
    assert verifier.verify(issuer.mint(subject="netid-wired")).subject == "netid-wired"


def test_incomplete_oidc_configuration_still_fails_closed(issuer, settings) -> None:
    from app.api.auth import build_verifier

    for missing in ("oidc_issuer", "oidc_audience", "oidc_jwks_uri"):
        configured = settings.model_copy(update={
            "auth_provider": "oidc",
            "oidc_issuer": issuer.issuer_url,
            "oidc_audience": AUDIENCE,
            "oidc_jwks_uri": issuer.jwks_uri,
            missing: None,
        })
        assert build_verifier(configured) is None, missing
