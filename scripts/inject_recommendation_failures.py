"""Phase 7.6 failure injection: break requirement-aware recommendations on purpose.

Each fault is a source edit applied in place, the Prompt 05 test suite is
run, and the file is restored - whatever happens. A fault is DETECTED when at
least one test fails. Same harness as scripts/inject_planning_failures.py.

Usage:  python scripts/inject_recommendation_failures.py [fault-letter ...]
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PY = sys.executable
ENGINE = "backend/app/services/planning/engine.py"
SERVICE = "backend/app/services/planning/service.py"
RANKING = "backend/app/services/planning/ranking.py"
CANDIDATES = "backend/app/services/scheduling/candidates.py"
DECLARATION = "backend/app/services/declaration.py"
TESTS = ["ingestion/tests/test_requirement_aware_recommendations.py"]

FAULTS = {
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


def run(letter: str) -> tuple[bool, str]:
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
    letters = sys.argv[1:] or list(FAULTS)
    missed = 0
    for letter in letters:
        detected, detail = run(letter)
        missed += not detected
        print(f"{letter:3} {'DETECTED' if detected else 'MISSED  '} {FAULTS[letter][0]}\n"
              f"    {detail}", flush=True)
    print(f"{len(letters) - missed}/{len(letters)} detected")


if __name__ == "__main__":
    main()
