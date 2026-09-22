"""Request identity, authorization and abuse controls (Phase 5.3).

> Security wraps CoursePilot. Security does not redefine CoursePilot.

Nothing here touches the Degree Engine. It decides **who is asking** and
**how often they may ask**; what is academically true remains entirely the
engine's business.

## What CoursePilot actually has

Investigated before writing any of this:

| | Status |
|---|---|
| authentication | **none existed** - no `get_current_user`, no bearer handling, no JWT |
| rate limiting | none existed |
| middleware | CORS only |
| user table | **does not exist** |
| ownership link | **does not exist** - `Student` has no owner column |
| student identity | `Student.external_ref`, a nullable unique `String(64)` |

`Student`'s own docstring says authentication "must land before this table
holds a real person". So this phase is that landing, at the smallest size
that is actually safe.

## The identity model, and why it needs no migration

```
Authorization: Bearer <token>
        |
        v
Principal(subject=..., student_ref=...)      <- server-derived
        |
        v
Student.external_ref == principal.student_ref
```

A principal's `student_ref` is **derived from the credential**, never read
from the request body. That is the whole security property: `student_ref`
was removed from the public request schema, so a caller can no longer name a
student at all. Student A cannot ask about Student B because there is
nowhere to put "B".

That gives real data isolation **without** a `user` table or a
`student.user_id` column, so `alembic check` stays clean. A proper account
model is still the right eventual answer - see the limitations in
DATA_MODEL.md section 25 - but ownership enforced by *absence of a field* is
stronger than ownership enforced by a check someone can forget to write.

## Development tokens are opt-in and loud

Local development and tests need an identity without a Rutgers account. The
dev scheme is:

  * **off unless `DEV_AUTH_ENABLED=true`**;
  * refused outright when the environment is production, even if enabled;
  * never a production mechanism, and it says so in the docstring, the
    settings comment and the logs.

Tests override the dependency rather than bypassing it, so the authorization
path itself is exercised rather than skipped.
"""

from __future__ import annotations

import hashlib
import logging
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field

from fastapi import Depends, Header, HTTPException, Request, status

from app.api.auth import AuthenticationError, build_verifier
from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)

#: Rutgers NetIDs are short; the column is String(64). Bounding the credential
#: keeps pathological input away from the database and the model.
MAX_SUBJECT_CHARS = 64

_DEV_SCHEME = "devtoken"


@dataclass(frozen=True, slots=True)
class Principal:
    """An authenticated caller, resolved to a CoursePilot account.

    `account_id` is the STABLE internal identity and is what rate limits and
    logs key on. The provider subject can change (an identity migration, a
    recreated account); `account_id` cannot, so anything hung off it survives.

    There is deliberately no constructor path that takes a student reference
    from a request body.
    """

    account_id: uuid.UUID
    subject: str
    issuer: str
    provider: str

    def redacted(self) -> str:
        """Stable, non-reversible handle for logs.

        The account id is an internal UUID rather than personal data, but it
        is still hashed so a log leak does not hand over a join key.
        """
        return hashlib.sha256(str(self.account_id).encode()).hexdigest()[:12]


class RateLimitExceeded(HTTPException):
    def __init__(self, retry_after: int) -> None:
        super().__init__(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests.",
            headers={"Retry-After": str(retry_after)},
        )


@dataclass
class SlidingWindowLimiter:
    """A deterministic in-memory sliding-window limiter.

    **Limitations, stated rather than discovered later:** this is per-process
    and in-memory. It does not survive a restart and does not coordinate
    across workers, so N workers means N times the limit. That is acceptable
    for a single-process development deployment and is NOT a production
    control - a shared store (Redis) is the eventual answer.

    It is in-memory precisely so tests are deterministic and need no
    infrastructure.
    """

    limit: int
    window_seconds: float
    _hits: dict[str, deque[float]] = field(default_factory=lambda: defaultdict(deque))

    def check(self, key: str, *, now: float | None = None) -> None:
        """Record one hit, or raise 429. Never silently allows an overflow."""
        moment = time.monotonic() if now is None else now
        hits = self._hits[key]
        cutoff = moment - self.window_seconds
        while hits and hits[0] <= cutoff:
            hits.popleft()
        if len(hits) >= self.limit:
            retry_after = max(1, int(self.window_seconds - (moment - hits[0])) + 1)
            raise RateLimitExceeded(retry_after)
        hits.append(moment)

    def reset(self) -> None:
        self._hits.clear()


