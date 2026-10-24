"""The student's saved plan, its freshness, and the planning context (Phase 7.2).

Nothing here plans. `save_plan` calls the Phase 6.5 engine unchanged
(`generate_plan`) and stores its exact output; `current_plan` reads it back
and recomputes the input fingerprints to say whether it is still current.

## Freshness

A saved plan is STALE when any input it was computed from has changed:

```
program_changed          the student switched program or catalog year
academic_record_changed  completed / in-progress / planned rows changed
program_rules_changed    the program version's requirements were recurated
rutgers_data_changed     the SOC datasets the offering evidence rests on
engine_updated           the planning or audit engine's semantics changed
start_term_passed        the record now reaches the plan's first term
```

Each check compares a fingerprint the engine itself produced with one
recomputed now - the same functions, so equality means "same inputs". A stale
plan is returned with its reasons; it is never relabelled current, and the
client cannot mark it current (no endpoint accepts a status).

## Courses the planner could not place

The engine reports courses it would not schedule (UNKNOWN eligibility, no
offering evidence) only as codes inside issue `details`. `blocked_courses`
attaches each one's catalog title and the ORIGINAL Rutgers prerequisite text,
so the student sees the condition rather than a code. It reads; it never
re-evaluates eligibility, and it never turns an UNKNOWN into anything else.
"""

from __future__ import annotations

import re
import zlib
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.planning import PLANNING_ENGINE_VERSION, PlanningConstraints, PlanResult
from app.models import (
    Course,
    CoursePrerequisite,
    CourseSection,
    DataSource,
    Student,
    StudentCourse,
)
from app.models.plans import StudentPlan
from app.services.audit.cache import AUDIT_ENGINE_VERSION, academic_fingerprint, rules_fingerprint
from app.services.planning.calendar import current_term, default_start, start_options
from app.services.planning.offerings import dataset_identity
from app.services.planning.profile import get_profile, profile_input
from app.services.planning.service import InvalidStartTerm, generate_plan, latest_history_term
from app.services.planning.terms import label, next_term

SOC_KIND = "rutgers_official_api"

STALE_REASONS = ("program_changed", "academic_record_changed", "program_rules_changed",
                 "rutgers_data_changed", "engine_updated", "start_term_passed",
                 "planning_preferences_changed")

#: Issue codes whose `details` name courses the planner did not place.
_BLOCKING_DETAIL_CODES = ("ELIGIBILITY_UNKNOWN", "NO_PLACEABLE_OFFERING")
_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")


# --------------------------------------------------------------------------
# planning context
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class LoadedTerm:
    term_code: str
    label: str
    coverage: str | None        # complete | partial | unknown | None (nobody said)
    retrieved_at: datetime | None


@dataclass(frozen=True)
class PlanningContext:
    #: The latest term of COMPLETED or IN-PROGRESS coursework ("record through").
    latest_recorded_term: str | None
    #: The earliest such term ("history starts").
    earliest_recorded_term: str | None
    #: The Rutgers term in session today (approximate boundaries; calendar.py).
    current_term: str
    #: The default PLAN STARTING SEMESTER: the first future Fall/Spring term
    #: that also follows the record (Phase 7.2.1 - never in the past).
    earliest_start_term: str
    #: Every start the API accepts, in the seasons the student plans in.
    start_options: list[str]
    loaded_terms: list[LoadedTerm]


def loaded_terms(session: Session) -> list[LoadedTerm]:
    """Terms with Rutgers section data in this database, oldest first.

    A term counts only when sections exist for it - the same rule the
    Schedule Engine uses (`candidates.load_term`), so this list never offers
    a term the engine would answer TERM_SCHEDULE_NOT_PUBLISHED for.
    """
    published = session.scalars(select(CourseSection.term_code).distinct()).all()
    sources: dict[str, list] = {}
    for row in session.execute(
            select(DataSource.term_code, DataSource.coverage, DataSource.retrieved_at)
            .where(DataSource.kind == SOC_KIND, DataSource.term_code.in_(published))
            .order_by(DataSource.retrieved_at)):
        sources.setdefault(row.term_code, []).append(row)
    out = []
    for code in sorted(published):
        rows = sources.get(code, [])
        latest = rows[-1] if rows else None
        coverage = ("complete" if any(r.coverage == "complete" for r in rows)
                    else (latest.coverage if latest else None))
        out.append(LoadedTerm(term_code=code, label=label(code), coverage=coverage,
                              retrieved_at=latest.retrieved_at if latest else None))
    return out


def minimum_start(session: Session, student: Student) -> str:
    """The earliest start the API accepts: after the current term AND after the record."""
    floor = next_term(current_term())
    latest = latest_history_term(session, student)
    if latest is not None and next_term(latest) > floor:
        floor = next_term(latest)
    return floor


