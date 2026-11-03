"""Task 2 failure injection: break the planning questionnaire on purpose.

Each fault is a source edit applied in place, the Task 2 tests are run, and
the file is restored - whatever happens. A fault is DETECTED only when at
least one test FAILS an assertion and no test ERRORS (a setup error proves
nothing, so it is reported INVALID). Each run gets a fresh --basetemp outside
the repository, and tests use the TEST database explicitly.

Usage:  python scripts/inject_questionnaire_failures.py [fault-letter ...]
"""

from __future__ import annotations

import os
import pathlib
import re
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
PY = sys.executable
ENGINE = "backend/app/services/planning/engine.py"
QUESTIONNAIRE = "backend/app/services/planning/questionnaire.py"
SCHEDULER = "backend/app/services/scheduling/engine.py"
TESTS = ["backend/tests/test_planning_questionnaire.py",
         "ingestion/tests/test_workload_levels.py"]
TEST_DB = "postgresql+psycopg://coursepilot:coursepilot@localhost:5432/coursepilot_test"

FAULTS = {
    "A": ("the major-related range is ignored (no cap)", [(ENGINE,
          "            if major and role != \"anchor\" and major_count >= major_cap and not beyond_major:",
          "            if False:")]),
    "B": ("an anchor (retake or pin) is dropped when the range is full", [(ENGINE,
          "            if major and role != \"anchor\" and major_count >= major_cap and not beyond_major:",
          "            if major and major_count >= major_cap and not beyond_major:")]),
    "C": ("the graduation target no longer raises the range (silent delay)", [(ENGINE,
          "            major_cap = max(settings.major_max, need)     # never silently delay graduation",
          "            major_cap = settings.major_max")]),
    "D": ("a per-semester override is ignored", [(QUESTIONNAIRE,
          "        if override is not None and override.major_courses_min is not None:",
          "        if False:")]),
    "E": ("commitments do not lower the budget", [(QUESTIONNAIRE,
          "        reduced = base_budget - budget_reduction(self.hours)",
          "        reduced = base_budget")]),
    "F": ("honors-only courses are planned for a student not in honors", [(ENGINE,
          "        honors_gate = c.honors is not True",
          "        honors_gate = False")]),
    "G": ("fixed weekly times are not blocked in the schedule engine", [(SCHEDULER,
          "                      or overlaps_blocked(c.meetings, blocked))",
          "                      or None)")]),
    "H": ("major-related means only the major's own subject (math/science dropped)", [(ENGINE,
          "        return any(self._is_major_node(code) for code in serves.get(key, ()))",
          "        return key.split(\":\")[1] == self.major_subject and any(\n"
          "            self._is_major_node(code) for code in serves.get(key, ()))")]),
    "I": ("the pace/level conflict warning is not raised", [(ENGINE,
          "        conflict = pace_level_conflict(c)",
          "        conflict = None")]),
    "J": ("a term no longer says why it fits", [(ENGINE,
          "                            fit=(\"Why this fits you: \" + \" + \".join(why) + \" → \"",
          "                            fit=None and (\"\"")]),
    "K": ("the credit floor gives way to the major-related range", [(ENGINE,
          "            for over_budget in (False, True):",
          "            for over_budget in ():")]),
    "L": ("an honors course named only in the catalog title is planned", [(ENGINE,
          "        if course is not None and HONORS_TITLE.search(course.title or \"\"):",
          "        if False:")]),
    "M": ("a course needing special permission on every section is planned", [(ENGINE,
          "            if key not in dependency_targets and self._permission_only(term, key):",
          "            if False:")]),
    "N": ("the student's own credit range is ignored", [(QUESTIONNAIRE,
          "        if c.credit_min is not None:",
          "        if False:")]),
}


def run(letter: str) -> tuple[bool | None, str]:
    name, edits = FAULTS[letter]
    originals: dict[pathlib.Path, str] = {}
    try:
        for rel, old, new in edits:
            path = ROOT / rel
            originals.setdefault(path, path.read_text(encoding="utf-8"))
            current = path.read_text(encoding="utf-8")
            if current.count(old) != 1:
                return False, f"INJECTION FAILED: pattern not found exactly once in {rel}"
            path.write_text(current.replace(old, new), encoding="utf-8")
        env = {**os.environ, "DATABASE_URL": TEST_DB, "DATABASE_URL_SYNC": TEST_DB,
               "TEST_DATABASE_URL": TEST_DB}
        basetemp = pathlib.Path(tempfile.mkdtemp(prefix="cp-inject-")) / "t"
        proc = subprocess.run([PY, "-m", "pytest", *TESTS, "-q", "-p", "no:cacheprovider",
                               "-rfE", "--basetemp", str(basetemp)],
                              cwd=ROOT, capture_output=True, text=True, env=env)
        lines = proc.stdout.strip().splitlines()
        tail = lines[-1] if lines else proc.stderr[-200:]
        failed_n = int(m.group(1)) if (m := re.search(r"(\d+) failed", tail)) else 0
        errors_n = int(m.group(1)) if (m := re.search(r"(\d+) errors?", tail)) else 0
        failed = sorted({ln.split("::")[-1].split(" ")[0] for ln in lines
                         if ln.startswith("FAILED")})
        if errors_n:
            return None, f"{tail} | setup errors - not evidence either way"
        return failed_n > 0, f"{tail} | {', '.join(failed[:8])}"
    finally:
        for path, text in originals.items():
            path.write_text(text, encoding="utf-8")


def main() -> None:
    letters = sys.argv[1:] or list(FAULTS)
    detected_n = invalid = 0
    for letter in letters:
        detected, detail = run(letter)
        verdict = "INVALID " if detected is None else "DETECTED" if detected else "MISSED  "
        detected_n += detected is True
        invalid += detected is None
        print(f"{letter:3} {verdict} {FAULTS[letter][0]}\n    {detail}", flush=True)
    print(f"{detected_n}/{len(letters)} detected by assertion failures"
          + (f"; {invalid} INVALID (setup errors)" if invalid else ""))


if __name__ == "__main__":
    main()
