"""Phase 7.2.2 failure injection: break academic intelligence on purpose.

Each fault makes one forbidden thing TRUE - a requirement allocated outside its
scope, unverified campus attendance or an ungraded course counted, a waiver
granted from unverified evidence, an unknown budget shown as a percentage, an
in-progress course counted as completed, a stale plan projected, a WebReg
document accepted when its layout is ambiguous, a section identity guessed, an
unresolved import confirmed, another student's import readable, a time-less
section treated as time-verified, missing delivery evidence called in-person,
a changed school rule set not re-reviewed - then runs the suites that should
notice. Files are restored whatever happens. DETECTED = a test failed.
MISSED is reported, never hidden.

Usage:  python scripts/inject_academic_failures.py [--check] [fault ...]
"""

from __future__ import annotations

# newline="" throughout: line endings survive byte-for-byte on Windows.

import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PY = sys.executable
CONTEXT = "backend/app/services/academic_context.py"
PROGRESS = "backend/app/services/progress.py"
WEBREG = "backend/app/domain/webreg.py"
IMPORTS = "backend/app/services/scheduling/imports.py"
ROUTES = "backend/app/api/v1/routes/schedule_imports.py"
MEETINGS = "backend/app/services/scheduling/meetings.py"
MODALITY = "backend/app/services/scheduling/modality.py"
LIFECYCLE = "backend/app/services/program_lifecycle.py"
TEST_DB = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://coursepilot:coursepilot@localhost:5432/coursepilot_test")

ACAD, PROG, SCHED = "academic", "progress", "schedule"

FAULTS = {
    "A": ("a requirement is allocated by its system, ignoring its allocation scope", ACAD, [(CONTEXT,
          "    return requirement.allocation_scope or requirement.requirement_system",
          "    return requirement.requirement_system")]),
    "B": ("unverified campus attendance satisfies a campus-bound requirement", ACAD, [(CONTEXT,
          '            return Tri.UNKNOWN, "Attendance at the required campus is unverified."',
          '            return Tri.YES, "Attendance at the required campus is unverified."')]),
    "C": ("a Pass/No-Credit course satisfies a graded-only requirement", ACAD, [(CONTEXT,
          '            return Tri.NO, "A graded credit-bearing course is required."',
          '            return Tri.YES, "A graded credit-bearing course is required."')]),
    "D": ("an unverified claim grants a waiver", ACAD, [(CONTEXT,
          "                not claim.verified\n                or claim.evidence_kind",
          "                False\n                or claim.evidence_kind")]),
    "E": ("a scope with an unknown budget is still shown as a fraction", PROG, [(PROGRESS,
          "    return completed.budget if completed.budget_known and current.budget_known else 0",
          "    return completed.budget")]),
    "F": ("in-progress courses are counted as completed", PROG, [(PROGRESS,
          '    baseline = engine.audit(student, statuses=frozenset({"completed"}))',
          '    baseline = engine.audit(student, statuses=frozenset({"completed", "in_progress"}))')]),
    "G": ("a stale saved plan is still projected", PROG, [(PROGRESS,
          "        if stale_reasons(session, student, saved):",
          "        if False:")]),
    "H": ("a WebReg document naming two terms is accepted", SCHED, [(WEBREG,
          "    if len(terms) != 1:\n        raise ScheduleParseError(\"SCHEDULE_TERM_AMBIGUOUS\")",
          "    if not terms:\n        raise ScheduleParseError(\"SCHEDULE_TERM_AMBIGUOUS\")")]),
    "I": ("conflicting index numbers for one section are tolerated", SCHED, [(WEBREG,
          "                    if seen[key] != index:",
          "                    if False:")]),
    # (The first J relaxed the identity count; the leftover-text check right
    # after it still refused the line - an equivalent mutant, reported MISSED.
    # This J removes that second check.)
    "J": ("course text beside an exact identity is ignored", SCHED, [(WEBREG,
          "                if re.search(COURSE, line[: match.start()] + line[match.end() :]):",
          "                if False:")]),
    "K": ("course identities outside a recognised region are silently dropped", SCHED, [(WEBREG,
          "        if len(identity_objects) > recognized:",
          "        if False:")]),
    "L": ("MULTIPLE_MATCHES is resolved by picking the first section", SCHED, [(IMPORTS,
          "    elif len(consistent) > 1:",
          "    elif False:")]),
    "M": ("an import with unresolved rows can be confirmed", SCHED, [(IMPORTS,
          '        and all(r["state"] in {"matched", "metadata_unavailable", "excluded"} for r in rows)',
          "        and True")]),
    "N": ("another student's import is readable", SCHED, [(IMPORTS,
          "        ScheduleImport.id == import_id, ScheduleImport.student_id == student_id",
          "        ScheduleImport.id == import_id")]),
    "O": ("a registration shows meetings of a section whose index no longer matches", SCHED, [(ROUTES,
          "                and section.index_number == record.index_number\n",
          "")]),
    "P": ("a section with no meetings counts as time-verified", SCHED, [(MEETINGS,
          "    return bool(meetings) and all(m.kind not in TIME_UNKNOWN_KINDS for m in meetings)",
          "    return all(m.kind not in TIME_UNKNOWN_KINDS for m in meetings)")]),
    "Q": ("missing delivery evidence is classified as in-person", SCHED, [(MODALITY,
          "        mode = Modality.UNKNOWN\n        reason = \"unrecognized_delivery_evidence\"",
          "        mode = Modality.IN_PERSON\n        reason = \"unrecognized_delivery_evidence\"")]),
    "R": ("a changed school rule set is not sent back for review", ACAD, [(LIFECYCLE,
          "    school_set.lifecycle_state = NEEDS_REREVIEW\n    return True",
          "    return True")]),
}

