"""Generate schedule options on demand (Phase 6.6). Read-only; nothing is stored.

Course eligibility is ALWAYS rechecked here, whatever the caller says about
where the course list came from: the request is exactly "these courses in
this term", which is what `check_proposal` (Phase 6.4) answers - the
candidates are one same-term proposal, so co-requisites among them count.
The scheduler never re-implements a prerequisite; it only reads the verdict:

    UNSATISFIED  -> COURSE_ELIGIBILITY_FAILED, no options (a schedule for a
                    course the student may not take would mislead)
    UNKNOWN      -> options are returned with a needs-confirmation issue
"""

from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.domain.prerequisites import PrereqStatus
from app.domain.schedule import (
    DEFAULT_MAX_RESULTS,
    MAX_REQUESTED_COURSES,
    MAX_RESULTS_LIMIT,
    CourseRelationship,
    ScheduleIssue,
    ScheduleMetadata,
    SchedulePreferences,
    ScheduleResult,
    ScheduleStatus,
    SearchStats,
    Severity,
)
from app.models import Student
from app.services.course_eligibility import check_proposal
from app.services.scheduling.candidates import load_term, student_attributes
from app.services.scheduling.engine import NODE_LIMIT, SOLUTION_LIMIT, schedule

TERM_RE = re.compile(r"^\d{4}[0179]$")
COURSE_RE = re.compile(r"^\d{2}:\d{3}:\d{3}$")


class InvalidScheduleRequest(ValueError):
    """The request cannot be evaluated. Message is client-safe."""


class ScheduleWouldWrite(RuntimeError):
    """Generation left pending changes in the session. Never expected."""


def normalize_courses(courses: list[str]) -> tuple[list[str], list[str]]:
    """(canonical requested courses, duplicates removed)."""
    cleaned = [c.strip() for c in courses]
    bad = sorted({c for c in cleaned if not COURSE_RE.match(c)})
    if bad:
        raise InvalidScheduleRequest(f"Not Rutgers course codes (UU:SSS:CCC): {bad}")
    unique = sorted(set(cleaned))
    duplicates = sorted({c for c in cleaned if cleaned.count(c) > 1})
    if not unique:
        raise InvalidScheduleRequest("At least one course is required.")
    if len(unique) > MAX_REQUESTED_COURSES:
        raise InvalidScheduleRequest(
            f"At most {MAX_REQUESTED_COURSES} courses can be scheduled together.")
    return unique, duplicates


def generate_schedule(session: Session, student: Student, *, term_code: str,
                      courses: list[str], preferences: SchedulePreferences | None = None,
                      max_results: int = DEFAULT_MAX_RESULTS) -> ScheduleResult:
    if not TERM_RE.match(term_code or ""):
        raise InvalidScheduleRequest(f"{term_code!r} is not a Rutgers term code (YYYYT).")
    if not 1 <= max_results <= MAX_RESULTS_LIMIT:
        raise InvalidScheduleRequest(f"max_results must be between 1 and {MAX_RESULTS_LIMIT}.")
    prefs = preferences or SchedulePreferences()
    requested, duplicates = normalize_courses(courses)

    issues: list[ScheduleIssue] = []
    if duplicates:
        issues.append(ScheduleIssue(
            severity=Severity.WARNING, code="DUPLICATE_COURSE_REQUEST",
            message="Courses requested more than once are scheduled once.", courses=duplicates))

    term = load_term(session, term_code, requested, student_attributes(session, student))

    offered = [c for c in requested if c in term.slots_of]
    failed = False
    relationships: list[CourseRelationship] = []
    if offered:
        checks = check_proposal(session, student, offered, term_code)
        for key in offered:
            check = checks[key]
            co = check.corequisite
            for entry in (co.evidence.concurrent_checks if co.evidence else []):
                if entry.get("met_by") == "proposed_same_term":
                    # Phase 6.4's verdict, recorded for the client: an
                    # ACADEMIC co-requisite met by another requested course.
                    relationships.append(CourseRelationship(
                        kind="academic_corequisite", course=key, related=entry["course"],
                        evidence=co.raw_text, source=co.source_field))
            if check.status is PrereqStatus.UNSATISFIED:
                failed = True
                issues.append(ScheduleIssue(
                    severity=Severity.BLOCKER, code="COURSE_NOT_ELIGIBLE",
                    message=(f"{key}: the published prerequisite/co-requisite is not met "
                             "(Phase 6.4 course eligibility)."),
                    courses=[key], details={"combination": check.combination,
                                            "prerequisite": check.prerequisite.raw_text,
                                            "prerequisite_status": check.prerequisite.status.value,
                                            "corequisite": co.raw_text,
                                            "corequisite_status": co.status.value,
                                            "missing_corequisites": (
                                                co.evidence.missing_courses
                                                if co.evidence else []),
                                            "reasons": check.prerequisite.reasons}))
            elif check.status is PrereqStatus.UNKNOWN:
                issues.append(ScheduleIssue(
                    severity=Severity.NEEDS_CONFIRMATION, code="COURSE_ELIGIBILITY_UNKNOWN",
                    message=f"{key}: CoursePilot cannot confirm the student may take this course.",
                    courses=[key], details={"reasons": sorted(
                        set(check.prerequisite.reasons) | set(check.corequisite.reasons))}))

    if failed:
        result = ScheduleResult(
            term_code=term_code, status=ScheduleStatus.COURSE_ELIGIBILITY_FAILED,
            requested_courses=requested, preferences=prefs,
            issues=sorted(issues, key=lambda i: (i.severity.value, i.code, i.courses)),
            metadata=ScheduleMetadata(term_code=term_code, section_dataset=term.dataset,
                                      search=SearchStats(node_limit=NODE_LIMIT,
                                                         solution_limit=SOLUTION_LIMIT)))
    else:
        result = schedule(term, requested, prefs, max_results, issues, relationships)

    if session.new or session.dirty or session.deleted:
        session.rollback()
        raise ScheduleWouldWrite("schedule generation attempted to modify state")
    return result


__all__ = ["InvalidScheduleRequest", "ScheduleWouldWrite", "generate_schedule",
           "normalize_courses"]
