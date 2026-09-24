"""Request correlation, redaction and the error taxonomy (Phase 5.10).

> **Observability must not become a second source of sensitive data.**

A log line and a metric are storage. They are shipped to aggregators, kept
longer than request data, and read by more people than the database is. So
the rule for this module is the same one the API has: an identifier that is
never emitted cannot leak, and one that must be emitted is hashed first.

## Correlation

```
request arrives
  -> middleware mints an opaque id      server-generated, never client-supplied
  -> stored in a ContextVar             survives the threadpool hop
  -> injected into every log record     by a logging filter, not by callers
  -> returned as X-Request-ID           so an operator can be handed one
```

The id is `uuid4` hex, truncated. It is **not** derived from the account,
the student, the subject or the token - deriving it from identity would make
every log line a disclosure and every correlation across requests a way to
link a person's activity.

A client may *send* `X-Request-ID`, and it is deliberately ignored for
correlation identity. Honouring it would let a caller forge shared ids
across users, or inject log content. Authentication is never affected by any
header other than `Authorization`.

## Redaction

`redact_id` turns a UUID into a short stable handle. Stable so two log lines
about the same student can be correlated by an operator; one-way so the
handle cannot be turned back into a row. This is the same construction
`Principal.redacted()` already used - Phase 5.10 simply makes it available
to the places that were still logging raw ids.

## Error taxonomy

A small closed set. Clients receive the *code* and a fixed human sentence;
they never receive an exception message, because exception text is where
internal detail escapes. Operators get the class and the correlation id in
the log, which is enough to find the traceback without shipping it.
"""

from __future__ import annotations

import hashlib
import logging
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from enum import StrEnum

#: The current request's correlation id. A ContextVar rather than a
#: parameter because it has to reach logging calls in code that has no idea
#: an HTTP request exists - the Degree Engine, the cache, the providers.
#:
#: `anyio.to_thread` copies the context into worker threads, so this
#: survives the `run_in_threadpool` hop that all the synchronous audit work
#: goes through. A test pins that.
request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

#: Header name, both directions. Inbound it is IGNORED for identity.
REQUEST_ID_HEADER = "X-Request-ID"


def new_request_id() -> str:
    """Opaque, server-generated, unrelated to any identity."""
    return uuid.uuid4().hex[:16]


def current_request_id() -> str:
    return request_id_var.get()


def redact_id(value: object) -> str:
    """A stable, one-way handle for an identifier that must be correlatable.

    Used instead of logging a `Student.id`, `UserAccount.id` or any other
    database key. Stable so an operator can follow one subject across log
    lines; not reversible, so the log does not become a join key into the
    academic tables.

    Salted with a fixed domain string so handles from this codebase cannot
    be compared against a rainbow table of bare UUIDs.
    """
    if value is None:
        return "-"
    return hashlib.sha256(f"coursepilot-observability|{value}".encode()).hexdigest()[:12]


class ErrorCode(StrEnum):
    """The closed set of failures a client may be told about.

    Deliberately small. Each value answers "what kind of thing went wrong?"
    at the granularity a caller can act on, and nothing finer - a finer
    taxonomy leaks internal structure and becomes a probing oracle.
    """

    AUTHENTICATION_ERROR = "authentication_error"
    AUTHORIZATION_ERROR = "authorization_error"
    UNLINKED_ACCOUNT = "unlinked_account"
    RATE_LIMITED = "rate_limited"
    NOT_FOUND = "not_found"
    VALIDATION_ERROR = "validation_error"
    #: The deterministic engine could not produce a result. Never dressed up
    #: as a successful audit, and never answered by a model.
    DEGREE_ENGINE_ERROR = "degree_engine_error"
    #: Derived state failed. Must never reach a client as an error, because
    #: the engine can always recompute - present in the taxonomy so the
    #: *metric* exists, not so a response can use it.
    CACHE_ERROR = "cache_error"
    #: The AI provider failed. Also never a client error: the deterministic
    #: explanation is the answer.
    PROVIDER_ERROR = "provider_error"
    MODEL_VALIDATION_ERROR = "model_validation_error"
    TIMEOUT = "timeout"
    INTERNAL_ERROR = "internal_error"


#: What a client is told, per code. Fixed sentences: no exception text, no
#: identifiers, no counts that could be probed.
ERROR_MESSAGES: dict[ErrorCode, str] = {
    ErrorCode.AUTHENTICATION_ERROR: "Authentication required.",
    ErrorCode.AUTHORIZATION_ERROR: "Not authorized.",
    ErrorCode.UNLINKED_ACCOUNT: (
        "No academic record is linked to this account. "
        "Linking requires verification and cannot be self-served."
    ),
    ErrorCode.RATE_LIMITED: "Too many requests.",
    ErrorCode.NOT_FOUND: "Not found.",
    ErrorCode.VALIDATION_ERROR: "The request could not be understood.",
    ErrorCode.DEGREE_ENGINE_ERROR: "Unable to compute a degree audit for this account.",
    ErrorCode.CACHE_ERROR: "A transient storage error occurred.",
    ErrorCode.PROVIDER_ERROR: "The explanation service is unavailable.",
    ErrorCode.MODEL_VALIDATION_ERROR: "The explanation could not be verified.",
    ErrorCode.TIMEOUT: "The request timed out.",
    ErrorCode.INTERNAL_ERROR: "An internal error occurred.",
}


class RequestIdFilter(logging.Filter):
    """Puts the correlation id on every record, without touching call sites.

    A filter rather than an adapter so that libraries, the Degree Engine and
    the cache all get correlated too - none of them should have to know that
    an HTTP layer exists in order to be diagnosable.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


@contextmanager
def stage(name: str):
    """Time one phase of a request into a histogram.

    The name is a declared metric constant supplied by the caller; there is
    no label mechanism here either, for the same reason as everywhere else.
    """
    from app.core.metrics import get_metrics

    started = time.perf_counter()
    try:
        yield
    finally:
        get_metrics().observe(name, (time.perf_counter() - started) * 1000)


__all__ = [
    "ERROR_MESSAGES",
    "REQUEST_ID_HEADER",
    "ErrorCode",
    "RequestIdFilter",
    "current_request_id",
    "new_request_id",
    "redact_id",
    "request_id_var",
    "stage",
]
