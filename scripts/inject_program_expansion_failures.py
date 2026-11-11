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
TESTS = ["ingestion/tests/test_program_expansion.py"]
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
