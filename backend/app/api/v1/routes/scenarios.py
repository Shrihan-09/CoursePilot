"""Hypothetical program scenarios for the authenticated student (Phase 6.0).

```
POST /api/v1/student/scenarios/audit     my record under another program's rules
POST /api/v1/student/scenarios/compare   ...and how that differs from my actual audit
```

Body, and nothing else:

```json
{ "program_key": "sas-640-ba", "catalog_year": "2026-2027" }
```

`catalog_year` is optional; omitted, it is the student's own catalog year and
never "the latest" (see `app.services.scenarios`).

## The client selects rules, never a person

The client chooses WHICH PROGRAM. It cannot choose WHOSE RECORD: the student
is resolved exactly as `/student/audit` resolves it,

```
principal.account_id -> UserAccount -> Student.user_id == account.id
```

and the request model forbids extra fields, so `student_id`, `student_ref`,
`external_ref`, `account_id` or a provider subject in the body is a 422 - not
silently ignored, which would leave a client believing it had selected
someone.

## POST, although nothing is written

The evaluation is read-only. POST is used for the same reason the explanation
endpoint uses it: the request is a structured body whose unknown fields must
be REJECTED, and query strings cannot express "forbid anything else".
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from app.api.security import Principal, enforce_request_rate_limit
from app.api.v1.routes.programs import PROGRAM_KEY_MAX_LENGTH, PROGRAM_KEY_PATTERN
from app.api.v1.routes.student import _resolve, _unlinked
from app.db.session import get_sync_sessionmaker
from app.domain.scenario import ProgramComparison, ScenarioAssumption, ScenarioAudit, ScenarioTarget
from app.services.scenarios import (
    CatalogYearUnavailable,
    ProgramChoiceRequired,
    ProgramNotEvaluable,
    ProgramNotFound,
    compare_with_current,
    run_scenario_audit,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/student/scenarios", tags=["scenarios"])


class ScenarioRequest(BaseModel):
    program_key: str = Field(pattern=PROGRAM_KEY_PATTERN, max_length=PROGRAM_KEY_MAX_LENGTH)
    catalog_year: str | None = Field(default=None, pattern=r"^\d{4}-\d{4}$")

    model_config = {"extra": "forbid"}


class ScenarioComparisonResponse(BaseModel):
    target: ScenarioTarget
    assumptions: list[ScenarioAssumption]
    comparison: ProgramComparison


def _translate(exc: Exception) -> HTTPException:
    if isinstance(exc, ProgramNotFound | CatalogYearUnavailable):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, ProgramNotEvaluable):
        return HTTPException(status_code=422, detail=str(exc))
    if isinstance(exc, ProgramChoiceRequired):
        return HTTPException(status_code=status.HTTP_409_CONFLICT,
                             detail=str(exc))
    raise exc


def _audit(account_id, body: ScenarioRequest) -> ScenarioAudit | None:
    with get_sync_sessionmaker()() as session:
        student = _resolve(session, account_id)
        if student is None:
            return None
        return run_scenario_audit(session, student, body.program_key, body.catalog_year)


def _compare(account_id, body: ScenarioRequest):
    with get_sync_sessionmaker()() as session:
        student = _resolve(session, account_id)
        if student is None:
            return None
        return compare_with_current(session, student, body.program_key, body.catalog_year)


async def _run(fn, principal: Principal, body: ScenarioRequest, event: str):
    started = time.perf_counter()
    try:
        outcome = await run_in_threadpool(fn, principal.account_id, body)
    except (ProgramNotFound, CatalogYearUnavailable, ProgramNotEvaluable,
            ProgramChoiceRequired) as exc:
        raise _translate(exc) from None
    except Exception:
        # Same rule as /student/audit: an engine failure is never dressed up
        # as a result, because an empty audit reads as "nothing satisfied".
        logger.exception(f"{event}_failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to evaluate this scenario.",
        ) from None
    if outcome is None:
        raise _unlinked()
    # Program key and timing only: the target is public catalog data, and no
    # course, grade or requirement outcome belongs in a log line.
    logger.info(event, extra={
        "principal": principal.redacted(),
        "program_key": body.program_key,
        "latency_ms": round((time.perf_counter() - started) * 1000, 1),
    })
    return outcome


@router.post(
    "/audit",
    response_model=ScenarioAudit,
    summary="The authenticated student's record evaluated under another program",
)
async def scenario_audit(
    body: ScenarioRequest,
    principal: Principal = Depends(enforce_request_rate_limit),
) -> ScenarioAudit:
    return await _run(_audit, principal, body, "scenario_audit_served")


@router.post(
    "/compare",
    response_model=ScenarioComparisonResponse,
    summary="Deterministic comparison of the actual audit with a program scenario",
)
async def scenario_compare(
    body: ScenarioRequest,
    principal: Principal = Depends(enforce_request_rate_limit),
) -> ScenarioComparisonResponse:
    scenario, _actual, comparison = await _run(
        _compare, principal, body, "scenario_compare_served"
    )
    return ScenarioComparisonResponse(
        target=scenario.target,
        assumptions=scenario.assumptions,
        comparison=comparison,
    )
