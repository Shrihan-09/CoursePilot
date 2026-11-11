"""Failure injection for a student's DROP: break it on purpose, prove the tests notice.

Each fault is a source edit; the drop and title tests run; the file is
restored byte for byte - whatever happens. A fault is DETECTED when at least
one test fails.

Usage:  python scripts/inject_drop_failures.py [fault-letter ...]
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PY = sys.executable
ENGINE = "backend/app/services/planning/engine.py"
EDITS = "backend/app/services/planning/edits.py"
TEXT = "backend/app/domain/text.py"
TESTS = ["ingestion/tests/test_course_drop.py", "backend/tests/test_course_titles.py"]

FAULTS = {
    "A": ("backfill re-enabled: a drop re-plans freely (no strict holds, no pinned-only term)",
          [(ENGINE, 'if getattr(self, "strict_holds", False) or pinned_only:', "if False:")]),
    "B": ("the drop preview is an ordinary re-plan (strict holds off)",
          [(EDITS, "return Edit(e, holds, [], summary, touched, strict=True)",
            "return Edit(e, holds, [], summary, touched, strict=False)")]),
    "C": ("the avoid covers only the dropped term (the course can come back later)",
          [(EDITS, "CourseAvoid(course=course, term_code=None)]",
            "CourseAvoid(course=course, term_code=term)]")]),
    "D": ("the dropped term is not kept as the student left it (no pins)",
          [(EDITS, "if kept.course == course or kept.course in pinned or s is None:",
            "if True:")]),
    "E": ("a rebuild may refill the dropped term (pinned-only off)",
          [(EDITS, "credit_max=top, pinned_only=True)]", "credit_max=top, pinned_only=False)]")]),
    "F": ("the dropped course's credits are made up elsewhere (electives respread)",
          [(ENGINE, 'if getattr(self, "strict_holds", False) and held is not None:',
            "if False:")]),
    "G": ("ALL-CAPS titles shown as published",
          [(TEXT, "    if not title or not any(ch.isalpha() for ch in title) "
                  "or any(ch.islower() for ch in title):", "    if True:")]),
}


def run(letter: str) -> tuple[bool, str]:
    name, edits = FAULTS[letter]
    originals: dict[pathlib.Path, bytes] = {}
    try:
        for rel, old, new in edits:
            path = ROOT / rel
            originals.setdefault(path, path.read_bytes())
            current = path.read_bytes().decode("utf-8")
            if current.count(old) != 1:
                return False, f"INJECTION FAILED: pattern not found exactly once in {rel}"
            path.write_bytes(current.replace(old, new).encode("utf-8"))
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
        for path, data in originals.items():
            path.write_bytes(data)


def main() -> None:
    letters = sys.argv[1:] or list(FAULTS)
    missed = 0
    for letter in letters:
        detected, detail = run(letter)
        missed += not detected
        print(f"{letter:3} {'DETECTED' if detected else 'MISSED  '} {FAULTS[letter][0]}\n"
              f"    {detail}", flush=True)
    sys.exit(1 if missed else 0)


if __name__ == "__main__":
    main()
