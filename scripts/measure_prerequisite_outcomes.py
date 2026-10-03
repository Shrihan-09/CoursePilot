"""Phase 6.4: prerequisite outcomes for synthetic students, before vs after.

For every published prerequisite in one term, three synthetic histories are
evaluated through the REAL service code (app.services.prerequisites._check):

  prepared_B  every referenced course completed with a B
  prepared_D  every referenced course completed with a D
  unprepared  nothing taken

The distribution of SATISFIED / UNSATISFIED / UNKNOWN is what a planner
would see. Rows loaded before Phase 6.4 (interpreted_conditions NULL) take
the Phase 6.2 path, so running this before and after a reload is the
before/after comparison.

Usage:  DATABASE_URL_SYNC=... python scripts/measure_prerequisite_outcomes.py [term] [label]
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
from collections import Counter

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "backend")]

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.domain.attempts import Attempt  # noqa: E402
from app.domain.prerequisites import course_keys, from_json  # noqa: E402
from app.models import Course, CoursePrerequisite  # noqa: E402
from app.services.prerequisites import _check  # noqa: E402

TERM = sys.argv[1] if len(sys.argv) > 1 else "20269"
LABEL = sys.argv[2] if len(sys.argv) > 2 else "run"


def main() -> None:
    url = os.environ.get("DATABASE_URL_SYNC",
                         "postgresql+psycopg://coursepilot:coursepilot@localhost:5432/coursepilot")
    out: dict = {"term": TERM, "label": LABEL}
    with Session(create_engine(url)) as s:
        rows = s.execute(select(Course.course_string, CoursePrerequisite)
                         .join(Course, Course.id == CoursePrerequisite.course_id)
                         .where(CoursePrerequisite.term_code == TERM)).all()
        by_course: dict = {}
        for key, row in rows:
            by_course.setdefault(key, []).append(row)
        out["offerings_with_prerequisite"] = len(by_course)
        for scenario in ("prepared_B", "prepared_D", "unprepared"):
            counts: Counter = Counter()
            for key, prereqs in by_course.items():
                refs = set()
                for r in prereqs:
                    if r.expression:
                        refs |= set(course_keys(from_json(r.expression)))
                if scenario == "unprepared":
                    history = {}
                else:
                    grade = scenario[-1]
                    history = {k: [Attempt(k, "20251", "completed", grade)] for k in refs}
                counts[_check(key, TERM, True, prereqs, history).status.value] += 1
            out[scenario] = dict(counts)
    print(json.dumps(out))


if __name__ == "__main__":
    main()
