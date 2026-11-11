"""Course options, plan changes and undo for MY saved plan (Prompt 11 Part A).

```
GET  /student/plans/current/courses/{course}/{term}/options   A1 options
POST /student/plans/current/changes/preview                    A2 the planner re-run with a change
POST /student/plans/current/changes/apply   {preview_id}       applied only if the plan is unchanged
POST /student/plans/current/undo                               the previous version back
GET  /student/plans/current/choices                            A3b "Your choices for <next term>"
POST /student/plans/current/search                             A3c requirement-aware course search
PUT  /student/courses/{course}/{term}/doing-well              A3  my "doing well" mark (in progress)
PUT  /student/plans/personalization                            A3  "Use my grades to personalize"
```

Every route is scoped to the signed-in student's own plan (`_resolve`: the
principal -> account -> owned student; there is no student id anywhere in a
path or body). These are the typed tools a Part B assistant will call - it
gets the same previews and the same confirmation, never a direct write.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field

from app.api.security import Principal, enforce_request_rate_limit
from app.api.v1.routes.plans import SavedPlanView, _saved_view
from app.api.v1.routes.student import _resolve, _unlinked
from app.db.session import get_sync_sessionmaker
from app.domain.plan_edits import ApplyRequest, PlanChange, PlanPreview
from app.domain.text import CourseTitle, OptionalCourseTitle

logger = logging.getLogger(__name__)

router = APIRouter(tags=["plan changes"])

COURSE = r"^\d{2}:\d{3}:\d{3}$"
#: In a PATH the course may be written with dashes (01-198-111): the web proxy
#: allows only [A-Za-z0-9._~-] in a path segment. Normalised to colons.
PATH_COURSE = r"^\d{2}[:-]\d{3}[:-]\d{3}$"


def _course(value: str) -> str:
    return value.replace("-", ":")
TERM = r"^\d{4}[0179]$"


class CourseKnowledgeView(BaseModel):
    """Prompt 08 B1: what the course knowledge index says about a course this term."""
    status: str                     # mapped | excluded | free_elective_only | unknown
    after_stage: str | None = None  # e.g. "rbs-nb-admission"
    pending_review: bool = False
    conflicts: list[str] = []
    reasons: list[str] = []
    stale: bool = False


class OptionView(BaseModel):
    course: str
    title: CourseTitle
    credits: Decimal
    requirement_codes: list[str]
    requirements: list[str]
    offering: str
    offering_term: str
    workload_weight: Decimal | None
    contact_hours: Decimal | None
    delivery: list[str]
    fills_multiple: bool
    impact: str
    similar_to: str | None = None
    group: str
    #: Prompt 08 B1: the course knowledge index's verdict (status, stage, review labels).
    knowledge: CourseKnowledgeView | None = None


class OptionGroupView(BaseModel):
    kind: str
    requirement_code: str
    requirement_name: str
    options: list[OptionView]


class CourseOptionsView(BaseModel):
    course: str
    term_code: str
    title: CourseTitle
    state: str
    role: str | None
    locked: bool
    locked_reason: str | None
    requirements: list[str]
    groups: list[OptionGroupView]
    move_terms: list[str]
    can_remove: bool
    not_available: list[dict]


class ChoiceQuestionView(BaseModel):
    kind: str
    term_code: str
    prompt: str
    course: str | None = None
    title: OptionalCourseTitle = None
    groups: list[OptionGroupView] = Field(default_factory=list)
    requirement_code: str | None = None
    alternatives: list[dict] = Field(default_factory=list)
    recommended: list[str] = Field(default_factory=list)
    reason: str | None = None


class ChoicesView(BaseModel):
    term_code: str | None
    questions: list[ChoiceQuestionView]


class SearchFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    online: bool | None = None
    async_only: bool | None = None
    in_person: bool | None = None
    campus: str | None = Field(default=None, min_length=1, max_length=40)
    earliest: str | None = Field(default=None, pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    latest: str | None = Field(default=None, pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    avoid_days: list[Literal["M", "T", "W", "H", "F", "S", "U"]] | None = None
    level: int | None = Field(default=None, ge=1, le=4)
    credits: Decimal | None = Field(default=None, gt=0, le=30)
    open_only: bool | None = None
    fills_two: bool | None = None


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: What the student typed ("a different AHo course in Spring 2027, online").
    text: str = Field(default="", max_length=300)
    term_code: str | None = Field(default=None, pattern=TERM)
    #: Resolved requirement groups (ANDed; codes within a group ORed). An
    #: assistant passes these instead of guessing from words.
    requirements: list[list[str]] | None = Field(default=None, max_length=4)
    topic: str | None = Field(default=None, max_length=120)
    filters: SearchFilters | None = None
    limit: int = Field(default=10, ge=1, le=25)


class SearchHitView(BaseModel):
    course: str
    title: CourseTitle
    credits: Decimal
    requirement_codes: list[str]
    requirements: list[str]
    offering: str
    delivery: list[str]
    workload_weight: Decimal | None
    contact_hours: Decimal | None
    open_status: str
    why: str
    similar_to: str | None = None
    planned_term: str | None = None
    fills_multiple: bool = False
    knowledge: CourseKnowledgeView | None = None


class InterpretationView(BaseModel):
    term_code: str
    requirements: list[list[str]]
    requirement_names: list[str]
    topic: str | None
    online: bool
    async_only: bool
    in_person: bool
    campus: str | None = None
    earliest: str | None = None
    latest: str | None = None
    avoid_days: list[str]
    level: int | None
    credits: Decimal | None
    open_only: bool
    fills_two: bool
    exclude: list[str]
    did_you_mean: list[dict]
    notes: list[str]


class SearchView(BaseModel):
    interpretation: InterpretationView
    results: list[SearchHitView]
    not_available: list[dict]


class BuilderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    term_code: str = Field(pattern=TERM)
    selection: list[Annotated[str, Field(pattern=COURSE)]] | None = Field(default=None, max_length=24)
    text: str = Field(default="", max_length=300)
    requirements: list[list[str]] | None = Field(default=None, max_length=4)
    filters: SearchFilters | None = None
    delivery: Literal["any", "hybrid"] = "any"
    still_needed: bool = True
    degree_credit_only: bool = False
    sort: Literal["recommended", "name", "credits", "fills"] = "recommended"
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=30, ge=1, le=100)


@router.post("/student/plans/current/builder", summary="Browse every SOC course for a semester, with gates visible")
async def builder(body: BuilderRequest, principal: Principal = Depends(enforce_request_rate_limit)):
    from app.services.planning.semester_builder import browse

    def work():
        with get_sync_sessionmaker()() as session:
            student = _resolve(session, principal.account_id)
            if student is None:
                return None
            ctx = _context(session, student)
            if ctx.term(body.term_code) is None:
                raise HTTPException(status_code=422, detail="Choose a semester in your saved plan.")
            result = browse(ctx, body)
            session.rollback()
            return result
    return await _run(work, "semester_builder")


class DoingWell(BaseModel):
    model_config = ConfigDict(extra="forbid")
    doing_well: bool


class Personalization(BaseModel):
    model_config = ConfigDict(extra="forbid")
    use_grades_for_suggestions: bool


def _context(session, student):
    from app.services.planning.edits import NoSavedPlan, _row
    from app.services.planning.options import OptionsContext
    from app.services.planning.saved import _decode

    try:
        row = _row(session, student)
    except NoSavedPlan:
        raise HTTPException(status_code=404, detail="Build your plan first.") from None
    from app.domain.planning import PlanningConstraints

    return OptionsContext(session, student, _decode(row.result_blob),
                          PlanningConstraints.model_validate(row.constraints))


def _group(g) -> OptionGroupView:
    return OptionGroupView(kind=g.kind, requirement_code=g.requirement_code,
                           requirement_name=g.requirement_name,
                           options=[OptionView(**vars(o)) for o in g.options])


async def _run(work, what: str):
    try:
        out = await run_in_threadpool(work)
    except HTTPException:
        raise
    except Exception:
        logger.exception(f"{what}_failed")
        raise HTTPException(status_code=500, detail="Something went wrong. Try again.") from None
    if out is None:
        raise _unlinked()
    return out


@router.get("/student/plans/current/courses/{course}/{term}/options",
            response_model=CourseOptionsView,
            summary="Valid options for one planned course (same requirement / another one)")
async def options(course: str = Path(pattern=PATH_COURSE), term: str = Path(pattern=TERM),
                  principal: Principal = Depends(enforce_request_rate_limit)
                  ) -> CourseOptionsView:
    from app.services.planning.change_intent import CourseNotInPlan
    from app.services.planning.options import course_options

    started = time.perf_counter()

    def work():
        with get_sync_sessionmaker()() as session:
            student = _resolve(session, principal.account_id)
            if student is None:
                return None
            ctx = _context(session, student)
            try:
                o = course_options(ctx, _course(course), term)
            except CourseNotInPlan as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from None
            session.rollback()
            return CourseOptionsView(**{**vars(o), "groups": [_group(g) for g in o.groups]})
    out = await _run(work, "plan_options")
    logger.info("plan_options", extra={"latency_ms": round((time.perf_counter() - started)
                                                           * 1000, 1)})
    return out


@router.post("/student/plans/current/changes/preview", response_model=PlanPreview,
             summary="What a change would do to my plan (nothing is saved)")
async def preview(change: PlanChange, principal: Principal = Depends(enforce_request_rate_limit)
                  ) -> PlanPreview:
    from app.services.planning.edits import NoSavedPlan, PlanOutOfDate, preview_change

    started = time.perf_counter()

    def work():
        with get_sync_sessionmaker()() as session:
            student = _resolve(session, principal.account_id)
            if student is None:
                return None
            try:
                out = preview_change(session, student, change)
            except NoSavedPlan as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from None
            except PlanOutOfDate as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from None
            session.commit()
            return out
    out = await _run(work, "plan_preview")
    logger.info("plan_preview", extra={"kind": change.kind, "valid": out.valid,
                                       "latency_ms": round((time.perf_counter() - started)
                                                           * 1000, 1)})
    return out


@router.post("/student/plans/current/changes/apply", response_model=SavedPlanView,
             summary="Make a preview my plan (refused if my plan changed since)")
async def apply(body: ApplyRequest, principal: Principal = Depends(enforce_request_rate_limit)
                ) -> SavedPlanView:
    from app.services.onboarding import lock_student
    from app.services.planning.edits import NoSavedPlan, StalePreview, apply_preview

    def work():
        with get_sync_sessionmaker()() as session:
            student = lock_student(session, principal.account_id)
            if student is None:
                return None
            try:
                saved = apply_preview(session, student, body.preview_id,
                                      now=datetime.now(UTC))
            except NoSavedPlan as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from None
            except StalePreview as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from None
            session.commit()
            return _saved_view(saved)
    return await _run(work, "plan_apply")


@router.post("/student/plans/current/undo", response_model=SavedPlanView,
             summary="Undo my last plan change")
async def undo_change(principal: Principal = Depends(enforce_request_rate_limit)
                      ) -> SavedPlanView:
    from app.services.onboarding import lock_student
    from app.services.planning.edits import NoSavedPlan, NothingToUndo, undo

    def work():
        with get_sync_sessionmaker()() as session:
            student = lock_student(session, principal.account_id)
            if student is None:
                return None
            try:
                saved = undo(session, student)
            except (NoSavedPlan, NothingToUndo) as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from None
            session.commit()
            return _saved_view(saved)
    return await _run(work, "plan_undo")


@router.get("/student/plans/current/choices", response_model=ChoicesView,
            summary="Your choices for the plan's next semester")
async def choices(principal: Principal = Depends(enforce_request_rate_limit)) -> ChoicesView:
    from app.services.planning.options import next_term_choices

    def work():
        with get_sync_sessionmaker()() as session:
            student = _resolve(session, principal.account_id)
            if student is None:
                return None
            ctx = _context(session, student)
            term, questions = next_term_choices(ctx)
            session.rollback()
            return ChoicesView(term_code=term, questions=[
                ChoiceQuestionView(**{**vars(q), "groups": [_group(g) for g in q.groups]})
                for q in questions])
    return await _run(work, "plan_choices")


@router.post("/student/plans/current/search", response_model=SearchView,
             summary="Find courses by requirement, topic and filters (valid ones only)")
async def search(body: SearchRequest, principal: Principal = Depends(enforce_request_rate_limit)
                 ) -> SearchView:
    from app.services.planning.course_search import search_courses

    def work():
        with get_sync_sessionmaker()() as session:
            student = _resolve(session, principal.account_id)
            if student is None:
                return None
            ctx = _context(session, student)
            r = search_courses(ctx, body.text, term_code=body.term_code,
                               requirements=body.requirements, topic=body.topic,
                               filters=body.filters.model_dump(exclude_none=True) if body.filters else None,
                               limit=body.limit)
            session.rollback()
            return SearchView(interpretation=InterpretationView(**vars(r.interpretation)),
                              results=[SearchHitView(**vars(h)) for h in r.results],
                              not_available=r.not_available)
    return await _run(work, "plan_search")


@router.put("/student/courses/{course}/{term}/doing-well", status_code=204,
            summary="Mark an in-progress course of mine as going well (or not)")
async def doing_well(body: DoingWell, course: str = Path(pattern=PATH_COURSE),
                     term: str = Path(pattern=TERM),
                     principal: Principal = Depends(enforce_request_rate_limit)) -> None:
    from sqlalchemy import select

    from app.models import Course, StudentCourse

    def work():
        with get_sync_sessionmaker()() as session:
            student = _resolve(session, principal.account_id)
            if student is None:
                return None
            row = session.scalar(
                select(StudentCourse).join(Course, Course.id == StudentCourse.course_id)
                .where(StudentCourse.student_id == student.id, Course.course_string == _course(course),
                       StudentCourse.term_code == term))
            if row is None or row.status != "in_progress":
                raise HTTPException(status_code=404,
                                    detail="That is not one of your in-progress courses.")
            row.doing_well = body.doing_well or None
            session.commit()
            return True
    await _run(work, "doing_well")


@router.put("/student/plans/personalization", response_model=Personalization,
            summary='"Use my grades to personalize suggestions" (default on)')
async def personalization(body: Personalization,
                          principal: Principal = Depends(enforce_request_rate_limit)
                          ) -> Personalization:
    from app.services.planning.profile import get_profile

    def work():
        with get_sync_sessionmaker()() as session:
            student = _resolve(session, principal.account_id)
            if student is None:
                return None
            profile = get_profile(session, student)
            if profile is None:
                raise HTTPException(status_code=409,
                                    detail="Confirm your planning preferences first.")
            profile.use_grades_for_suggestions = body.use_grades_for_suggestions
            session.commit()
            return Personalization(use_grades_for_suggestions=profile.use_grades_for_suggestions)
    return await _run(work, "personalization")


@router.get("/student/plans/personalization", response_model=Personalization,
            summary='Whether my grades personalize my suggestions')
async def get_personalization(principal: Principal = Depends(enforce_request_rate_limit)
                              ) -> Personalization:
    from app.services.planning.options import uses_grades

    def work():
        with get_sync_sessionmaker()() as session:
            student = _resolve(session, principal.account_id)
            if student is None:
                return None
            return Personalization(use_grades_for_suggestions=uses_grades(session, student))
    return await _run(work, "personalization")
