"""Planning readiness: may CoursePilot build this student a plan? (Phase 7.2.1)

Server-owned, so the web app, a future mobile app and a future assistant all
get the SAME answer - no client decides it.

    needs_program            no academic profile (program + catalog year) yet
    needs_academic_history   onboarding not finished, or a transcript import is open
    needs_planning_profile   the student has not confirmed planning preferences
    ready_with_warnings      ready; some things are worth knowing first
    ready

"Academic history" means CONFIRMED: onboarding finished with no import left
open. An empty history is a legitimate confirmed state (a student starting
out), so it is not a blocker - it is reported.

Warnings never block and never claim failure: an UNKNOWN condition is listed
as "needs confirmation", a pending-review program as pending review.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import StudentCourse
from app.services.planning.calendar import current_term, default_start
from app.services.planning.profile import get_profile, profile_input
from app.services.planning.service import latest_history_term
from app.services.planning.terms import label


@dataclass
class Check:
    code: str
    state: str            # done | missing | warning | info
    message: str


@dataclass
class Readiness:
    state: str
    checks: list[Check] = field(default_factory=list)


def readiness(session: Session, account_id, now: datetime) -> Readiness:
    from app.services import onboarding as onboarding_service

    status = onboarding_service.onboarding_status(session, account_id, now)
    student = status.student
    if student is None:
        return Readiness("needs_program", [Check(
            "program", "missing", "Choose your program and catalog year.")])
    checks = [Check("program", "done", "Program and catalog year chosen.")]

    history_confirmed = student.onboarding_completed_at is not None and not status.open_imports
    if history_confirmed:
        checks.append(Check("academic_history", "done", "Academic history confirmed."))
    else:
        checks.append(Check("academic_history", "missing",
                            "Finish reviewing and confirming your academic history."))

    profile = get_profile(session, student)
    if profile is not None:
        checks.append(Check("planning_profile", "done", "Planning preferences confirmed."))
    else:
        checks.append(Check("planning_profile", "missing",
                            "Tell CoursePilot how you'd like your semesters planned."))

    # Context worth knowing - never a blocker.
    from app.models import ProgramVersion

    program = onboarding_service.describe_selection(
        session, session.get(ProgramVersion, student.program_version_id))
    if program.support_status != "supported":
        checks.append(Check("program_review", "warning",
                            "Your program's requirements are pending human review."))
    counts = dict(session.execute(
        select(StudentCourse.status, func.count()).where(StudentCourse.student_id == student.id)
        .group_by(StudentCourse.status)).all())
    if counts.get("in_progress"):
        checks.append(Check("in_progress", "info",
                            "Courses in progress count only once you pass them; later courses "
                            "that depend on them are planned conditionally."))
    if counts.get("planned"):
        checks.append(Check("planned_entries", "info",
                            "Courses you marked as planned are not used by the planner; it "
                            "recommends its own sequence."))
    latest = latest_history_term(session, student)
    now_term = current_term()
    if history_confirmed and (latest is None or latest < now_term) and counts:
        checks.append(Check("current_term_missing", "warning",
                            f"Your record has no courses for {label(now_term)}. If you're "
                            "taking courses now, add them as in progress so the plan starts "
                            "from the right place."))
    if profile is not None:
        goals = profile_input(profile).additional_goals
        if goals:
            checks.append(Check("additional_goals", "info",
                                "Additional programs you're exploring are not included in the "
                                "plan: CoursePilot doesn't combine programs yet."))
    start = default_start(latest)
    checks.append(Check("start_term", "info", f"Your plan would start in {label(start)}."))

    if not history_confirmed:
        state = "needs_academic_history"
    elif profile is None:
        state = "needs_planning_profile"
    elif any(c.state == "warning" for c in checks):
        state = "ready_with_warnings"
    else:
        state = "ready"
    return Readiness(state, checks)


READY_STATES = ("ready", "ready_with_warnings")

__all__ = ["Check", "READY_STATES", "Readiness", "readiness"]
