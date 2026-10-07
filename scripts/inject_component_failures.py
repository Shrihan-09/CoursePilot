"""Phase 6.6.1 failure injection: break linked-component handling on purpose.

Each fault is a source edit; the component, schedule, planning and
eligibility suites run; the file is restored whatever happens. DETECTED = at
least one test failed. A fault with no observable effect is reported as
MISSED, never claimed as caught.

Usage:  python scripts/inject_component_failures.py [fault ...]
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PY = sys.executable
CANDS = "backend/app/services/scheduling/candidates.py"
ENGINE = "backend/app/services/scheduling/engine.py"
MEET = "backend/app/services/scheduling/meetings.py"
ELIG = "backend/app/services/course_eligibility.py"
LOADER = "ingestion/coursepilot_ingestion/loaders/prerequisites.py"
TESTS = ["ingestion/tests/test_linked_components.py", "ingestion/tests/test_schedule_engine.py",
         "ingestion/tests/test_planning_engine.py",
         "ingestion/tests/test_course_eligibility_pipeline.py",
         "backend/tests/test_schedule_domain.py"]

FAULTS = {
    "A": ("required lab record dropped from the bundle", [(CANDS,
          '                slots.append((sup, "required_companion", evidence))',
          "                pass")]),
    "B": ("an unproven (optional) record forced into the bundle", [(CANDS,
          "                return s.index_number, text.strip()\n    return None",
          "                return s.index_number, text.strip()\n"
          "    return records[0][1].index_number, \"(no note)\"")]),
    "C": ("academic co-requisites ignored by eligibility", [(ELIG,
          "    if not rows:\n        return CorequisiteCheck(False, PrereqStatus.SATISFIED,",
          "    if True:\n        return CorequisiteCheck(False, PrereqStatus.SATISFIED,")]),
    "C2": ("co-requisite on SOME sections dropped at load (the pre-6.6.1 behaviour)", [(LOADER,
           "                if marked:",
           "                if False:")]),
    "C3": ("unmet co-requisite on some sections treated as satisfied", [(ELIG,
           "        status = PrereqStatus.UNKNOWN\n        reasons.append(\"corequisite_on_some_sections\")",
           "        status = PrereqStatus.SATISFIED\n        reasons.append(\"corequisite_on_some_sections\")")]),
    "D": ("registration component presented as an academic (primary) course", [(CANDS,
          '                slots.append((sup, "required_companion", evidence))',
          '                slots.append((sup, "primary", evidence))')]),
    "E": ("the second component's meetings ignored in conflict checks", [(ENGINE,
          "        out[slot] = [Candidate(\n            slot=slot, index=c.index_number, meetings=tuple(c.meetings),",
          "        out[slot] = [Candidate(\n            slot=slot, index=c.index_number,\n"
          "            meetings=tuple(c.meetings) if c.component == \"primary\" else (),")]),
    "F": ("component restriction ignored", [(CANDS,
          "                        restrictions[s.id], attrs, open_to_text=s.open_to_text,",
          "                        restrictions[s.id] if component == \"primary\" else [], attrs,\n"
          "                        open_to_text=s.open_to_text,")]),
    "G": ("meeting times stated only in notes treated as verified", [(CANDS,
          "                    time_verified=time_verified(ms) and prose is None,",
          "                    time_verified=time_verified(ms),")]),
    "H'": ("prose section pairing with another requested course not flagged", [(ENGINE,
           "            if other and other != c.course:",
           "            if False:")]),
    "I": ("zero-credit lab record adds credits", [(ENGINE,
          "        credits = [c.credits for c in choices]",
          "        credits = [c.credits or Decimal(4) for c in choices]")]),
    "J": ("positive-credit academic lab counted as zero credits", [(ENGINE,
          "        credits = [c.credits for c in choices]",
          "        credits = [Decimal(0) if c.meeting_components == [\"LAB\"] else c.credits\n"
          "                   for c in choices]")]),
    "K": ("session dates ignored (components in different sessions)", [(MEET,
          "    if None in (a.start_date, a.end_date, b.start_date, b.end_date):\n        return True",
          "    if True:\n        return True")]),
    "L": ("conflicting required component silently dropped to find a schedule", [(ENGINE,
          "    if options:\n        return result(ScheduleStatus.OPTIONS_FOUND, options, stats)",
          "    if not options and any(\"#\" in s for s in term.candidates):\n"
          "        for s in [s for s in term.candidates if \"#\" in s]:\n"
          "            del term.candidates[s]\n"
          "        return schedule(term, courses, prefs, max_results, base_issues, relationships)\n"
          "    if options:\n        return result(ScheduleStatus.OPTIONS_FOUND, options, stats)")]),
}


def run(name: str) -> tuple[bool, str]:
    _, edits = FAULTS[name]
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
    for name in sys.argv[1:] or list(FAULTS):
        detected, detail = run(name)
        print(f"{name:3} {'DETECTED' if detected else 'MISSED  '} {FAULTS[name][0]}\n"
              f"    {detail}", flush=True)


if __name__ == "__main__":
    main()
