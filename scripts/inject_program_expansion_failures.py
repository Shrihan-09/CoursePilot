"""Failure injection for Prompt 08 B1: break each engine change, prove the tests notice.

Each fault is a source edit; the program-expansion tests run; the file is
restored byte for byte - whatever happens. A fault is DETECTED when at least
one test fails.

Usage:  python scripts/inject_program_expansion_failures.py [fault-letter ...]
(the report-API fault needs a *_test PostgreSQL database in DATABASE_URL_SYNC)
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PY = sys.executable
LF, CRLF = chr(10), chr(13) + chr(10)
CTX = "backend/app/services/academic_context.py"
ENGINE = "backend/app/services/planning/engine.py"
AUDIT = "backend/app/services/audit/engine.py"
KNOW = "backend/app/services/knowledge.py"
TOTAL = "backend/app/services/planning/degree_total.py"
VALIDATE = "ingestion/coursepilot_ingestion/encoding/validate.py"
COMPONENTS = "ingestion/coursepilot_ingestion/loaders/components.py"
REPORTS = "backend/app/api/v1/routes/requirement_reports.py"
ELIG = "backend/app/services/eligibility.py"
OFFER = "backend/app/services/planning/offerings.py"
PREREQ = "backend/app/services/prerequisites.py"
DEPS = "backend/app/services/planning/dependencies.py"
RULES = "backend/app/services/audit/rules.py"
SCEN = "backend/app/services/scenarios.py"
SERVICE = "backend/app/services/planning/service.py"
LIFE = "backend/app/services/program_lifecycle.py"
TESTS = ["ingestion/tests/test_program_expansion.py", "ingestion/tests/test_cs_intro_transition.py",
         "ingestion/tests/test_b1_followups.py"]
DB_TESTS = ["backend/tests/test_requirement_reports.py"]

FAULTS = {
    "A": ("linked components compose with every program of their school",
          [(CTX, '            SchoolRequirementSetVersion.composition == "school",\n', "")], TESTS),
    "B": ("a linked major component displaces the program's own major nodes",
          [(CTX, "        systems = {r.requirement_system for r in shared}",
            "        systems = {r.requirement_system for r in shared} | {'major'}")], TESTS),
    "C": ("an UNVERIFIED admission claim passes the stage",
          [(CTX, '            if c.verified and c.evidence_kind == "admission"',
            '            if c.evidence_kind == "admission"')], TESTS),
    "D": ("the plan assumes admission (no 'after admission' labels)",
          [(ENGINE, "                        - {None} - self.passed_stage)",
            "                        - {None} - set(self.node_stage.values()))")], TESTS),
    "E": ("an unknown class year is treated as governed by the curriculum",
          [(CTX, "    if not stated:\n        return None,", "    if not stated:\n        return True,")],
          TESTS),
    "F": ("a course below the per-course credit minimum counts",
          [(CTX, "        if credits < minimum:", "        if False:")], TESTS),
    "G": ("group caps are ignored (two 493/494 count)",
          [(AUDIT, '        if n > int(cap["max"]):', "        if False:")], TESTS),
    "H": ("the index calls unmapped courses 'free elective only' from incomplete coverage",
          [(KNOW, "        if cov.complete and not self.stale:", "        if True:")], TESTS),
    "I": ("the index unions certifications across terms (not term-scoped)",
          [(KNOW, "                        for cid in certified.get((term, code.casefold()), ()):",
            "                        for cid in set().union(*[v for (t, k), v in certified.items()"
            " if k == code.casefold()]):")], TESTS),
    "J": ("a missing degree total silently becomes SAS's 120",
          [(TOTAL, "    return degree_total(code, version.catalog_year)",
            '    return degree_total("SAS", version.catalog_year)')], TESTS),
    "K": ("a stale index build is served as fresh",
          [(KNOW, '    return build.manifest_sha256 == (m or manifest(session))["sha256"]',
            "    return True")], TESTS),
    "L": ("the validator accepts a quote that is not on the page",
          [(VALIDATE, '            if _norm(part).strip(" .;,") in text:', "            if True:")],
          TESTS),
    "M": ("an unevaluated school policy may be marked evaluable",
          [(COMPONENTS, '        if rule.get("rule_type") in UNEVALUATED_TYPES and '
                        'rule.get("is_evaluable", True):', "        if False:")], TESTS),
    "N": ("certification-code nodes get no eligibility",
          [(ELIG, "    for code_ in rule.get(\"certification_codes\", ()):",
            "    for code_ in ():")], TESTS),
    "P": ("a retired course is still offered after its last term",
          [(OFFER, "        if last is not None and target_term > last:", "        if False:")], TESTS),
    "Q": ("an announced course gets no offering evidence",
          [(OFFER, "        if row is not None and announced_in(row, target_term):",
            "        if False:")], TESTS),
    "R": ("122 does not satisfy prerequisites naming 112",
          [(PREREQ, "    if not stands_in or not any(k in history for k in stands_in):",
            "    if True:")], TESTS),
    "S": ("an unpublished announced course is treated as having no prerequisite",
          [(PREREQ, "    if not rows and assumed:", "    if False:")], TESTS),
    "T": ("the planner keeps planning a retired prerequisite (no substitution)",
          [(DEPS, "            if blocked and stand_ins:", "            if False:")], TESTS),
    "U": ("a completed 111 counts silently (no 'needs review' finding)",
          [(AUDIT, "                if a.course.course_string in (flag.get(\"applies_to\") or ()):",
            "                if False:")], TESTS),
    "V": ("CS120 is not recognised as free elective only",
          [(KNOW, "        if course in self._no_major:", "        if False:")], TESTS),
    "W": ("a class-scoped program silently picks a curriculum",
          [(SCEN, "        if class_year is None:" + LF + "            raise CurriculumClassYearRequired(",
            "        if class_year is None:" + LF + "            class_year = years[-1]" + LF
            + "        if False:" + LF + "            raise CurriculumClassYearRequired(")], TESTS),
    "X": ("a pre-business student's admission is assumed (program school's gen-ed applies)",
          [(CTX, "    if stage in passed_stages(evidence):" + LF + "        return program_school",
            "    if True:" + LF + "        return program_school")], TESTS),
    "Y": ("'counts for both' is guessed while the target lists are unknown",
          [(SERVICE, "            if not known:" + LF + "                pc.gen_ed_label",
            "            if False:" + LF + "                pc.gen_ed_label")], TESTS),
    "Z": ("a component can be published without an approved review",
          [(LIFE, "    if component.lifecycle_state != REVIEWED:", "    if False:"),
           (LIFE, "    review = latest_component_review(session, component)" + LF
            + "    if review is None or review.decision != \"approved\":",
            "    review = latest_component_review(session, component)" + LF + "    if False:")],
          TESTS),
    "O": ("the admin report list exposes the reporter's account id",
          [(REPORTS, "reporter=redact_id(str(r.account_id))", "reporter=str(r.account_id)")],
          DB_TESTS),
}


def run(letter: str) -> tuple[bool, str]:
    name, edits, tests = FAULTS[letter]
    originals: dict[pathlib.Path, bytes] = {}
    try:
        for rel, old, new in edits:
            path = ROOT / rel
            originals.setdefault(path, path.read_bytes())
            current = path.read_bytes().decode("utf-8")
            if CRLF in current:       # match the file's line endings (CRLF on Windows checkouts)
                old, new = old.replace(LF, CRLF), new.replace(LF, CRLF)
            if current.count(old) != 1:
                return False, f"INJECTION FAILED: pattern not found exactly once in {rel}"
            path.write_bytes(current.replace(old, new).encode("utf-8"))
        env = dict(os.environ)
        if tests is TESTS:      # SQLite tests: never point them at a server
            for k in ("DATABASE_URL", "DATABASE_URL_SYNC", "TEST_DATABASE_URL"):
                env.pop(k, None)
        base = os.environ.get("LOCALAPPDATA", "/tmp")
        proc = subprocess.run([PY, "-m", "pytest", *tests, "-q", "-p", "no:cacheprovider", "-rfE",
                               f"--basetemp={base}/Temp/cp-b1-inject"],
                              cwd=ROOT, capture_output=True, text=True, env=env)
        lines = proc.stdout.strip().splitlines()
        tail = lines[-1] if lines else proc.stderr[-200:]
        failed = sorted({ln.split("::")[-1].split(" ")[0] for ln in lines
                         if ln.startswith(("FAILED", "ERROR"))})
        return proc.returncode != 0 and "passed" in tail or bool(failed), \
            f"{tail} | {', '.join(failed[:6])}"
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
