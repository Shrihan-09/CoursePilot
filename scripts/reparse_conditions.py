"""Reparse stored condition source notes; dry run unless --apply is passed.

No new source text, course identities, offerings, or student data are created.
"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.db.session import get_sync_sessionmaker  # noqa: E402
from app.domain.conditions import CONDITION_PARSER_VERSION, interpret  # noqa: E402
from app.domain.prerequisites import course_keys, from_json  # noqa: E402
from app.models import Course, CoursePrerequisite  # noqa: E402
from sqlalchemy import select  # noqa: E402


def main():
    with get_sync_sessionmaker()() as session:
        courses = {}
        for course in session.scalars(select(Course).where(Course.supplement_code == "")):
            courses.setdefault((course.subject_code, course.course_number), set()).add(course.course_string)
        def resolve(subject, number):
            matches = courses.get((subject, number), ())
            return next(iter(matches)) if len(matches) == 1 else None
        changed, semantic = 0, []
        for row in session.scalars(select(CoursePrerequisite)):
            parsed = interpret([row.condition_note, row.section_condition_note],
                set(course_keys(from_json(row.expression))) if row.expression else set(), resolve)
            if parsed == row.interpreted_conditions:
                continue
            before = dict(row.interpreted_conditions or {})
            after = dict(parsed or {})
            before.pop("parser_version", None)
            after.pop("parser_version", None)
            if before != after:
                code = session.get(Course, row.course_id).course_string
                semantic.append(dict(course=code, term=row.term_code, before=before, after=after))
            row.interpreted_conditions = parsed
            row.condition_parser_version = CONDITION_PARSER_VERSION if parsed else None
            changed += 1
        if "--apply" in sys.argv:
            session.commit()
        else:
            session.rollback()
        print(json.dumps(dict(applied="--apply" in sys.argv, changed=changed,
                              semantic_changes=semantic), indent=2))


if __name__ == "__main__":
    main()
