"""List courses whose SOC record has NO expandedTitle (home follow-ups, item 1).

    DATABASE_URL_SYNC=... python scripts/list_unexpanded_titles.py [subject ...] > titles.csv

Ingestion stores Rutgers' `expandedTitle` as `course.title` whenever SOC sends
one (and keeps the short form in `title_abbrev`). The rest - `title_abbrev IS
NULL` - only have the short SOC title. Those are the ONLY courses the frontend's
per-course fallback map (frontend/src/lib/academic/course-title.ts,
SOC_FULL_TITLES) may cover, and each entry stays `reviewed: false` until an
owner confirms the full title against a Rutgers source.

Read-only: one SELECT inside a READ ONLY transaction. Output is CSV
(course, soc_title) on stdout, optionally filtered by subject codes ("198").
"""

from __future__ import annotations

import csv
import os
import sys

from sqlalchemy import create_engine, text


def main(argv: list[str]) -> int:
    subjects = [a for a in argv if a.isdigit()]
    engine = create_engine(os.environ.get(
        "DATABASE_URL_SYNC", "postgresql+psycopg://coursepilot:coursepilot@localhost:5432/coursepilot"))
    query = text(
        "SELECT DISTINCT course_string, title FROM course WHERE title_abbrev IS NULL "
        "ORDER BY course_string")
    with engine.connect() as conn:
        conn.execute(text("SET TRANSACTION READ ONLY"))
        rows = conn.execute(query).all()
    out = csv.writer(sys.stdout)
    out.writerow(["course", "soc_title"])
    for course, title in rows:
        if subjects and course.split(":")[1] not in subjects:
            continue
        out.writerow([course, title])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
