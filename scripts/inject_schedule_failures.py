"""Phase 6.6 failure injection: break the Schedule Engine on purpose, prove tests notice.

Each fault is a source edit; the schedule test suites run; the file is
restored whatever happens. DETECTED = at least one test failed. A fault with
no observable effect is reported as MISSED, never claimed as caught.

Usage:  python scripts/inject_schedule_failures.py [fault-letter ...]
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PY = sys.executable
S = "backend/app/services/scheduling/"
MEETINGS, SEARCH, CANDS, ENGINE, SERVICE = (S + f for f in (
    "meetings.py", "search.py", "candidates.py", "engine.py", "service.py"))
TESTS = ["ingestion/tests/test_schedule_engine.py", "backend/tests/test_schedule_domain.py"]

FAULTS = {
    "A": ("direct time conflict ignored", [(MEETINGS,
          "    return a.start_minute < b.end_minute + buffer and b.start_minute < a.end_minute + buffer",
          "    return False")]),
    "B": ("only the first meeting of each section checked", [(MEETINGS,
          "    for x in a:\n        for y in b:\n            if conflict(x, y, buffer):",
          "    for x in a[:1]:\n        for y in b[:1]:\n            if conflict(x, y, buffer):")]),
    "C": ("back-to-back meetings treated as overlapping", [(MEETINGS,
          "    return a.start_minute < b.end_minute + buffer and b.start_minute < a.end_minute + buffer",
          "    return a.start_minute <= b.end_minute + buffer and b.start_minute <= a.end_minute + buffer")]),
    "D": ("active date ranges ignored", [(MEETINGS,
          "    if None in (a.start_date, a.end_date, b.start_date, b.end_date):\n        return True",
          "    if True:\n        return True")]),
    "E": ("TBA / arranged time treated as conflict-free", [(MEETINGS,
          "        elif desc in CLASS_MODES:\n            kind = MeetingKind.TBA\n        else:\n"
          "            kind = MeetingKind.ARRANGED",
          "        else:\n            kind = MeetingKind.ASYNCHRONOUS")]),
    "F": ("a known failed section restriction ignored", [(ENGINE,
          "                why[\"restriction_not_satisfied\"].append(c.index_number)\n"
          "                continue",
          "                why[\"restriction_not_satisfied\"].append(c.index_number)")]),
    "G": ("UNKNOWN restriction treated as satisfied", [(CANDS,
          "        outcome = RestrictionOutcome.UNKNOWN\n        reasons.append(\"student_attributes_incomplete\")",
          "        outcome = RestrictionOutcome.SATISFIED\n        reasons.append(\"student_attributes_incomplete\")")]),
    "H": ("closed section treated as open", [(CANDS,
          "state=AvailabilityState.OPEN if s.open_status else AvailabilityState.CLOSED,",
          "state=AvailabilityState.OPEN,")]),
    "I": ("archived availability presented as live", [(CANDS,
          "    availability_base = dict(freshness=AvailabilityFreshness.ARCHIVED,",
          "    availability_base = dict(freshness=AvailabilityFreshness.LIVE,")]),
    "J": ("required linked component dropped", [(CANDS,
          "                slots.append((sup, \"required_companion\"))",
          "                pass")]),
    "K": ("deterministic tie-breaking removed", [(ENGINE,
          "            score.class_days, score.gap_minutes, score.closed_sections_if_live,\n"
          "            tuple(c.index_number for c in sorted(choices, key=lambda c: c.slot)))",
          "            score.class_days, score.gap_minutes, score.closed_sections_if_live)"),
          (ENGINE,
          "        keep.sort(key=lambda c: (c.restriction.outcome is RestrictionOutcome.UNKNOWN,\n"
          "                                 not c.time_verified, c.index_number))",
          "        import random\n        random.shuffle(keep)\n"
          "        keep.sort(key=lambda c: (c.restriction.outcome is RestrictionOutcome.UNKNOWN,\n"
          "                                 not c.time_verified))")]),
    "L": ("a requested course silently dropped from the schedule", [(ENGINE,
          "    if term.not_offered:\n        return result(ScheduleStatus.NO_VALID_SCHEDULE)",
          "    pass")]),
    "M": ("schedule generation mutates the student", [(SERVICE,
          "    term = load_term(session, term_code, requested, student_attributes(session, student))",
          "    student.catalog_year = student.catalog_year\n    student.external_ref = 'touched'\n"
          "    term = load_term(session, term_code, requested, student_attributes(session, student))")]),
    "N": ("sections from the wrong term mixed in", [(CANDS,
          "        .where(Course.course_string.in_(sorted(set(courses))),\n"
          "               CourseSection.term_code == term_code,\n"
          "               CourseOffering.term_code == term_code)).all()",
          "        .where(Course.course_string.in_(sorted(set(courses))))).all()")]),
    "O": ("duplicate course requests kept", [(SERVICE,
          "    unique = sorted(set(cleaned))",
          "    unique = sorted(cleaned)")]),
    "P": ("course eligibility bypassed for direct requests", [(SERVICE,
          "    if offered:\n        checks = check_proposal(",
          "    if False:\n        checks = check_proposal(")]),
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
                               "-rfE"], cwd=ROOT, capture_output=True, text=True, env=env)
        lines = proc.stdout.strip().splitlines()
        tail = lines[-1] if lines else proc.stderr[-200:]
        failed = sorted({ln.split("::")[-1].split(" ")[0] for ln in lines
                         if ln.startswith(("FAILED", "ERROR"))})
        return proc.returncode != 0, f"{tail} | {', '.join(failed[:8])}"
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
