"""Plan-quality diagnostics (Phase 7.2.1): legacy greedy vs each pace, on FABRICATED students.

    python scripts/plan_quality.py [program_key]

Builds fabricated students (no real person) inside ONE transaction against the
configured database - which must hold real Rutgers course and section data -
plans for each with several settings, prints diagnostics, and ROLLS BACK:
nothing is written.

Diagnostics per plan: semesters, credits per semester, courses per semester,
mean absolute deviation from the target, underfilled semesters (< 60% of the
target, final one counted separately), final-semester load, status, time.
No difficulty score is invented.
"""

from __future__ import annotations

import pathlib
import statistics
import sys
import time
import uuid
from decimal import Decimal

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import select  # noqa: E402

from app.db.session import get_sync_sessionmaker  # noqa: E402
from app.domain.planning import Pace, PlanningConstraints  # noqa: E402
from app.models import Course, Student, StudentCourse  # noqa: E402
from app.services.planning.service import generate_plan  # noqa: E402
from app.services.scenarios import resolve_version  # noqa: E402

PROGRAM = sys.argv[1] if len(sys.argv) > 1 else "sas-198-ba"
START = "20271"

C = lambda code, term, grade="A", status="completed": (code, term, grade, status)  # noqa: E731
PERSONAS = {
    "first-year (current courses only)": [C("01:198:111", "20269", None, "in_progress"),
                                          C("01:640:151", "20269", None, "in_progress"),
                                          C("01:355:101", "20269", None, "in_progress")],
    "second-year": [C("01:198:111", "20259"), C("01:640:151", "20259", "B"), C("01:355:101", "20259"),
                    C("01:198:112", "20261", "B+"), C("01:640:152", "20261", "B"), C("01:198:205", "20261", "B"),
                    C("01:198:211", "20269", None, "in_progress"), C("01:198:206", "20269", None, "in_progress")],
    "third-year": [C("01:198:111", "20249"), C("01:640:151", "20249"), C("01:355:101", "20249"),
                   C("01:198:112", "20251"), C("01:640:152", "20251", "B"), C("01:198:205", "20251"),
                   C("01:198:211", "20259", "B"), C("01:198:206", "20259", "B"), C("01:640:250", "20259", "B"),
                   C("01:198:344", "20261", "B"), C("01:198:314", "20261"),
                   C("01:198:336", "20269", None, "in_progress"), C("01:198:213", "20269", None, "in_progress")],
    "nothing yet": [],
}
MODES = {
    "6.5 greedy (max 15)": PlanningConstraints(max_credits_per_term=Decimal(15)),
    "balanced 15/18": PlanningConstraints(target_credits_per_term=Decimal(15), max_credits_per_term=Decimal(18),
                                          max_courses_per_term=6, pace=Pace.BALANCED),
    "balanced 16/16": PlanningConstraints(target_credits_per_term=Decimal(16), max_credits_per_term=Decimal(16),
                                          max_courses_per_term=5, pace=Pace.BALANCED),
    "lighter 12/12": PlanningConstraints(target_credits_per_term=Decimal(12), max_credits_per_term=Decimal(12),
                                         max_courses_per_term=4, max_terms=12, pace=Pace.LIGHTER),
    "finish earlier 15/18": PlanningConstraints(target_credits_per_term=Decimal(15), max_credits_per_term=Decimal(18),
                                                max_courses_per_term=6, pace=Pace.FINISH_EARLIER),
}


def main() -> None:
    with get_sync_sessionmaker()() as session:
        version, _ = resolve_version(session, PROGRAM, "2026-2027")
        rows = []
        for persona, history in PERSONAS.items():
            student = Student(external_ref=f"plan-quality-{uuid.uuid4().hex[:8]}", catalog_year="2026-2027",
                              program_version_id=version.id)
            session.add(student)
            session.flush()
            for code, term, grade, status in history:
                course = session.scalar(select(Course).where(Course.course_string == code,
                                                             Course.supplement_code == ""))
                session.add(StudentCourse(student_id=student.id, course_id=course.id, term_code=term,
                                          status=status, grade=grade, credit_origin="rutgers"))
            session.flush()
            for mode, cons in MODES.items():
                t0 = time.perf_counter()
                plan = generate_plan(session, student, start_term=START, constraints=cons)
                ms = (time.perf_counter() - t0) * 1000
                loads = [t.credits for t in plan.terms]
                target = cons.target_credits_per_term or cons.max_credits_per_term
                under = [x for x in loads[:-1] if x * 10 < target * 6]
                rows.append((persona, mode, plan.status.value, len(loads),
                             "/".join(f"{x:g}" for x in loads), "/".join(str(len(t.courses)) for t in plan.terms),
                             round(statistics.fmean(abs(x - target) for x in loads), 2) if loads else "-",
                             len(under), f"{loads[-1]:g}" if loads else "-", round(ms),
                             ",".join(o.code for o in plan.observations)))
        session.rollback()
    head = ("persona", "mode", "status", "terms", "credits", "courses", "|dev|", "under", "last", "ms", "observations")
    print(" | ".join(head))
    for r in rows:
        print(" | ".join(str(x) for x in r))


if __name__ == "__main__":
    main()
