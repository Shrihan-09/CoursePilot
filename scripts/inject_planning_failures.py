"""Phase 6.5 failure injection: break the planner on purpose, prove the tests notice.

Each fault is a source edit applied to a scratch copy of the file, the
planning test suites are run, and the file is restored - whatever happens.
A fault is DETECTED when at least one test fails.

Usage:  python scripts/inject_planning_failures.py [fault-letter ...]
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PY = sys.executable
ENGINE = "backend/app/services/planning/engine.py"
OFFERINGS = "backend/app/services/planning/offerings.py"
SERVICE = "backend/app/services/planning/service.py"
TESTS = ["ingestion/tests/test_planning_engine.py", "backend/tests/test_planning_domain.py"]

FAULTS = {
    "A": ("UNKNOWN treated as eligible", [(ENGINE,
          "        if b.status is PrereqStatus.SATISFIED:\n            return EligibilityState.CONDITIONAL_ON_PLAN",
          "        if b.status in (PrereqStatus.SATISFIED, PrereqStatus.UNKNOWN):\n"
          "            return EligibilityState.CONDITIONAL_ON_PLAN")]),
    "B": ("prerequisite ordering ignored (an unmet prerequisite does not hold a course back)",
          [(ENGINE,
            "                 if s in (EligibilityState.SATISFIED_BY_HISTORY,\n"
            "                          EligibilityState.CONDITIONAL_ON_PLAN)\n"
            "                 and evaluated[k][2].kind in PLACEABLE",
            "                 if s in (EligibilityState.SATISFIED_BY_HISTORY,\n"
            "                          EligibilityState.CONDITIONAL_ON_PLAN,\n"
            "                          EligibilityState.UNSATISFIED)\n"
            "                 and evaluated[k][2].kind in PLACEABLE")]),
    # Kept to record a NEUTRALISED fault: candidates are evaluated before any
    # same-term pick, so same-term courses never reach the hypothesis.
    "B0": ("same-term planned courses counted as passed (structurally unreachable)", [(ENGINE,
           'out = [Attempt(k, t, "completed", "A") for k, t in sorted(planned.items()) if t < term]',
           'out = [Attempt(k, t, "completed", "A") for k, t in sorted(planned.items()) if t <= term]')]),
    "C": ("required co-requisite ignored (prerequisite verdict only)", [(ENGINE,
          "        if a.status is PrereqStatus.SATISFIED:\n            return EligibilityState.SATISFIED_BY_HISTORY\n"
          "        if b.status is PrereqStatus.SATISFIED:",
          "        if a.prerequisite.status is PrereqStatus.SATISFIED:\n"
          "            return EligibilityState.SATISFIED_BY_HISTORY\n"
          "        if b.prerequisite.status is PrereqStatus.SATISFIED:")]),
    "C2": ("candidates batched as one proposal (the Phase 6.5 finding)", [(ENGINE,
           "as_of_term=term, independent=True)\n            b = check_proposal",
           "as_of_term=term)\n            b = check_proposal"), (ENGINE,
           "projected=hyp, as_of_term=term, independent=True)",
           "projected=hyp, as_of_term=term)")]),
    "D": ("double counting (a course credited to every requirement it is eligible for)", [(ENGINE,
          "                pc.requirements = [RequirementContribution(\n",
          "                pc.requirements = [RequirementContribution(requirement_code=c, "
          "requirement_name=c, requirement_system='major') for c in sorted(\n"
          "                    c for c, pool in self.pools.items() if pc.course in pool)] or "
          "[RequirementContribution(\n")]),
    "E": ("a course that adds nothing is selected (acceptance and pruning disabled)", [(ENGINE,
          "                if after <= score and not is_dep:\n                    continue",
          "                pass"), (ENGINE,
          "            if key in credited(final):\n                continue",
          "            continue")]),
    "F": ("historical offering treated as confirmed", [(OFFERINGS,
          "        if target_term in observed:",
          "        if target_term in observed or same:")]),
    "G": ("nondeterminism (no canonical tie-break, arbitrary candidate order)", [(ENGINE,
          "                    0 if states[key] is EligibilityState.SATISFIED_BY_HISTORY else 1, key)",
          "                    0 if states[key] is EligibilityState.SATISFIED_BY_HISTORY else 1)"),
          (ENGINE,
          "        order = sorted(ready | coreq_pending, key=rank)",
          "        import random\n        order = list(ready | coreq_pending)\n"
          "        random.shuffle(order)\n        order = sorted(order, key=rank)")]),
    "H": ("what-if writes to the student (projected attempts added to the session)", [(ENGINE,
          "        return tuple(rows)\n\n    def _audit",
          "        for sc, _ in rows:\n            self.session.add(sc)\n"
          "        return tuple(rows)\n\n    def _audit")]),
    "I": ("catalog years / program versions mixed (audits use the student's own version)",
          [(ENGINE,
            "        return self.audit_engine.audit(student, program_version=version,\n"
            "                                       projected=",
            "        return self.audit_engine.audit(student, program_version=None,\n"
            "                                       projected=")]),
    # Kept to record a NEUTRALISED fault: a candidate from another version's
    # pool is rejected because the Degree Engine (right version) credits it nowhere.
    "I0": ("candidate pools from every version (neutralised by engine acceptance)", [(ENGINE,
          "                .join(Course, Course.id == RequirementCourseOption.course_id)\n"
          "                .where(Requirement.program_version_id == version.id)).all():",
          "                .join(Course, Course.id == RequirementCourseOption.course_id)).all():")]),
    "J": ("future minimum-grade prerequisite marked satisfied", [(ENGINE,
          "        if b.status is PrereqStatus.SATISFIED:\n            return EligibilityState.CONDITIONAL_ON_PLAN",
          "        if b.status is PrereqStatus.SATISFIED:\n            return EligibilityState.SATISFIED_BY_HISTORY")]),
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
    for letter in letters:
        detected, detail = run(letter)
        print(f"{letter:3} {'DETECTED' if detected else 'MISSED  '} {FAULTS[letter][0]}\n"
              f"    {detail}", flush=True)


if __name__ == "__main__":
    main()
