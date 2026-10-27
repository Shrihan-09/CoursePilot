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
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from app.api.security import Principal, enforce_request_rate_limit
from app.api.v1.routes.programs import PROGRAM_KEY_MAX_LENGTH, PROGRAM_KEY_PATTERN
from app.api.v1.routes.scenarios import _translate
from app.api.v1.routes.student import _resolve, _unlinked
from app.db.session import get_sync_sessionmaker
from app.domain.planning import CONSTRAINTS_SOURCE, PlanningConstraints, PlanResult
from app.domain.planning_profile import to_constraints
from app.services.planning.engine import InvalidAlternativeSelection
from app.services.planning.profile import get_profile, profile_input
from app.services.planning.readiness import READY_STATES, readiness
from app.services.planning.saved import (
    SavedPlan,
    current_plan,
    delete_plan,
    planning_context,
    save_plan,
)
from app.services.planning.service import InvalidStartTerm, generate_plan
from app.services.planning.terms import label as term_label
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
    except (InvalidStartTerm, InvalidAlternativeSelection) as exc:
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


# ==========================================================================
# Phase 7.2: planning context and the saved plan
#
#   GET    /student/plans/context   start term + Rutgers terms with section data
#   POST   /student/plans           generate MY plan (my program) and save it
#   GET    /student/plans/current   the saved plan + server-computed freshness
#   DELETE /student/plans/current   forget it
#
# The saved plan is the engine's exact PlanResult (app.services.planning.saved).
# Nothing here accepts a status, a course list or a "mark current": the only
# way to change a saved plan is to have the engine produce a new one.
# ==========================================================================


class LoadedTermView(BaseModel):
    term_code: str
    label: str
    coverage: str | None
    retrieved_at: datetime | None


class TermOption(BaseModel):
    term_code: str
    label: str


class PlanningContextView(BaseModel):
    #: "History starts" and "record through": completed or in-progress terms.
    earliest_recorded_term: str | None
    latest_recorded_term: str | None
    #: The Rutgers term in session today (approximate boundaries).
    current_term: str
    current_term_label: str
    #: The default PLAN STARTING SEMESTER, and every start the API accepts.
    earliest_start_term: str
    earliest_start_label: str
    start_options: list[TermOption]
    #: Terms with Rutgers section data loaded - the only terms the Schedule
    #: Engine can build for.
    loaded_terms: list[LoadedTermView]
    #: Server-side defaults, labelled as CoursePilot settings, not Rutgers policy.
    default_constraints: PlanningConstraints
    constraints_source: str = CONSTRAINTS_SOURCE


class Freshness(BaseModel):
    state: Literal["current", "stale"]
    #: Which inputs changed since the plan was generated (empty when current).
    reasons: list[str]


class BlockedCourseView(BaseModel):
    course: str
    title: str | None
    issue_code: str
    requirement_code: str | None
    reasons: list[str]
    #: The Rutgers prerequisite text as published (markup removed), and its term.
    prerequisite_text: str | None
    prerequisite_term: str | None


class SavedPlanView(BaseModel):
    id: str
    created_at: datetime
    updated_at: datetime
    start_term: str
    constraints: PlanningConstraints
    freshness: Freshness
    blocked_courses: list[BlockedCourseView]
    result: PlanResult


class CurrentPlanResponse(BaseModel):
    plan: SavedPlanView | None


class SavePlanRequest(BaseModel):
    """My own program only - a what-if plan is generated, never saved.

    Phase 7.2.1: the load settings come from my CONFIRMED Planning Profile, not
    from the request; the body may only choose the starting semester (default:
    the server's), which must be a future term."""

    start_term: str | None = Field(default=None, pattern=r"^\d{4}[0179]$")

    model_config = {"extra": "forbid"}


def _saved_view(saved: SavedPlan) -> SavedPlanView:
    return SavedPlanView(
        id=saved.id, created_at=saved.created_at, updated_at=saved.updated_at,
        start_term=saved.start_term, constraints=saved.constraints,
        freshness=Freshness(state="stale" if saved.stale_reasons else "current",
                            reasons=saved.stale_reasons),
        blocked_courses=[BlockedCourseView(**vars(b)) for b in saved.blocked_courses],
        result=saved.result)


