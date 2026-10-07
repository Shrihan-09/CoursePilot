"""Deterministic section schedules for the authenticated student (Phase 6.6).

```
POST /api/v1/student/schedules/generate
```

Body, and nothing else:

```json
{ "term": "20269",
  "courses": ["01:198:344", "01:640:300"],
  "preferences": { "earliest_start": "10:00", "latest_end": null, "avoid_days": ["F"],
                   "minimum_minutes_between_classes": 0, "preferred_campuses": ["BUS"] },
  "max_results": 10 }
```

`courses` is exactly what to schedule - one term of a Phase 6.5 plan, or the
student's own selection; the HTTP layer does not depend on planner objects.
Identity comes from the principal only; unknown fields (`student_id`,
`account_id`, ...) are a 422. Nothing is written, nothing is registered,
nothing reaches WebReg, and no LLM is called.
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from app.api.security import Principal, enforce_request_rate_limit
from app.api.v1.routes.student import _resolve, _unlinked
from app.db.session import get_sync_sessionmaker
from app.domain.schedule import (
    DEFAULT_MAX_RESULTS,
    MAX_REQUESTED_COURSES,
    MAX_RESULTS_LIMIT,
    SchedulePreferences,
    ScheduleResult,
)
from app.services.scheduling.service import InvalidScheduleRequest, generate_schedule

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/student/schedules", tags=["schedules"])


class ScheduleRequest(BaseModel):
    term: str = Field(pattern=r"^\d{4}[0179]$")
    courses: list[str] = Field(min_length=1, max_length=2 * MAX_REQUESTED_COURSES)
    preferences: SchedulePreferences = Field(default_factory=SchedulePreferences)
    max_results: int = Field(default=DEFAULT_MAX_RESULTS, ge=1, le=MAX_RESULTS_LIMIT)

    model_config = {"extra": "forbid"}


def _generate(account_id, body: ScheduleRequest) -> ScheduleResult | None:
    with get_sync_sessionmaker()() as session:
        student = _resolve(session, account_id)
        if student is None:
            return None
        return generate_schedule(session, student, term_code=body.term, courses=body.courses,
                                 preferences=body.preferences, max_results=body.max_results)


@router.post(
    "/generate",
    response_model=ScheduleResult,
    summary="Deterministic section combinations for exact courses in one term (no registration)",
)
async def generate(
    body: ScheduleRequest,
    principal: Principal = Depends(enforce_request_rate_limit),
) -> ScheduleResult:
    started = time.perf_counter()
    try:
        outcome = await run_in_threadpool(_generate, principal.account_id, body)
    except InvalidScheduleRequest as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except Exception:
        logger.exception("schedule_generate_failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to generate schedules.",
        ) from None
    if outcome is None:
        raise _unlinked()
    # Term, counts and timing only - no course, section or grade in a log line.
    logger.info("schedule_generated", extra={
        "principal": principal.redacted(),
        "term": body.term,
        "schedule_status": outcome.status.value,
        "options": len(outcome.options),
        "latency_ms": round((time.perf_counter() - started) * 1000, 1),
    })
    return outcome
