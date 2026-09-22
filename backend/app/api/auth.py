"""Authentication boundary: token verification and account resolution (5.4).

> Authentication establishes who the user is. Authorization determines what
> that user may access. The Degree Engine remains the sole authority for
> academic correctness.

## Why this is provider-neutral

```
Rutgers IdP ----\
                 >--- TokenVerifier ---> AuthenticatedPrincipal ---> UserAccount
other OIDC ----/
```

Domain code never sees a JWT. It receives a `Principal` carrying a subject,
an issuer and nothing else it does not need. Swapping identity providers
means implementing one protocol, and the Degree Engine does not learn that
OAuth exists.

## What Rutgers actually offers (researched, not assumed)

Rutgers IT publicly documents **CAS, Shibboleth (SAML) and LDAP/RAD** as its
SSO options, with an "SSO Decision Flow" for choosing between them, and
directs integrators to its identity-management support portal. **No public
OIDC/OAuth 2.0 discovery endpoint or self-service client registration is
documented**, and integration requires engaging Rutgers IT for approval and
credentials.

Consequences, stated rather than glossed:

  * **Rutgers live SSO is NOT verified** by this phase, and cannot be from
    this environment - there is no registered client and no credential;
  * the standards-based OIDC/JWT path below is the right target shape, and a
    Rutgers deployment would need either an OIDC bridge in front of
    CAS/Shibboleth or a SAML verifier implementing the same `TokenVerifier`
    protocol;
  * everything here is exercised with **controlled test tokens** signed by a
    key the test generates. That verifies the validation logic. It does not
    verify Rutgers.

## Cryptography is not hand-rolled

Signature verification, algorithm restriction and JWKS handling are PyJWT's.
This module decides *policy* - which issuer, which audience, which
algorithms, how much clock skew - and never touches a signature itself.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from app.core.config import Settings

logger = logging.getLogger(__name__)

#: Asymmetric only. An HMAC algorithm here would let anyone holding the
#: (shared) verification secret MINT tokens, and `none` is the classic JWT
#: forgery. The token declaring its own algorithm is never trusted.
ALLOWED_ALGORITHMS = ("RS256", "RS384", "RS512", "ES256", "ES384")

#: Tolerance for clock drift between this server and the identity provider.
#: Small on purpose: generous skew extends the life of a revoked token.
CLOCK_SKEW_SECONDS = 60


class AuthenticationError(Exception):
    """Verification failed. Carries no token content and no provider detail.

    Deliberately opaque: the route turns every instance into an identical
    401, because telling a caller *why* a token failed is a probing oracle.
    """


@dataclass(frozen=True, slots=True)
class AuthenticatedPrincipal:
    """A verified external identity. The ONLY thing derived from a token.

    Note what is absent: no raw token, no full claim set, no groups, no
    scopes. Anything added here becomes something the application might start
    trusting, so it stays minimal until a feature needs more.
    """

    subject: str
    issuer: str
    provider: str
    #: A deliberately small, copied subset - never the whole payload.
    claims: dict[str, Any] = field(default_factory=dict)

    def redacted_subject(self) -> str:
        """Hashed handle for logs. A `sub` can be a NetID-derived value."""
        import hashlib

        return hashlib.sha256(f"{self.issuer}|{self.subject}".encode()).hexdigest()[:12]


@runtime_checkable
class TokenVerifier(Protocol):
    """Verifies a bearer credential and returns a principal, or raises."""

    provider: str

    def verify(self, token: str) -> AuthenticatedPrincipal: ...


class OIDCTokenVerifier:
    """Standards-based JWT verification via PyJWT and a JWKS endpoint.

    Checks performed, all of them by PyJWT under policy set here:

    | check | why |
    |---|---|
    | signature | the only thing making any other claim meaningful |
    | algorithm allow-list | stops `alg: none` and HMAC confusion |
    | `iss` | a valid token from another issuer is not valid here |
    | `aud` | a token minted for another service is not for us |
    | `exp` / `nbf` | expiry is the only revocation most IdPs offer |
    | `sub` present | an identity with no subject is not an identity |

    Keys come from the provider's JWKS endpoint, cached by PyJWKClient, which
    handles rotation by refetching on an unknown `kid`.
    """

    provider = "oidc"

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        jwks_uri: str,
        algorithms: tuple[str, ...] = ALLOWED_ALGORITHMS,
    ) -> None:
        if not (issuer and audience and jwks_uri):
            raise AuthenticationError("incomplete OIDC configuration")
        self._issuer = issuer
        self._audience = audience
        self._algorithms = list(algorithms)
        from jwt import PyJWKClient

        self._jwks = PyJWKClient(jwks_uri, cache_keys=True)

    def verify(self, token: str) -> AuthenticatedPrincipal:
        import jwt

        try:
            signing_key = self._jwks.get_signing_key_from_jwt(token)
            payload = jwt.decode(
                token,
                signing_key.key,
                algorithms=self._algorithms,
                issuer=self._issuer,
                audience=self._audience,
                leeway=CLOCK_SKEW_SECONDS,
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
        except Exception as exc:
            # Only the exception CLASS is logged. A JWT error can echo the
            # token, and a token is a credential.
            logger.info("token_rejected", extra={"error_type": type(exc).__name__})
            raise AuthenticationError("token verification failed") from None

        subject = payload.get("sub")
        if not subject:
            raise AuthenticationError("token has no subject")

        return AuthenticatedPrincipal(
            subject=str(subject),
            issuer=str(payload.get("iss", self._issuer)),
            provider=self.provider,
            # Copied selectively. The full payload is not carried around.
            claims={k: payload[k] for k in ("email",) if k in payload},
        )


class StaticKeyVerifier:
    """Same policy, a fixed public key instead of a JWKS endpoint.

    For tests and for a local development IdP. It exercises the REAL
    validation path - signature, issuer, audience, expiry, algorithm
    allow-list - so the checks are tested rather than mocked away.
    """

    provider = "oidc"

    def __init__(
        self,
        *,
        public_key: Any,
        issuer: str,
        audience: str,
        algorithms: tuple[str, ...] = ALLOWED_ALGORITHMS,
    ) -> None:
        self._key = public_key
        self._issuer = issuer
        self._audience = audience
        self._algorithms = list(algorithms)

    def verify(self, token: str) -> AuthenticatedPrincipal:
        import jwt

        try:
            payload = jwt.decode(
                token,
                self._key,
                algorithms=self._algorithms,
                issuer=self._issuer,
                audience=self._audience,
                leeway=CLOCK_SKEW_SECONDS,
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
        except Exception as exc:
            logger.info("token_rejected", extra={"error_type": type(exc).__name__})
            raise AuthenticationError("token verification failed") from None

        subject = payload.get("sub")
        if not subject:
            raise AuthenticationError("token has no subject")
        return AuthenticatedPrincipal(
            subject=str(subject),
            issuer=str(payload["iss"]),
            provider=self.provider,
            claims={k: payload[k] for k in ("email",) if k in payload},
        )


class DevSubjectVerifier:
    """NON-PRODUCTION. Accepts `dev:<subject>` and verifies nothing.

    It exists so `pytest` and local development need no identity provider. It
    is not authentication and never claims to be:

      * enabled only by `DEV_AUTH_ENABLED`, which defaults to false;
      * refused outright when the environment is production;
      * issues principals under the `dev` provider, so a dev account can
        never collide with a real OIDC account - `(provider, subject)` is the
        identity, and `dev` is a different namespace from `oidc`.

    That last point matters: even if someone enabled this in production, a
    dev credential could not impersonate an existing OIDC user, because it
    would resolve to a different account.
    """

    provider = "dev"

    def __init__(self, *, issuer: str = "coursepilot-dev") -> None:
        self._issuer = issuer

    def verify(self, token: str) -> AuthenticatedPrincipal:
        prefix, _, subject = token.partition(":")
        if prefix != "dev" or not subject:
            raise AuthenticationError("invalid development credential")
        if len(subject) > 255:
            raise AuthenticationError("invalid development credential")
        return AuthenticatedPrincipal(
            subject=subject, issuer=self._issuer, provider=self.provider
        )


def build_verifier(settings: Settings) -> TokenVerifier | None:
    """Construct the configured verifier, or None if none is usable.

    Returns None rather than a permissive stand-in: the caller then fails
    **closed** with a 401. A misconfigured server that authenticates nobody is
    correct; one that authenticates everybody is a breach.
    """
    from app.core.config import Environment

    provider = (settings.auth_provider or "none").lower()

    if provider == "oidc":
        try:
            return OIDCTokenVerifier(
                issuer=settings.oidc_issuer or "",
                audience=settings.oidc_audience or "",
                jwks_uri=settings.oidc_jwks_uri or "",
            )
        except AuthenticationError as exc:
            logger.error("auth_provider_misconfigured: %s", exc)
            return None

    if provider == "dev":
        if settings.coursepilot_env is Environment.PRODUCTION:
            logger.error("dev_auth_refused_in_production")
            return None
        if not settings.dev_auth_enabled:
            return None
        return DevSubjectVerifier()

    if provider != "none":
        logger.error("unknown AUTH_PROVIDER %r; refusing all credentials", provider)
    return None


__all__ = [
    "ALLOWED_ALGORITHMS",
    "CLOCK_SKEW_SECONDS",
    "AuthenticatedPrincipal",
    "AuthenticationError",
    "DevSubjectVerifier",
    "OIDCTokenVerifier",
    "StaticKeyVerifier",
    "TokenVerifier",
    "build_verifier",
]