@router.get("/context", response_model=PlanningContextView,
            summary="Where a plan can start, and which Rutgers terms have section data")
async def context(principal: Principal = Depends(enforce_request_rate_limit)
                  ) -> PlanningContextView:
    def work():
        with get_sync_sessionmaker()() as session:
            student = _resolve(session, principal.account_id)
            if student is None:
                return None
            ctx = planning_context(session, student)
            return PlanningContextView(
                earliest_recorded_term=ctx.earliest_recorded_term,
                latest_recorded_term=ctx.latest_recorded_term,
                current_term=ctx.current_term, current_term_label=term_label(ctx.current_term),
                earliest_start_term=ctx.earliest_start_term,
                earliest_start_label=term_label(ctx.earliest_start_term),
                start_options=[TermOption(term_code=t, label=term_label(t))
                               for t in ctx.start_options],
                loaded_terms=[LoadedTermView(**vars(t)) for t in ctx.loaded_terms],
                default_constraints=PlanningConstraints())
    try:
        out = await run_in_threadpool(work)
    except Exception:
        logger.exception("plan_context_failed")
        raise HTTPException(status_code=500, detail="Unable to load planning context.") from None
    if out is None:
        raise _unlinked()
    return out


@router.post("", response_model=SavedPlanView,
             summary="Generate my plan for my own program and save it (replaces the saved plan)")
async def save(body: SavePlanRequest,
               principal: Principal = Depends(enforce_request_rate_limit)) -> SavedPlanView:
    from app.services.onboarding import lock_student

    started = time.perf_counter()

    def work():
        with get_sync_sessionmaker()() as session:
            # The Phase 7.0 writer lock: the plan is computed from, and saved
            # against, one consistent record - an edit cannot interleave.
            student = lock_student(session, principal.account_id)
            if student is None:
                return None
            # Server-owned readiness: confirmed history and confirmed
            # preferences, or no saved plan.
            ready = readiness(session, principal.account_id, datetime.now(UTC))
            if ready.state not in READY_STATES:
                missing = next((c.message for c in ready.checks if c.state == "missing"),
                               "CoursePilot isn't ready to build your plan yet.")
                raise HTTPException(status_code=409, detail=missing)
            profile = get_profile(session, student)
            saved = save_plan(session, student, start_term=body.start_term,
                              constraints=to_constraints(profile_input(profile)))
            session.commit()
            return _saved_view(saved)
    try:
        out = await run_in_threadpool(work)
    except HTTPException:
        raise
    except (InvalidStartTerm, InvalidAlternativeSelection) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except (ProgramNotFound, CatalogYearUnavailable, ProgramNotEvaluable,
            ProgramChoiceRequired) as exc:
        raise _translate(exc) from None
    except Exception:
        logger.exception("plan_save_failed")
        raise HTTPException(status_code=500, detail="Unable to generate a plan.") from None
    if out is None:
        raise _unlinked()
    logger.info("plan_saved", extra={
        "principal": principal.redacted(),
        "plan_status": out.result.status.value,
        "latency_ms": round((time.perf_counter() - started) * 1000, 1),
    })
    return out


@router.get("/current", response_model=CurrentPlanResponse,
            summary="My saved plan, with whether its inputs have changed since")
async def current(principal: Principal = Depends(enforce_request_rate_limit)
                  ) -> CurrentPlanResponse:
    def work():
        with get_sync_sessionmaker()() as session:
            student = _resolve(session, principal.account_id)
            if student is None:
                return None
            saved = current_plan(session, student)
            return CurrentPlanResponse(plan=_saved_view(saved) if saved else None)
    try:
        out = await run_in_threadpool(work)
    except Exception:
        logger.exception("plan_current_failed")
        raise HTTPException(status_code=500, detail="Unable to load the saved plan.") from None
    if out is None:
        raise _unlinked()
    return out


@router.delete("/current", status_code=204, summary="Delete my saved plan")
async def delete(principal: Principal = Depends(enforce_request_rate_limit)) -> Response:
    from app.services.onboarding import lock_student

    def work():
        with get_sync_sessionmaker()() as session:
            student = lock_student(session, principal.account_id)
            if student is None:
                return None
            delete_plan(session, student)
            session.commit()
            return True
    if await run_in_threadpool(work) is None:
        raise _unlinked()
    return Response(status_code=204)
