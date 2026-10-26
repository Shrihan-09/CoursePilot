"""Planning Profile, planning readiness and My current schedule (Phase 7.2.1).

    GET  /student/planning-profile               confirmed profile (or null) + defaults
    PUT  /student/planning-profile               CONFIRM a profile (the only writer)
    GET  /student/planning-readiness             may CoursePilot build a plan yet?
    GET  /student/current-schedule               in-progress courses + CONFIRMED sections
    GET  /student/current-schedule/sections      the Rutgers sections of one in-progress course
    PUT  /student/current-schedule/sections      confirm MY section (exact index, never inferred)
    DELETE /student/current-schedule/sections    forget a course's confirmation

Identity as everywhere: the record is the authenticated account's own; no
request names a student, and unknown body fields are a 422.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from app.api.security import Principal, enforce_request_rate_limit
from app.api.v1.routes.student import _resolve, _unlinked
from app.db.session import get_sync_sessionmaker
from app.domain.planning import FillerPreference, Pace, Strategy, WorkloadLevel
from app.domain.planning_profile import (
    DEFAULT_MAX_TERMS,
    GRADUATION_TARGET_TERMS,
    PlanningProfileInput,
)
from app.domain.schedule import SectionChoice
from app.services.planning.engine import GRADUATION_SEMESTERS
from app.services.planning.profile import confirm_profile, get_profile, profile_input
from app.services.planning.readiness import readiness
from app.services.planning.terms import label as term_label
from app.services.planning.workload import LEVELS
from app.services.scheduling.current import (
    CurrentScheduleError,
    NotInProgress,
    confirm_section,
    current_schedule,
    remove_confirmations,
    sections_for,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/student", tags=["personalization"])

COURSE = r"^\d{2}:\d{3}:\d{3}$"

#: Phase 7.3: Pace is the GRADUATION TARGET ("When do you want to graduate?")
#: and sets only the plan's horizon; the workload level sets each term's load.
#: Stored values are unchanged (balanced / finish_earlier / lighter).
PACES = [
    {"value": pace.value, "label": label,
     "description": (f"Graduate within {GRADUATION_SEMESTERS[pace]} Fall/Spring semesters, counted "
                     "from your first semester at Rutgers. If the remaining requirements don't "
                     "fit, the plan says so - it never silently pushes graduation later.")}
    for pace, label in ((Pace.BALANCED, "On time"), (Pace.FINISH_EARLIER, "Early"),
                        (Pace.LIGHTER, "No rush"))]

#: Phase 7.3: per-term intensity. Numbers are CoursePilot settings, shown as is.
WORKLOAD_LEVELS = [
    {"value": level.value, "label": label,
     "credit_floor": str(LEVELS[level][0]), "credit_max": str(LEVELS[level][1]),
     "budget": str(LEVELS[level][2]),
     "description": (f"{LEVELS[level][0]}-{LEVELS[level][1]} credits a semester, workload up "
                     f"to {LEVELS[level][2]}.")}
    for level, label in ((WorkloadLevel.LIGHT, "Light"), (WorkloadLevel.BALANCED, "Balanced"),
                         (WorkloadLevel.SEMI_RIGOROUS, "Semi-rigorous"),
                         (WorkloadLevel.CHALLENGING, "Challenging"))]
FILLER_PREFERENCES = [
    {"value": FillerPreference.FLEXIBLE.value, "label": "Prefer flexible",
     "description": "Asynchronous or online sections first, when a course has them."},
    {"value": FillerPreference.IN_PERSON.value, "label": "Prefer in person",
     "description": "In-person sections first."},
    {"value": FillerPreference.NO_PREFERENCE.value, "label": "No preference",
     "description": "Delivery mode doesn't matter."},
]
WORKLOAD_WEIGHT_TEXT = (
    "Workload weight = credits + 0.25 x weekly contact hours above the credits + 1 for a lab "
    "+ 0.5 x (course level - 1) + 1 if technical (your major's subject, math, or a Core "
    "natural science). Computed from the Rutgers Schedule of Classes; not a difficulty rating.")

#: Academic strategy (7.2.4): an ordering preference among courses CoursePilot
#: has already checked are valid. Never a shortcut around a requirement.
STRATEGIES = [
    {"value": Strategy.BALANCED.value, "label": "Balanced",
     "description": "Mix your major, school/Core and other requirements."},
    {"value": Strategy.MAJOR_FIRST.value, "label": "Major first",
     "description": "When several courses fit, take your major's requirements earlier."},
    {"value": Strategy.REQUIREMENTS_FIRST.value, "label": "School/Core first",
     "description": "When several courses fit, finish school and Core requirements earlier."},
    {"value": Strategy.UNLOCK_FUTURE_OPTIONS.value, "label": "Keep options open",
     "description": "When several courses fit, prefer ones that many later courses build on."},
]


class ProfileView(PlanningProfileInput):
    version: int
    confirmed_at: datetime
    source: str
    effective_max_terms: int


class ProfileResponse(BaseModel):
    profile: ProfileView | None
    defaults: PlanningProfileInput
    paces: list[dict]
    strategies: list[dict] = STRATEGIES
    workload_levels: list[dict] = WORKLOAD_LEVELS
    filler_preferences: list[dict] = FILLER_PREFERENCES
    workload_weight: str = WORKLOAD_WEIGHT_TEXT
    #: Planning settings are CoursePilot's, never Rutgers credit-load policy.
    disclaimer: str = ("These are your CoursePilot planning settings, not Rutgers credit-load "
                       "policy. Check your school's rules before planning an overload.")


class CheckView(BaseModel):
    code: str
    state: str
    message: str


class ReadinessView(BaseModel):
    state: str
    checks: list[CheckView]


class CurrentCourseView(BaseModel):
    course: str
    title: str | None
    term_code: str
    transcript_section: str | None
    confirmed: list[SectionChoice]
    unavailable: list[dict]
    sections_available: int
    confirmed_identities: list[dict] = Field(default_factory=list)
    #: Phase 7.4: not on the newest confirmed WebReg import of this term ("dropped?").
    not_on_latest_webreg: bool = False


class CurrentScheduleView(BaseModel):
    term_code: str | None
    term_label: str | None
    other_in_progress_terms: list[str]
    term_published: bool
    courses: list[CurrentCourseView]


class SectionsView(BaseModel):
    course: str
    term_code: str
    term_published: bool
    transcript_section: str | None
    sections: list[SectionChoice]


class ConfirmSection(BaseModel):
    course: str = Field(pattern=COURSE)
    index_number: str = Field(pattern=r"^\d{5}$")

    model_config = {"extra": "forbid"}


def _session():
    return get_sync_sessionmaker()()


def _now() -> datetime:
    return datetime.now(UTC)


def _view(row) -> ProfileView:
    data = profile_input(row)
    return ProfileView(**data.model_dump(), version=row.version, confirmed_at=row.confirmed_at,
                       source=row.source,
                       effective_max_terms=data.max_terms or (
                           GRADUATION_TARGET_TERMS if data.workload_level
                           else DEFAULT_MAX_TERMS)[data.pace])


async def _run(work, event: str):
    try:
        out = await run_in_threadpool(work)
    except HTTPException:
        raise
    except Exception:
        logger.exception(event)
        raise HTTPException(status_code=500, detail="Something went wrong on CoursePilot's side.") \
            from None
    return out


# --------------------------------------------------------------- profile


@router.get("/planning-profile", response_model=ProfileResponse,
            summary="My confirmed planning preferences (null until confirmed) and the defaults")
async def get_planning_profile(principal: Principal = Depends(enforce_request_rate_limit)
                               ) -> ProfileResponse:
    def work():
        with _session() as session:
            student = _resolve(session, principal.account_id)
            if student is None:
                return None
            row = get_profile(session, student)
            return ProfileResponse(profile=_view(row) if row else None,
                                   defaults=PlanningProfileInput(
                                       workload_level=WorkloadLevel.BALANCED), paces=PACES)
    out = await _run(work, "planning_profile_get_failed")
    if out is None:
        raise _unlinked()
    return out


@router.put("/planning-profile", response_model=ProfileView,
            summary="Confirm my planning preferences (the planner reads only these)")
async def put_planning_profile(body: PlanningProfileInput,
                               principal: Principal = Depends(enforce_request_rate_limit)
                               ) -> ProfileView:
    from app.services.onboarding import lock_student

    def work():
        with _session() as session:
            student = lock_student(session, principal.account_id)
            if student is None:
                return None
            row = confirm_profile(session, student, body, _now())
            session.commit()
            return _view(row)
    out = await _run(work, "planning_profile_put_failed")
    if out is None:
        raise _unlinked()
    return out


@router.get("/planning-readiness", response_model=ReadinessView,
            summary="Whether CoursePilot has what it needs to build my plan")
async def get_readiness(principal: Principal = Depends(enforce_request_rate_limit)
                        ) -> ReadinessView:
    def work():
        with _session() as session:
            r = readiness(session, principal.account_id, _now())
            return ReadinessView(state=r.state,
                                 checks=[CheckView(**vars(c)) for c in r.checks])
    return await _run(work, "planning_readiness_failed")


# ------------------------------------------------------- current schedule


@router.get("/current-schedule", response_model=CurrentScheduleView,
            summary="My in-progress courses and the sections I have confirmed")
async def get_current_schedule(principal: Principal = Depends(enforce_request_rate_limit)
                               ) -> CurrentScheduleView:
    def work():
        with _session() as session:
            student = _resolve(session, principal.account_id)
            if student is None:
                return None
            cs = current_schedule(session, student)
            return CurrentScheduleView(
                term_code=cs.term_code, term_label=term_label(cs.term_code) if cs.term_code else None,
                other_in_progress_terms=cs.other_in_progress_terms,
                term_published=cs.term_published,
                courses=[CurrentCourseView(**vars(c)) for c in cs.courses])
    out = await _run(work, "current_schedule_failed")
    if out is None:
        raise _unlinked()
    return out


@router.get("/current-schedule/sections", response_model=SectionsView,
            summary="The Rutgers sections of one of my in-progress courses, to choose mine")
async def get_sections(course: str = Query(pattern=COURSE),
                       principal: Principal = Depends(enforce_request_rate_limit)) -> SectionsView:
    def work():
        with _session() as session:
            student = _resolve(session, principal.account_id)
            if student is None:
                return None
            try:
                term, published, sections, hint = sections_for(session, student, course)
            except NotInProgress as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from None
            return SectionsView(course=course, term_code=term, term_published=published,
                                transcript_section=hint, sections=sections)
    out = await _run(work, "current_sections_failed")
    if out is None:
        raise _unlinked()
    return out


@router.put("/current-schedule/sections", response_model=CurrentScheduleView,
            summary="Confirm the exact section I am taking for an in-progress course")
async def put_section(body: ConfirmSection,
                      principal: Principal = Depends(enforce_request_rate_limit)
                      ) -> CurrentScheduleView:
    from app.services.onboarding import lock_student

    def work():
        with _session() as session:
            student = lock_student(session, principal.account_id)
            if student is None:
                return None
            try:
                confirm_section(session, student, body.course, body.index_number, _now())
            except NotInProgress as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from None
            except CurrentScheduleError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from None
            session.commit()
            cs = current_schedule(session, student)
            return CurrentScheduleView(
                term_code=cs.term_code, term_label=term_label(cs.term_code) if cs.term_code else None,
                other_in_progress_terms=cs.other_in_progress_terms,
                term_published=cs.term_published,
                courses=[CurrentCourseView(**vars(c)) for c in cs.courses])
    out = await _run(work, "current_section_confirm_failed")
    if out is None:
        raise _unlinked()
    return out


@router.delete("/current-schedule/sections", status_code=204,
               summary="Forget the section(s) I confirmed for a course")
async def delete_section(course: str = Query(pattern=COURSE),
                         principal: Principal = Depends(enforce_request_rate_limit)) -> Response:
    from app.services.onboarding import lock_student

    def work():
        with _session() as session:
            student = lock_student(session, principal.account_id)
            if student is None:
                return None
            try:
                remove_confirmations(session, student, course)
            except NotInProgress as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from None
            session.commit()
            return True
    if await _run(work, "current_section_delete_failed") is None:
        raise _unlinked()
    return Response(status_code=204)