def check_start(session: Session, student: Student, start_term: str) -> None:
    """A saved plan never starts in the past or inside the recorded history."""
    floor = minimum_start(session, student)
    if start_term < floor:
        raise InvalidStartTerm(
            f"A plan starts in a future term: {label(floor)} ({floor}) or later. Terms that "
            "have passed, or that your record already covers, can't be planned.")


def planning_context(session: Session, student: Student) -> PlanningContext:
    latest = latest_history_term(session, student)
    earliest = session.scalar(select(StudentCourse.term_code).where(
        StudentCourse.student_id == student.id,
        StudentCourse.status.in_(("completed", "in_progress")))
        .order_by(StudentCourse.term_code).limit(1))
    profile = get_profile(session, student)
    prefs = profile_input(profile) if profile is not None else None
    options = start_options(minimum_start(session, student), 6,
                            include_summer=bool(prefs and prefs.include_summer),
                            include_winter=bool(prefs and prefs.include_winter))
    default = default_start(latest)
    if default not in options:
        options = sorted({*options, default})
    return PlanningContext(latest_recorded_term=latest, earliest_recorded_term=earliest,
                           current_term=current_term(), earliest_start_term=default,
                           start_options=options, loaded_terms=loaded_terms(session))


# --------------------------------------------------------------------------
# saved plan
# --------------------------------------------------------------------------


@dataclass
class BlockedCourse:
    course: str
    title: str | None
    issue_code: str              # ELIGIBILITY_UNKNOWN | NO_PLACEABLE_OFFERING
    requirement_code: str | None
    reasons: list[str] = field(default_factory=list)
    prerequisite_text: str | None = None
    prerequisite_term: str | None = None


@dataclass
class SavedPlan:
    id: str
    created_at: datetime
    updated_at: datetime
    start_term: str
    constraints: PlanningConstraints
    result: PlanResult
    stale_reasons: list[str]
    blocked_courses: list[BlockedCourse]


def _encode(result: PlanResult) -> bytes:
    return zlib.compress(result.canonical_json().encode(), 6)


def _decode(blob: bytes) -> PlanResult:
    return PlanResult.model_validate_json(zlib.decompress(blob))


def save_plan(session: Session, student: Student, *, start_term: str | None,
              constraints: PlanningConstraints) -> SavedPlan:
    """Generate the student's plan for THEIR OWN program and replace the saved one.

    Phase 7.2.1: `constraints` come from the student's CONFIRMED Planning
    Profile (the route passes `to_constraints(profile)`), and the start term
    defaults to - and may never precede - the server's planning floor.

    `generate_plan` runs read-only (it raises if the engine would write); the
    single write is the upsert below, committed by the caller's transaction.
    """
    if start_term is None:
        start_term = default_start(latest_history_term(session, student))
    check_start(session, student, start_term)
    result = generate_plan(session, student, start_term=start_term, constraints=constraints)
    meta = result.metadata
    row = session.scalar(select(StudentPlan).where(StudentPlan.student_id == student.id))
    if row is None:
        row = StudentPlan(student_id=student.id)
        session.add(row)
    row.program_version_id = student.program_version_id
    row.catalog_year = student.catalog_year
    row.start_term = start_term
    row.constraints = constraints.model_dump(mode="json")
    row.planning_engine_version = meta.planning_engine_version
    row.audit_engine_version = meta.audit_engine_version
    row.academic_fingerprint = meta.academic_fingerprint
    row.rules_fingerprint = meta.rules_fingerprint
    row.offering_dataset = list(meta.offering_dataset)
    row.plan_status = result.status.value
    row.result_blob = _encode(result)
    session.flush()
    session.refresh(row)
    return _view(session, student, row, result=result)


def current_plan(session: Session, student: Student) -> SavedPlan | None:
    row = session.scalar(select(StudentPlan).where(StudentPlan.student_id == student.id))
    if row is None:
        return None
    return _view(session, student, row)


def delete_plan(session: Session, student: Student) -> bool:
    row = session.scalar(select(StudentPlan).where(StudentPlan.student_id == student.id))
    if row is None:
        return False
    session.delete(row)
    session.flush()
    return True


def offering_assumption_reasons(session: Session, row: StudentPlan) -> list[str]:
    """Phase 7.2.4: courses placed on HISTORICAL evidence, re-checked against today's
    term authority. A term Rutgers has since published can contradict the
    assumption; the plan is then flagged - a course is never silently swapped."""
    from app.domain.planning import OfferingEvidenceKind as K
    from app.services.planning.offerings import OfferingIndex

    plan = _decode(row.result_blob)
    assumed = [(pc.course, term.term_code) for term in plan.terms for pc in term.courses
               if pc.offering.kind is K.HISTORICAL_SAME_SEASON]
    if not assumed:
        return []
    index = OfferingIndex(session, {course for course, _ in assumed})
    now = {index.evidence(course, term).kind for course, term in assumed}
    reasons = []
    if K.NOT_OFFERED_IN_TERM in now:
        reasons.append("offering_assumption_contradicted")
    if K.TERM_DATA_PENDING in now:
        reasons.append("target_term_published")
    return reasons


