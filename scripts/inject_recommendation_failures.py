"""Phase 7.6 failure injection: break requirement-aware recommendations on purpose.

Each fault is a source edit applied in place, the Prompt 05 test suite is
run, and the file is restored - whatever happens. A fault is DETECTED only
when at least one test FAILS an assertion and no test ERRORS: a setup error
(a database, a temp folder) proves nothing about the fault, so such a run is
reported INVALID, never counted as detected. Each run gets a fresh --basetemp
outside the repository, and tests use the TEST database explicitly.

Usage:  python scripts/inject_recommendation_failures.py [fault-letter ...]
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
SERVICE = "backend/app/services/planning/service.py"
RANKING = "backend/app/services/planning/ranking.py"
CANDIDATES = "backend/app/services/scheduling/candidates.py"
DECLARATION = "backend/app/services/declaration.py"
TESTS = ["ingestion/tests/test_requirement_aware_recommendations.py"]
ALTERNATIVE_TESTS = ["ingestion/tests/test_science_alternatives.py",
                     "backend/tests/test_requirement_alternatives.py"]

TEST_DB = "postgresql+psycopg://coursepilot:coursepilot@localhost:5432/coursepilot_test"
#: Faults judged by the generic alternatives suites (task 1).
ALTERNATIVE_FAULTS = ("N", "O", "P", "Q", "R")

FAULTS = {
    "Q": ("an explicit student choice is ignored", [(ENGINE,
          "            if requested:\n                chosen = [c for c in node.children",
          "            if False:\n                chosen = [c for c in node.children")]),
    "R": ("an unparseable prerequisite is treated as satisfied", [(
          "backend/app/domain/prerequisites.py",
          '            _add(out.unknown_reasons, f"unsupported:{e.reason}")\n'
          "            return PrereqStatus.UNKNOWN",
          '            _add(out.unknown_reasons, f"unsupported:{e.reason}")\n'
          "            return PrereqStatus.SATISFIED")]),
    "N": ("one-of opens all its competing children", [(ENGINE,
          "            children = ([c for c in open_ if c.requirement_code in selected][:need]\n"
          "                        if selected is not None else open_[:need])",
          "            children = open_")]),
    "O": ("already-started alternatives are ignored", [(ENGINE,
          'started = self._record_progress.get(child.requirement_code, Decimal(0)) > 0',
          'started = False')]),
    "P": ("unsupported corequisite silently satisfied", [(
          "backend/app/services/course_eligibility.py",
          'return CorequisiteCheck(True, PrereqStatus.UNKNOWN, raw_text=row.raw_text,',
          'return CorequisiteCheck(True, PrereqStatus.SATISFIED, raw_text=row.raw_text,')]),
    "A": ("a course every section of which is closed to the student is still planned", [(ENGINE,
          "                 and self.course_info[k].credits is not None\n"
          "                 and k not in restricted}",
          "                 and self.course_info[k].credits is not None}")]),
    "B": ("the multi-requirement preference ignores the sharing policy", [(ENGINE,
          "        return len({self._system(c) for c in codes}) if self.share else 1",
          "        return len(codes)")]),
    "C": ("Core goals not checked against the planned term's SOC coreCodes", [(ENGINE,
          "                if verdict is False:",
          "                if verdict is None and False:")]),
    "D": ("a confirmed Degree Navigator audit does not drive planning", [(SERVICE,
          "    result = PlanningEngine(session, constraints, overlay).plan(",
          "    result = PlanningEngine(session, constraints, None).plan(")]),
    "E": ("an unmapped open DN requirement is dropped silently", [(SERVICE,
          "                     \"kind\": row.kind}))\n    return out",
          "                     \"kind\": row.kind}))\n    return []")]),
    "F": ("a school without an encoded Core is treated as encoded (SAS substituted)", [(SERVICE,
          "    sets = school_requirement_sets(session, version)\n    if sets:",
          "    sets = school_requirement_sets(session, version)\n    if True:")]),
    "G": ("every requirement status labelled estimated (DN source lost from the trace)", [(ENGINE,
          "            if status is not None:\n                return \"degree_navigator\", status.rule",
          "            if status is not None:\n                return \"estimated\", None")]),
    "H": ("an undeclared student is treated as a declared major by section restrictions",
          [(CANDIDATES,
            "    if student.declaration_status == \"undeclared\":",
            "    if student.declaration_status == \"__never__\":")]),
    "I": ("declaration grade check accepts any passing grade (D counted as C or better)",
          [(DECLARATION,
            "    verdicts = [meets_minimum(a.outcome, minimum) for a in attempts",
            "    verdicts = [meets_minimum(a.outcome, \"D\") for a in attempts")]),
    "J": ("in-progress (WebReg) courses do not unlock next-term prerequisites", [(ENGINE,
          "        out += [Attempt(k, t, \"completed\", \"A\") for k, t in sorted(self.in_progress.items())]",
          "        out += []")]),
    "K": ("sequencing keys removed (long chains and one-season courses not preferred)", [(RANKING,
          "        -evidence.unlock_chain_depth,\n"
          "        0 if evidence.season_only else 1,\n",
          "")]),
    "L": ("progress on an unneeded ANY_OF alternative counted as useful", [(ENGINE,
          "        if node.requirement_type == \"any_of\" and node.status in MET and children:",
          "        if False:")]),
    "M": ("no structured trace on recommendations", [(ENGINE,
          "                self._trace(pc)\n",
          "")]),
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
        tests = ALTERNATIVE_TESTS if letter in ALTERNATIVE_FAULTS else TESTS
        basetemp = pathlib.Path(tempfile.mkdtemp(prefix="cp-inject-")) / "t"
        proc = subprocess.run([PY, "-m", "pytest", *tests, "-q", "-p", "no:cacheprovider",
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
