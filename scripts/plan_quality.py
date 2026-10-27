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

Phase 7.6 adds three requirement-awareness columns, computed from fields every
engine version emits (so the same script measures before and after):

* untraced  - planned courses that fill no requirement and unlock no planned
              course (nothing justifies them);
* extra_alt - courses placed in an ANY_OF alternative beyond the number of
              alternatives it needs (e.g. Chemistry 161 and Physics 193 both
              planned when one B.S. science sequence is required);
* multi     - courses credited to two or more requirements (allowed sharing).

Set COURSEPILOT_PQ_ROOT to measure another checkout (the "before" numbers).
"""

from __future__ import annotations

import json
import os  # noqa: E402
import pathlib
import statistics
import sys
import time
import uuid
from decimal import Decimal

ROOT = pathlib.Path(os.environ.get("COURSEPILOT_PQ_ROOT")
                    or pathlib.Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(ROOT / "backend"))

from app.db.session import get_sync_sessionmaker  # noqa: E402
from app.domain.planning import Pace, PlanningConstraints, WorkloadLevel  # noqa: E402
from app.models import Course, Student, StudentCourse  # noqa: E402
from app.services.planning.service import generate_plan  # noqa: E402
from app.services.scenarios import resolve_version  # noqa: E402
from sqlalchemy import select  # noqa: E402

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
    "semi-rigorous (7.3)": PlanningConstraints(workload_level=WorkloadLevel.SEMI_RIGOROUS,
                                               max_courses_per_term=6, pace=Pace.BALANCED),
}


def alternatives(session, version) -> dict[str, tuple[str, str, int]]:
    """leaf code -> (ANY_OF code, alternative child code, alternatives needed)."""
    from app.services.academic_context import composed_requirements

    reqs = composed_requirements(session, version)
    by_id = {r.id: r for r in reqs}
    out = {}
    for r in reqs:
        node = r
        while node is not None:
            parent = by_id.get(node.parent_id)
            if parent is not None and parent.requirement_type == "any_of":
                out[r.code] = (parent.code, node.code, parent.min_count or 1)
                break
            node = parent
    return out


def descendants(session, version) -> dict[str, set[str]]:
    """requirement code -> itself and every requirement beneath it."""
    from app.services.academic_context import composed_requirements

    reqs = composed_requirements(session, version)
    by_id = {r.id: r.code for r in reqs}
    children: dict[str, set[str]] = {}
    for r in reqs:
        if r.parent_id in by_id:
            children.setdefault(by_id[r.parent_id], set()).add(r.code)
    out: dict[str, set[str]] = {}

    def walk(code: str) -> set[str]:
        if code not in out:
            out[code] = {code}
            for child in children.get(code, ()):
                out[code] |= walk(child)
        return out[code]

    for r in reqs:
        walk(r.code)
    return out


def inactive(plan, below) -> set[str]:
    """Requirements under an alternative the plan did NOT choose: closed, not
    'remaining' (task 1: unselected science sequences were counted as gaps)."""
    out: set[str] = set()
    for choice in plan.metadata.alternative_choices:
        for alt in choice.alternatives:
            out |= below.get(alt.code, {alt.code})
    return out


def awareness(plan, alts) -> tuple[int, int, int]:
    courses = [pc for t in plan.terms for pc in t.courses]
    planned = {pc.course for pc in courses}
    untraced = sum(1 for pc in courses
                   if not pc.requirements and not (set(pc.unlocks) & planned))
    touched: dict[str, set[str]] = {}
    need: dict[str, int] = {}
    for pc in courses:
        for r in pc.requirements:
            if r.requirement_code in alts:
                group, alt, n = alts[r.requirement_code]
                touched.setdefault(group, set()).add(alt)
                need[group] = n
    extra = sum(max(0, len(a) - need[g]) for g, a in touched.items())
    multi = sum(1 for pc in courses if len(pc.requirements) >= 2)
    return untraced, extra, multi


def main() -> None:
    with get_sync_sessionmaker()() as session:
        version, _ = resolve_version(session, PROGRAM, "2026-2027")
        alts = alternatives(session, version)
        below = descendants(session, version)
        rows = []
        details = []
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
                ms = (time.perf_counter() - t0) * 1000      # the plan only, not reporting
                details.append({"persona": persona, "mode": mode,
                    "status": plan.status.value,
                    "remaining": [r.model_dump(mode="json") for r in plan.requirements
                                  if r.status_after not in ("satisfied", "provisionally_satisfied")
                                  and r.requirement_code not in inactive(plan, below)],
                    "issues": [i.model_dump(mode="json") for i in plan.issues],
                    "alternatives": [a.model_dump(mode="json")
                                     for a in plan.metadata.alternative_choices]})
                loads = [t.credits for t in plan.terms]
                # A workload level states no target: its credit floor (16 for
                # semi-rigorous) is the reference.
                target = cons.target_credits_per_term or (
                    Decimal(16) if cons.workload_level else cons.max_credits_per_term)
                under = [x for x in loads[:-1] if x * 10 < target * 6]
                rows.append((persona, mode, plan.status.value, len(loads),
                             "/".join(f"{x:g}" for x in loads), "/".join(str(len(t.courses)) for t in plan.terms),
                             round(statistics.fmean(abs(x - target) for x in loads), 2) if loads else "-",
                             len(under), f"{loads[-1]:g}" if loads else "-",
                             *awareness(plan, alts), round(ms),
                             ",".join(o.code for o in plan.observations)))
        session.rollback()
    head = ("persona", "mode", "status", "terms", "credits", "courses", "|dev|", "under", "last",
            "untraced", "extra_alt", "multi", "ms", "observations")
    print(" | ".join(head))
    for r in rows:
        print(" | ".join(str(x) for x in r))
    if "--details" in sys.argv:
        destination = pathlib.Path(sys.argv[sys.argv.index("--details") + 1])
        destination.write_text(json.dumps(details, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
