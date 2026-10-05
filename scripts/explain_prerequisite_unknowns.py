"""Phase 6.4: WHY prerequisites are UNKNOWN for a student with B in every
referenced course - the honest decomposition of the UNKNOWN rate.

Groups each UNKNOWN by its cause (Phase 6.2 unsupported expression, a
condition text still uninterpreted - and from which source field - a grade
the condition cannot compare, ...).

Usage:  DATABASE_URL_SYNC=... python scripts/explain_prerequisite_unknowns.py [term]
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


def main() -> None:
    url = os.environ.get("DATABASE_URL_SYNC",
                         "postgresql+psycopg://coursepilot:coursepilot@localhost:5432/coursepilot")
    causes: Counter = Counter()
    sources: Counter = Counter()
    with Session(create_engine(url)) as s:
        rows = s.execute(select(Course.course_string, CoursePrerequisite)
                         .join(Course, Course.id == CoursePrerequisite.course_id)
                         .where(CoursePrerequisite.term_code == TERM)).all()
        by_course: dict = {}
        for key, row in rows:
            by_course.setdefault(key, []).append(row)
        for key, prereqs in by_course.items():
            refs = set()
            for r in prereqs:
                if r.expression:
                    refs |= set(course_keys(from_json(r.expression)))
            history = {k: [Attempt(k, "20251", "completed", "B")] for k in refs}
            result = _check(key, TERM, True, prereqs, history)
            if result.status.value != "unknown":
                continue
            row = prereqs[0]
            kinds = sorted({r.split(":")[0] for r in result.reasons})
            causes[" + ".join(kinds) or "none"] += 1
            src = ("courseNotes" if row.condition_note else "") + \
                  ("+sectionNotes:all" if row.section_condition_note else "")
            sources[src or ("expression only" if row.raw_text else "?")] += 1
    print(json.dumps({"term": TERM, "unknown_by_cause": dict(causes.most_common()),
                      "unknown_by_condition_source": dict(sources.most_common())}, indent=1))


if __name__ == "__main__":
    main()