#: Two separate budgets, because they protect different things.
#:
#:   requests - protects the database and the audit engine
#:   model    - protects MONEY; every successful provider call costs tokens
#:
#: A single combined limit would either be too loose to protect spend or too
#: tight to allow ordinary deterministic use, which needs no provider at all.
_request_limiter: SlidingWindowLimiter | None = None
_model_limiter: SlidingWindowLimiter | None = None


def get_request_limiter(settings: Settings | None = None) -> SlidingWindowLimiter:
    global _request_limiter
    settings = settings or get_settings()
    if _request_limiter is None:
        _request_limiter = SlidingWindowLimiter(
            limit=settings.rate_limit_requests,
            window_seconds=settings.rate_limit_window_seconds,
        )
    return _request_limiter


def get_model_limiter(settings: Settings | None = None) -> SlidingWindowLimiter:
    global _model_limiter
    settings = settings or get_settings()
    if _model_limiter is None:
        _model_limiter = SlidingWindowLimiter(
            limit=settings.rate_limit_model_calls,
            window_seconds=settings.rate_limit_window_seconds,
        )
    return _model_limiter


def reset_limiters() -> None:
    """Test hook. Not called by application code."""
    global _request_limiter, _model_limiter
    _request_limiter = None
    _model_limiter = None


def _unauthenticated(detail: str) -> HTTPException:
    # WWW-Authenticate is part of a correct 401, and the detail never says
    # WHY a credential failed - "no such student" and "wrong token" must look
    # identical or the endpoint becomes a student-existence oracle.
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_principal(
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> Principal:
    """Verify the credential and resolve it to a CoursePilot account.

    Two steps that must stay distinct:

      1. **authentication** - a `TokenVerifier` decides the credential is
         genuine and yields an external subject;
      2. **account resolution** - that subject is mapped to a `UserAccount`,
         provisioning one on first login.

    Fails **closed**: with no verifier configured every credential is
    refused. A server that authenticates nobody is broken; one that
    authenticates everybody is breached.

    Every failure produces the same opaque 401. Distinguishing "bad
    signature" from "unknown account" would make this a probing oracle.
    """
    if not authorization:
        raise _unauthenticated("Authentication required.")

    scheme, _, credential = authorization.partition(" ")
    if scheme.lower() != "bearer" or not credential.strip():
        raise _unauthenticated("Invalid authentication scheme.")

    verifier = build_verifier(settings)
    if verifier is None:
        raise _unauthenticated("Authentication is not configured on this server.")

    try:
        authenticated = verifier.verify(credential.strip())
    except AuthenticationError:
        raise _unauthenticated("Invalid credential.") from None

    from app.db.session import get_sync_sessionmaker
    from app.services.accounts import AccountDisabled, resolve_account

    try:
        with get_sync_sessionmaker()() as session:
            account = resolve_account(session, authenticated)
            principal = Principal(
                account_id=account.id,
                subject=authenticated.subject,
                issuer=authenticated.issuer,
                provider=authenticated.provider,
            )
            session.commit()
            return principal
    except AccountDisabled:
        raise _unauthenticated("Invalid credential.") from None


def enforce_request_rate_limit(
    request: Request,
    principal: Principal = Depends(get_principal),
    settings: Settings = Depends(get_settings),
) -> Principal:
    """Per-identity request budget. Runs after authentication on purpose.

    Keying on the principal rather than the IP means one authenticated
    caller cannot exhaust everyone else's budget, and an unauthenticated
    caller never reaches the limiter at all - they were already refused.
    """
    if settings.rate_limit_enabled:
        # Keyed on the STABLE internal account id, never on the provider
        # subject, an email, or a student reference - those can change or be
        # chosen by the caller.
        get_request_limiter(settings).check(f"req:{principal.account_id}")
    return principal


__all__ = [
    "MAX_SUBJECT_CHARS",
    "Principal",
    "RateLimitExceeded",
    "SlidingWindowLimiter",
    "enforce_request_rate_limit",
    "get_model_limiter",
    "get_principal",
    "get_request_limiter",
    "reset_limiters",
]
