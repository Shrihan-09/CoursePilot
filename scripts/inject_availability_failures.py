"""Phase 6.7 failure injection: break registration availability on purpose.

Each fault is a source edit; the availability suites run (offline, SQLite);
the file is restored whatever happens. DETECTED = at least one test failed.
A fault with no observable effect is reported as MISSED, never claimed as
caught.

Usage:  python scripts/inject_availability_failures.py [fault ...]
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PY = sys.executable
DOMAIN = "backend/app/domain/availability.py"
SERVICE = "backend/app/services/availability.py"
CANDS = "backend/app/services/scheduling/candidates.py"
ENGINE = "backend/app/services/scheduling/engine.py"
SCHED = "backend/app/services/scheduling/service.py"
TESTS = ["ingestion/tests/test_live_availability.py", "backend/tests/test_availability_domain.py",
         "backend/tests/test_schedule_domain.py"]

_IMPORT = (CANDS, "from app.domain.availability import CURRENT_WINDOW\n",
           "from app.domain.availability import CURRENT_WINDOW, AvailabilityStatus\n")


def _force_open(component: str):
    return [_IMPORT, (CANDS, "                    availability=availability_of(s.index_number),",
                      ("                    availability=(availability_of(s.index_number)\n"
                      f"                        if component != {component!r} else\n"
                      "                        availability_of(s.index_number).model_copy(\n"
                      "                            update={'state': AvailabilityStatus.OPEN})),"))]


FAULTS = {
    "A": ("lecture (primary) availability lookup broken - always open",
          _force_open("primary")),
    "B": ("linked lab (required companion) lookup broken - always open",
          _force_open("required_companion")),
    "C": ("term scoping broken - refresh applied to every term's sections", [(SERVICE,
          "        .where(CourseSection.term_code == term_code)).all())",
          "        ).all())")]),
    "D": ("bundle aggregation broken - only the primary component decides", [(DOMAIN,
          "    if AvailabilityStatus.CLOSED in values:\n        return AvailabilityStatus.CLOSED",
          "    if values[0] is AvailabilityStatus.CLOSED:\n        return AvailabilityStatus.CLOSED")]),
    "E": ("stale handling broken - old observations called current", [(DOMAIN,
          "<= CURRENT_WINDOW else Freshness.STALE)",
          "<= CURRENT_WINDOW * 1000 else Freshness.STALE)")]),
    "F": ("unknown handling broken - an unknown component counts as open", [(DOMAIN,
          "    if AvailabilityStatus.UNKNOWN in values:\n        return AvailabilityStatus.UNKNOWN",
          "    if AvailabilityStatus.UNKNOWN in values:\n        return AvailabilityStatus.OPEN")]),
    "G": ("source disagreement handling broken - newest silently wins", [(DOMAIN,
          "              and o.status is not newest.status]",
          "              and o.status is not newest.status and False]")]),
    "H": ("empty / non-overlapping payload accepted - closes every section", [(DOMAIN,
          "    if not payload.indexes:", "    if False:"), (DOMAIN,
          "    if known and not matched:", "    if False:")]),
    "I": ("wrong-term payload accepted", [(SERVICE,
          "    if url_term is not None and url_term != term_code:", "    if False:")]),
    "J": ("unchanged state recorded as a transition (OPEN -> OPEN)", [(DOMAIN,
          "            if previous.get(i) is not s]", "            ]")]),
    "K": ("schedule generation writes availability (outside the session's tracking)", [(SCHED,
          "from sqlalchemy.orm import Session\n",
          "from sqlalchemy import text\nfrom sqlalchemy.orm import Session\n"), (SCHED,
          "    if session.new or session.dirty or session.deleted:",
          ("    session.execute(text(\"UPDATE availability_refresh SET detail = 'touched'\"))\n"
          "    if session.new or session.dirty or session.deleted:"))]),
    "L": ("per-section availability queries (N+1)", [(SERVICE,
          ("        snapshot = {row.section_id: row for row in session.scalars(\n"
          "            select(SectionAvailability).where(SectionAvailability.section_id.in_(ids)))}"),
          ("        snapshot = {i: row for i in ids for row in session.scalars(\n"
          "            select(SectionAvailability).where(SectionAvailability.section_id == i))}"))]),
    "M": ("prefer_open discards closed sections", [(ENGINE,
          "            if prefs.availability is AvailabilityMode.REQUIRE_OPEN:",
          "            if True:")]),
    "N": ("ranking ignores current availability", [(ENGINE,
          "    if not _current(c):\n        return 0, 0", "    if True:\n        return 0, 0")]),
    "O": ("require_open accepts stale availability", [(ENGINE,
          "                if a.freshness is not AvailabilityFreshness.CURRENT:",
          "                if False:")]),
    "P": ("academic co-requisite folded into the first course's bundle", [(ENGINE,
          "        by_course[c.course].append(c)", "        by_course[choices[0].course].append(c)")]),
    "Q": ("structural courses.json download presented as current", [(SERVICE,
          "                _aware(retrieved), COURSES_SOURCE, can_be_current=False))",
          "                _aware(retrieved), COURSES_SOURCE, can_be_current=True))")]),
}


def run(letter: str) -> tuple[bool, str]:
    _, edits = FAULTS[letter]
    originals: dict[pathlib.Path, str] = {}
    try:
        for rel, old, new in edits:
            path = ROOT / rel
            originals.setdefault(path, path.read_text(encoding="utf-8"))
            current = path.read_text(encoding="utf-8")
            if current.count(old) != 1:
                return False, f"INJECTION FAILED: pattern not found exactly once in {rel}"
            path.write_text(current.replace(old, new), encoding="utf-8")
        env = {k: v for k, v in os.environ.items()
               if k not in ("DATABASE_URL", "DATABASE_URL_SYNC", "TEST_DATABASE_URL")}
        proc = subprocess.run([PY, "-m", "pytest", *TESTS, "-q", "-p", "no:cacheprovider",
                               "-rfE"], cwd=ROOT, capture_output=True, text=True, env=env,
                              check=False)
        lines = proc.stdout.strip().splitlines()
        tail = lines[-1] if lines else proc.stderr[-200:]
        failed = sorted({ln.split("::")[-1].split(" ")[0] for ln in lines
                         if ln.startswith(("FAILED", "ERROR"))})
        return proc.returncode != 0, f"{tail} | {', '.join(failed[:6])}"
    finally:
        for path, text in originals.items():
            path.write_text(text, encoding="utf-8")


def main() -> None:
    for letter in sys.argv[1:] or list(FAULTS):
        detected, detail = run(letter)
        print(f"{letter:3} {'DETECTED' if detected else 'MISSED  '} {FAULTS[letter][0]}\n"
              f"    {detail}", flush=True)


if __name__ == "__main__":
    main()
