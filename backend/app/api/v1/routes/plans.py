"""Deterministic semester plans for the authenticated student (Phase 6.5).

```
POST /api/v1/student/plans/generate
```

Body, and nothing else:

```json
{ "start_term": "20271",
  "program_key": "sas-640-ba-option-a",       (optional - default: the student's own program)
  "catalog_year": "2026-2027",               (optional - default: the student's own)
  "constraints": { "max_credits_per_term": 15, "max_courses_per_term": 5,
                   "max_terms": 8, "include_summer": false, "include_winter": false } }
```

Same identity rule as `/student/scenarios`: the record is resolved from the
authenticated principal only, and unknown fields - `student_id`,
`account_id`, anything else - are a 422, not silently ignored.

POST although nothing is written: the plan is generated on demand and never
stored (see app.services.planning.service). There is no LLM anywhere on this
path.
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from app.api.security import Principal, enforce_request_rate_limit
from app.api.v1.routes.programs import PROGRAM_KEY_MAX_LENGTH, PROGRAM_KEY_PATTERN
from app.api.v1.routes.scenarios import _translate
from app.api.v1.routes.student import _resolve, _unlinked
from app.db.session import get_sync_sessionmaker
from app.domain.planning import PlanningConstraints, PlanResult
from app.services.planning.service import InvalidStartTerm, generate_plan
from app.services.scenarios import (
    CatalogYearUnavailable,
    ProgramChoiceRequired,
    ProgramNotEvaluable,
    ProgramNotFound,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/student/plans", tags=["plans"])


class PlanRequest(BaseModel):
    start_term: str = Field(pattern=r"^\d{4}[0179]$")
    program_key: str | None = Field(default=None, pattern=PROGRAM_KEY_PATTERN,
                                    max_length=PROGRAM_KEY_MAX_LENGTH)
    catalog_year: str | None = Field(default=None, pattern=r"^\d{4}-\d{4}$")
    constraints: PlanningConstraints = Field(default_factory=PlanningConstraints)

    model_config = {"extra": "forbid"}


def _generate(account_id, body: PlanRequest) -> PlanResult | None:
    with get_sync_sessionmaker()() as session:
        student = _resolve(session, account_id)
        if student is None:
            return None
        return generate_plan(session, student, start_term=body.start_term,
                             program_key_=body.program_key, catalog_year=body.catalog_year,
                             constraints=body.constraints)


@router.post(
    "/generate",
    response_model=PlanResult,
    summary="A deterministic semester-by-semester course plan (no sections, no times)",
)
async def generate(
    body: PlanRequest,
    principal: Principal = Depends(enforce_request_rate_limit),
) -> PlanResult:
    started = time.perf_counter()
    try:
        outcome = await run_in_threadpool(_generate, principal.account_id, body)
    except InvalidStartTerm as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except (ProgramNotFound, CatalogYearUnavailable, ProgramNotEvaluable,
            ProgramChoiceRequired) as exc:
        raise _translate(exc) from None
    except Exception:
        logger.exception("plan_generate_failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to generate a plan.",
        ) from None
    if outcome is None:
        raise _unlinked()
    # Program key, status and timing only - no course or grade in a log line.
    logger.info("plan_generated", extra={
        "principal": principal.redacted(),
        "program_key": outcome.target.program_key,
        "plan_status": outcome.status.value,
        "latency_ms": round((time.perf_counter() - started) * 1000, 1),
    })
    return outcome
