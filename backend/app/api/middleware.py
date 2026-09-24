"""Request correlation, request metrics and the error contract (Phase 5.10).

Three cross-cutting concerns that every route needs and none should
implement: an id to correlate by, a count of what happened, and one shape
for failures.

```
request
  -> mint correlation id (server-side, opaque)
  -> bind to ContextVar, so every log record downstream carries it
  -> call the route
  -> count status class, observe latency
  -> attach X-Request-ID to the response, success or failure
```

## Why a middleware rather than a dependency

A dependency runs inside the route, so it cannot label a response the route
never produced - and an unhandled exception is exactly the case an operator
most needs correlated. Before this, an unhandled error returned a bare 500
with no id and no metric: the least diagnosable path was the one that
mattered most.

## The error contract

Every handled failure returns the same envelope:

```json
{"error": {"code": "unlinked_account", "message": "...", "request_id": "..."}}
```

The `code` comes from a closed taxonomy, the `message` is a fixed sentence
for that code, and **no exception text ever reaches a client**. An operator
gets the exception class and the id in the log and can find the traceback;
a caller gets enough to act on and nothing to probe with.
"""

from __future__ import annotations

import logging
import time

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.metrics import (
    AUTHENTICATION_FAILURES,
    AUTHORIZATION_FAILURES,
    RATE_LIMITED_TOTAL,
    REQUEST_DURATION,
    REQUESTS_2XX,
    REQUESTS_4XX,
    REQUESTS_5XX,
    REQUESTS_TOTAL,
    UNLINKED_ACCOUNT_TOTAL,
    get_metrics,
)
from app.core.observability import (
    ERROR_MESSAGES,
    REQUEST_ID_HEADER,
    ErrorCode,
    new_request_id,
    request_id_var,
)

logger = logging.getLogger(__name__)

#: Status code -> taxonomy code, for failures raised as plain HTTPException
#: by existing routes. Keeps Phase 5.4-5.6 status semantics exactly as they
#: were while giving each one a stable machine-readable code.
_STATUS_TO_CODE: dict[int, ErrorCode] = {
    401: ErrorCode.AUTHENTICATION_ERROR,
    403: ErrorCode.AUTHORIZATION_ERROR,
    404: ErrorCode.NOT_FOUND,
    409: ErrorCode.UNLINKED_ACCOUNT,
    422: ErrorCode.VALIDATION_ERROR,
    429: ErrorCode.RATE_LIMITED,
}

#: Which taxonomy codes also bump a dedicated counter.
_CODE_COUNTERS: dict[ErrorCode, str] = {
    ErrorCode.AUTHENTICATION_ERROR: AUTHENTICATION_FAILURES,
    ErrorCode.AUTHORIZATION_ERROR: AUTHORIZATION_FAILURES,
    ErrorCode.UNLINKED_ACCOUNT: UNLINKED_ACCOUNT_TOTAL,
    ErrorCode.RATE_LIMITED: RATE_LIMITED_TOTAL,
}


def error_payload(code: ErrorCode, request_id: str, message: str | None = None) -> dict:
    """The error shape: additive, not a replacement.

    `detail` is kept byte-for-byte as FastAPI produced it before Phase 5.10,
    because clients and tests already depend on it - the 409 "no academic
    record is linked" sentence is part of the Phase 5.4 contract. Adding a
    machine-readable taxonomy is not a reason to break a working contract,
    so `error` sits alongside it rather than replacing it.

    `message` may override the default sentence only with text the
    application chose. Never with exception text.
    """
    text = message or ERROR_MESSAGES[code]
    return {
        # Unchanged contract.
        "detail": text,
        # Phase 5.10 addition: machine-readable code plus the correlation id
        # a user can read off and hand to an operator.
        "error": {
            "code": code.value,
            "message": text,
            "request_id": request_id,
        },
    }


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Correlation id, request metrics, and the id header on every response."""

    async def dispatch(self, request: Request, call_next):
        # Server-generated, always. A client-supplied X-Request-ID is
        # deliberately NOT adopted: honouring it would let a caller forge a
        # shared id across users, or write attacker-chosen text into logs.
        # The inbound value is echoed back nowhere and trusted for nothing.
        request_id = new_request_id()
        token = request_id_var.set(request_id)
        metrics = get_metrics()
        started = time.perf_counter()

        try:
            response = await call_next(request)
        except Exception:
            # The path that previously had neither id nor metric. The
            # traceback goes to the log with the correlation id; the client
            # gets a code and nothing else.
            elapsed = (time.perf_counter() - started) * 1000
            metrics.increment(REQUESTS_TOTAL)
            metrics.increment(REQUESTS_5XX)
            metrics.observe(REQUEST_DURATION, elapsed)
            logger.exception(
                "request_failed",
                extra={"method": request.method, "latency_ms": round(elapsed, 1)},
            )
            response = JSONResponse(
                status_code=500,
                content=error_payload(ErrorCode.INTERNAL_ERROR, request_id),
            )
            response.headers[REQUEST_ID_HEADER] = request_id
            request_id_var.reset(token)
            return response

        elapsed = (time.perf_counter() - started) * 1000
        metrics.increment(REQUESTS_TOTAL)
        if response.status_code < 400:
            metrics.increment(REQUESTS_2XX)
        elif response.status_code < 500:
            metrics.increment(REQUESTS_4XX)
        else:
            metrics.increment(REQUESTS_5XX)
        metrics.observe(REQUEST_DURATION, elapsed)

        # On success AND on handled failures, so an operator can always be
        # handed an id by a user.
        response.headers[REQUEST_ID_HEADER] = request_id
        request_id_var.reset(token)
        return response


async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """Give every deliberate HTTPException a taxonomy code and an id.

    Status codes and detail sentences are unchanged - Phases 5.4 to 5.6
    chose them carefully (409 for unlinked rather than 404 or 403, and so
    on), and this only adds machine-readable structure around them.
    """
    request_id = request_id_var.get()
    code = _STATUS_TO_CODE.get(exc.status_code, ErrorCode.INTERNAL_ERROR)

    counter = _CODE_COUNTERS.get(code)
    if counter:
        get_metrics().increment(counter)

    # `exc.detail` here is a sentence the application chose, never an
    # exception message - the routes construct these explicitly.
    payload = error_payload(code, request_id, message=str(exc.detail))
    response = JSONResponse(status_code=exc.status_code, content=payload,
                            headers=dict(exc.headers or {}))
    response.headers[REQUEST_ID_HEADER] = request_id
    return response


async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """422s carry a code and an id, but NOT the validation detail.

    FastAPI's default body echoes the offending input back. For a request
    that carried a bad field that is harmless; for one that carried a
    credential in the wrong place it is a disclosure, and telling a caller
    exactly which field failed is also a probing aid.
    """
    request_id = request_id_var.get()
    get_metrics().increment(  # validation failures are client errors
        _CODE_COUNTERS.get(ErrorCode.VALIDATION_ERROR, REQUESTS_4XX)
    )
    logger.info(
        "request_validation_failed",
        extra={"method": request.method, "error_count": len(exc.errors())},
    )
    response = JSONResponse(
        status_code=422,
        content=error_payload(ErrorCode.VALIDATION_ERROR, request_id),
    )
    response.headers[REQUEST_ID_HEADER] = request_id
    return response


def install(app: FastAPI) -> None:
    """Wire the middleware and handlers onto the application."""
    app.add_middleware(RequestContextMiddleware)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)


__all__ = [
    "RequestContextMiddleware",
    "error_payload",
    "http_exception_handler",
    "install",
    "validation_exception_handler",
]