def stale_reasons(session: Session, student: Student, row: StudentPlan) -> list[str]:
    """Which inputs differ from the ones the saved plan was computed from."""
    reasons = []
    program_changed = (row.program_version_id != student.program_version_id
                       or row.catalog_year != student.catalog_year)
    if program_changed:
        reasons.append("program_changed")
    # The academic fingerprint also hashes program version and catalog year,
    # so after a program change it necessarily differs; report the cause, not
    # its echo.
    elif academic_fingerprint(session, student) != row.academic_fingerprint:
        reasons.append("academic_record_changed")
    if rules_fingerprint(session, row.program_version_id) != row.rules_fingerprint:
        reasons.append("program_rules_changed")
    if dataset_identity(session) != list(row.offering_dataset):
        reasons.append("rutgers_data_changed")
    reasons += offering_assumption_reasons(session, row)
    if (row.planning_engine_version != PLANNING_ENGINE_VERSION
            or row.audit_engine_version != AUDIT_ENGINE_VERSION):
        reasons.append("engine_updated")
    latest = latest_history_term(session, student)
    if (latest is not None and latest >= row.start_term) or row.start_term <= current_term():
        reasons.append("start_term_passed")
    profile = get_profile(session, student)
    if profile is not None:
        from app.domain.planning_profile import to_constraints

        if to_constraints(profile_input(profile)).model_dump(mode="json") !=                 PlanningConstraints.model_validate(row.constraints).model_dump(mode="json"):
            reasons.append("planning_preferences_changed")
    return reasons


def _view(session: Session, student: Student, row: StudentPlan, *,
          result: PlanResult | None = None) -> SavedPlan:
    result = result or _decode(row.result_blob)
    return SavedPlan(
        id=str(row.id), created_at=row.created_at, updated_at=row.updated_at,
        start_term=row.start_term,
        constraints=PlanningConstraints.model_validate(row.constraints),
        result=result,
        stale_reasons=stale_reasons(session, student, row),
        blocked_courses=blocked_courses(session, result))


def _plain(text: str | None) -> str | None:
    """SOC prerequisite strings carry <em> markup; the meaning is in the words."""
    if not text:
        return None
    return _SPACE.sub(" ", _TAG.sub("", text)).strip() or None


def blocked_courses(session: Session, result: PlanResult) -> list[BlockedCourse]:
    """Courses named in blocking issue details, with catalog title and Rutgers text."""
    found: dict[tuple[str, str, str | None], BlockedCourse] = {}
    for issue in result.issues:
        if issue.code not in _BLOCKING_DETAIL_CODES:
            continue
        if issue.code == "ELIGIBILITY_UNKNOWN":
            named = {k: list(v) for k, v in (issue.details.get("courses") or {}).items()}
        else:
            named = {}
            for why, keys in (issue.details.get("by_reason") or {}).items():
                for key in keys:
                    named.setdefault(key, []).append(why)
        for key, why in named.items():
            found.setdefault((issue.code, key, issue.requirement_code), BlockedCourse(
                course=key, title=None, issue_code=issue.code,
                requirement_code=issue.requirement_code, reasons=sorted(set(why))))
    if not found:
        return []
    keys = sorted({k for _, k, _ in found})
    titles = dict(session.execute(
        select(Course.course_string, Course.title)
        .where(Course.course_string.in_(keys), Course.supplement_code == "")).all())
    # The most recent published prerequisite text per course.
    texts: dict[str, tuple[str, str]] = {}
    for course_string, term, raw in session.execute(
            select(Course.course_string, CoursePrerequisite.term_code,
                   CoursePrerequisite.raw_text)
            .join(Course, Course.id == CoursePrerequisite.course_id)
            .where(Course.course_string.in_(keys), Course.supplement_code == "",
                   CoursePrerequisite.raw_text.is_not(None))
            .order_by(Course.course_string, CoursePrerequisite.term_code)):
        texts[course_string] = (term, raw)
    out = []
    for (code, key, req), item in sorted(found.items(), key=lambda kv: (kv[0][0], kv[0][1],
                                                                        kv[0][2] or "")):
        item.title = titles.get(key)
        if key in texts:
            item.prerequisite_term, raw = texts[key]
            item.prerequisite_text = _plain(raw)
        out.append(item)
    return out


__all__ = ["BlockedCourse", "LoadedTerm", "PlanningContext", "STALE_REASONS", "SavedPlan",
           "blocked_courses", "check_start", "current_plan", "delete_plan", "loaded_terms",
           "minimum_start", "planning_context", "save_plan", "stale_reasons"]
