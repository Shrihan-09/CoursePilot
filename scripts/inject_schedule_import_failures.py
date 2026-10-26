"""Damage SYNTHETIC WebReg schedule prints and show the importer catches each (Phase 7.4).

Every fault must end as a REFUSAL (a closed code, nothing staged) or a row
that cannot be confirmed as it stands - never as a silently different section:

  no_text_layer        glyphs drawn as outlines ("Microsoft Print to PDF")
  run_together_columns a course code with no readable identity in the grid
  conflicting_ids      one course/section printed with two different indexes
  two_terms            two term labels on one print
  reused_index         the index belongs to a DIFFERENT section of the course
  missing_section      an index no published section has

The first four are judged by the parser on a real PDF; the last two by the
row normalizer against a tiny in-memory catalog. Invented data only.

Usage:  python scripts/inject_schedule_import_failures.py
"""

from __future__ import annotations

import pathlib
import sys
import uuid
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "backend" / "tests")]

from support.synthetic_webreg import build_schedule_pdf, objects  # noqa: E402

from app.domain.webreg import ScheduleParseError, parse_schedule  # noqa: E402
from app.services.scheduling.imports import ready, resolve_row  # noqa: E402
from app.services.scheduling.pdf_worker import extract_layout  # noqa: E402

COURSE = "01:999:101"
CATALOG = {"91234": ("01", COURSE), "91235": ("02", COURSE)}   # index -> (section, course)


class Catalog:
    """Just enough of a session for resolve_row: sections by index."""

    def __init__(self, index):
        self.index = index

    def execute(self, query):
        hit = CATALOG.get(self.index)
        rows = [] if hit is None else [(
            SimpleNamespace(id=uuid.uuid4(), index_number=self.index, section_number=hit[0]),
            SimpleNamespace(id=uuid.uuid4(), course_string=hit[1], supplement_code=""))]
        return SimpleNamespace(all=lambda: rows)

    def scalar(self, query):
        return None

    def scalars(self, query):
        return []


def parse(items, **kw):
    out = extract_layout(build_schedule_pdf(items, **kw), 20, 200_000)
    if out[0] != "ok":
        return f"refused: {out[1]}"
    try:
        rows = parse_schedule(out[1], out[3])
    except ScheduleParseError as exc:
        return f"refused: {exc}"
    return f"ACCEPTED {len(rows)} row(s)"


def normalize(section, index):
    row = resolve_row(Catalog(index), uuid.uuid4(), {
        "term_code": "20991", "course_string": COURSE, "section_number": section,
        "index_number": index})
    verdict = ("blocked" if not ready([row]) else
               "confirmable: identity only, no history or section" if row["section_id"] is None
               else "confirmable")
    return f"{row['state']} {row['issues']} section_id={row['section_id']} -> {verdict}"


def faults() -> dict[str, str]:
    good = [(170, 610, f"{COURSE}:01-91234 (4.0)")]
    return {
        "clean": parse(objects(term="Spring 2099", records=good)),
        "no_text_layer": parse(objects(term="Spring 2099", records=good), outlined=True),
        "run_together_columns": parse(objects(term="Spring 2099", records=[
            *good, (270, 610, f"{COURSE} LEC")])),
        "conflicting_ids": parse(objects(term="Spring 2099", records=[
            *good, (370, 610, f"{COURSE}:01-91235 (4.0)")])),
        "two_terms": parse([*objects(term="Spring 2099", records=good), (40, 700, "Fall 2099")]),
        "clean_row": normalize("01", "91234"),
        "reused_index": normalize("02", "91234"),
        "missing_section": normalize("01", "99999"),
    }


def main() -> int:
    for name, outcome in faults().items():
        print(f"{name:22} {outcome}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