_PYTEST = ["-q", "-x", "-p", "no:cacheprovider"]
SUITES = {
    ACAD: [([PY, "-m", "pytest", "tests/test_core_ingestion.py", "tests/test_postgres_core.py",
             "tests/test_modeled_progress.py", *_PYTEST], "ingestion"),
           ([PY, "-m", "pytest", "tests/test_progress_contract.py", *_PYTEST], "backend")],
    PROG: [([PY, "-m", "pytest", "tests/test_progress_contract.py", "tests/test_progress_api.py",
             *_PYTEST], "backend"),
           ([PY, "-m", "pytest", "tests/test_modeled_progress.py", *_PYTEST], "ingestion")],
    SCHED: [([PY, "-m", "pytest", "tests/test_webreg.py", "tests/test_schedule_import_service.py",
              "tests/test_schedule_import_api.py", "tests/test_course_intelligence.py",
              *_PYTEST], "backend")],
}


def _apply(letter: str) -> dict:
    _, _, edits = FAULTS[letter]
    originals: dict[pathlib.Path, str] = {}
    for rel, old, new in edits:
        path = ROOT / rel
        originals.setdefault(path, path.read_text(encoding="utf-8", newline=""))
        current = path.read_text(encoding="utf-8", newline="")
        eol = "\r\n" if "\r\n" in current else "\n"
        old, new = old.replace("\n", eol), new.replace("\n", eol)
        if current.count(old) != 1:
            for p, text in originals.items():
                p.write_text(text, encoding="utf-8", newline="")
            raise LookupError(f"pattern not found exactly once in {rel}: {old[:60]!r}")
        path.write_text(current.replace(old, new), encoding="utf-8", newline="")
    return originals


def _git_clean(rels) -> None:
    """Refuse to inject into a file that already differs from git: a fault
    left behind by an earlier run would otherwise become the 'original'."""
    out = subprocess.run(["git", "diff", "--name-only", "--", *sorted(set(rels))], cwd=ROOT,
                         capture_output=True, text=True, check=False).stdout.strip()
    if out:
        raise RuntimeError(f"refusing to inject: working tree already modified: {out}")


def _restore(originals: dict) -> None:
    """Write the originals back AND read them back. On this machine the repo
    lives under OneDrive, whose sync once raced a restore and left an injected
    fault on disk (Phase 7.2.1 regression). Verify, retry, else stop the run."""
    import time

    for path, text in originals.items():
        for attempt in range(6):
            path.write_text(text, encoding="utf-8", newline="")
            if path.read_text(encoding="utf-8", newline="") == text:
                break
            time.sleep(0.5 * (attempt + 1))
        else:
            raise SystemExit(f"RESTORE FAILED for {path} - stop and run `git checkout -- {path}`")


def _run(cmd: list[str], cwd: pathlib.Path, env: dict) -> tuple[bool, str]:
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=env, check=False, timeout=1800)
    lines = (proc.stdout + proc.stderr).strip().splitlines()
    summary = next((ln for ln in reversed(lines) if "passed" in ln or "failed" in ln), lines[-1:] or [""])
    # pytest exit 4/5 = usage error / nothing collected: not a detection.
    if proc.returncode in (4, 5):
        raise RuntimeError(f"suite did not run in {cwd.name}: {summary}")
    return proc.returncode != 0, str(summary).strip()


def run(letter: str) -> tuple[bool, str]:
    _git_clean([rel for rel, _, _ in FAULTS[letter][-1]])
    try:
        originals = _apply(letter)
    except LookupError as exc:
        return False, f"INJECTION FAILED: {exc}"
    try:
        env = {**os.environ, "DATABASE_URL": TEST_DB, "DATABASE_URL_SYNC": TEST_DB,
               "TEST_DATABASE_URL": TEST_DB}
        details = []
        for cmd, cwd in SUITES[FAULTS[letter][1]]:
            try:
                failed, summary = _run(cmd, ROOT / cwd, env)
            except RuntimeError as exc:
                return False, f"INJECTION FAILED: {exc}"
            details.append(f"{cwd}: {summary}")
            if failed:
                return True, " | ".join(details)
        return False, " | ".join(details)
    finally:
        _restore(originals)


def main() -> None:
    args = sys.argv[1:]
    if args[:1] == ["--check"]:
        for letter in args[1:] or list(FAULTS):
            try:
                for path, text in _apply(letter).items():
                    path.write_text(text, encoding="utf-8", newline="")
                print(f"{letter:3} ok")
            except LookupError as exc:
                print(f"{letter:3} BROKEN {exc}")
        return
    for letter in args or list(FAULTS):
        detected, detail = run(letter)
        print(f"{letter:3} {'DETECTED' if detected else 'MISSED  '} {FAULTS[letter][0]}\n"
              f"    {detail}", flush=True)


if __name__ == "__main__":
    main()
