"""Offering-evidence strength for upcoming unpublished terms (Phase 7.3). Read-only.

    DATABASE_URL_SYNC=... python scripts/measure_offering_evidence.py [term ...]

For every course in the requirement pools of every loaded program version
(majors and school/Core sets), reports the OfferingEvidence kind CoursePilot
would use for each target term, and the distribution of "offered in N of the
last M same-season terms". Run it before and after a historical backfill to
measure what the extra history buys. Writes nothing.
"""

from __future__ import annotations

import collections
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.models import Course, RequirementCourseOption  # noqa: E402
from app.services.planning.offerings import OfferingIndex  # noqa: E402

TERMS = sys.argv[1:] or ["20279", "20281"]


def main() -> int:
    engine = create_engine(os.environ.get(
        "DATABASE_URL_SYNC", "postgresql+psycopg://coursepilot:coursepilot@localhost:5432/coursepilot"))
    with Session(engine) as s:
        courses = set(s.scalars(select(Course.course_string).join(
            RequirementCourseOption, RequirementCourseOption.course_id == Course.id)
            .where(Course.supplement_code == "")))
        index = OfferingIndex(s, courses)
        loaded = sorted(index.coverage)
        print(f"requirement-pool courses: {len(courses)} | loaded SOC terms: {len(loaded)} "
              f"({loaded[0]}..{loaded[-1]})")
        for term in TERMS:
            kinds, ratio = collections.Counter(), collections.Counter()
            for course in sorted(courses):
                ev = index.evidence(course, term)
                kinds[ev.kind.value] += 1
                if ev.same_season_loaded:
                    ratio[f"{ev.same_season_offered}/{ev.same_season_loaded}"] += 1
            print(f"\n{term}: authority={index.evidence(next(iter(courses)), term).target_term_authority}")
            for kind, n in kinds.most_common():
                print(f"  {kind:34} {n:5}  ({100 * n / len(courses):.1f}%)")
            top = sorted(ratio.items(), key=lambda kv: (-int(kv[0].split('/')[0]), kv[0]))
            print("  offered-in-N-of-M same-season terms:", dict(top[:8]))
        s.rollback()
    return 0


if __name__ == "__main__":
    sys.exit(main())
