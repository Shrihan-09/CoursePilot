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

import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field

from fastapi import Depends, Header, HTTPException, Request, status

from app.core.config import Environment, Settings, get_settings

logger = logging.getLogger(__name__)

#: Rutgers NetIDs are short; the column is String(64). Bounding the credential
#: keeps pathological input away from the database and the model.
MAX_SUBJECT_CHARS = 64

_DEV_SCHEME = "devtoken"


@dataclass(frozen=True, slots=True)
class Principal:
    """An authenticated caller.

    `student_ref` is the ONLY way a request reaches student data, and it
    comes from the credential. There is deliberately no constructor path
    that takes it from a request body.
    """

    subject: str
    student_ref: str
    #: How the identity was established, for logs and for refusing dev
    #: credentials in production.
    method: str = _DEV_SCHEME

    def redacted(self) -> str:
        """A stable, non-reversible handle for logs.

        A NetID is personal data. Logs need to correlate requests, not to
        identify people, so they get a short digest instead.
        """
        import hashlib

        return hashlib.sha256(self.subject.encode()).hexdigest()[:12]


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
    """Resolve the caller. The ONLY source of student identity.

    Development scheme: `Authorization: Bearer devtoken:<student_ref>`.

    It is deliberately trivial - and deliberately impossible to enable by
    accident. A real deployment replaces this function (or the dependency
    override) with SSO/JWT verification; every route and every test already
    depends on the *dependency*, so that swap touches one file.
    """
    if not authorization:
        raise _unauthenticated("Authentication required.")

    scheme, _, credential = authorization.partition(" ")
    if scheme.lower() != "bearer" or not credential.strip():
        raise _unauthenticated("Invalid authentication scheme.")

    credential = credential.strip()

    if not settings.dev_auth_enabled:
        # No other scheme is implemented yet. Failing closed is the only
        # honest behaviour: there is no production verifier to fall back to.
        raise _unauthenticated("Authentication is not configured on this server.")

    if settings.coursepilot_env is Environment.PRODUCTION:
        # Belt and braces: even if someone sets DEV_AUTH_ENABLED in prod.
        logger.error("dev_auth_refused_in_production")
        raise _unauthenticated("Authentication is not configured on this server.")

    prefix, _, student_ref = credential.partition(":")
    if prefix != _DEV_SCHEME or not student_ref:
        raise _unauthenticated("Invalid credential.")
    if len(student_ref) > MAX_SUBJECT_CHARS:
        raise _unauthenticated("Invalid credential.")

    return Principal(subject=student_ref, student_ref=student_ref, method=_DEV_SCHEME)


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
        get_request_limiter(settings).check(f"req:{principal.subject}")
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
