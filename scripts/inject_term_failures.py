"""Phase 7.2.4 failure injection: break current-term intelligence on purpose.

Each fault makes one forbidden thing TRUE - history decides a published term,
a published-but-unloaded term falls back to history, an unvalidated ingestion
is called ready, a failed request is read as "not published", validated data
is downgraded, a contradicted plan stays fresh, source disagreement is
dropped, strategy outranks a needed prerequisite, an invalid section is
ranked, unverified professor evidence is used, an ambiguous identity is
attached, thin feedback leaks a distribution, missing difficulty reads as
low, and the UI claims "Rutgers hasn't published" without a check - then
runs the suites that should notice. Files are restored whatever happens.
DETECTED = a test failed. MISSED is reported, never hidden.

Usage:  python scripts/inject_term_failures.py [--check] [fault ...]
"""

from __future__ import annotations

# newline="" throughout: line endings survive byte-for-byte on Windows.

import os
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PY = sys.executable
OFFERINGS = "backend/app/services/planning/offerings.py"
AUTHORITY = "backend/app/services/term_authority.py"
DISCOVERY = "ingestion/coursepilot_ingestion/term_discovery.py"
SAVED = "backend/app/services/planning/saved.py"
RANKING = "backend/app/services/planning/ranking.py"
SECTIONS = "backend/app/services/scheduling/section_ranking.py"
IDENTITY = "backend/app/services/instructor_identity.py"
FEEDBACK = "backend/app/services/course_feedback.py"
VOCAB = "frontend/src/lib/academic/vocabulary.ts"
TEST_DB = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://coursepilot:coursepilot@localhost:5432/coursepilot_test")

TERMS, INTEL, WEB = "terms", "intelligence", "web"

FAULTS = {
    "A": ("history decides a term whose data is authoritative", TERMS, [(OFFERINGS,
          "        if state.authority is TermAuthority.READY:",
          "        if False:")]),
    "B": ("a published-but-unloaded term falls back to history", TERMS, [(OFFERINGS,
          "        if state.authority is TermAuthority.PUBLISHED_PENDING:",
          "        if False:")]),
    "C": ("an unvalidated ingestion is treated as ready", TERMS, [(AUTHORITY,
          "        if row.ingestion_state == INGESTED and row.ingested_hash and row.section_count > 0:",
          "        if True:")]),
    "D": ("a failed request is recorded as 'not published'", TERMS, [(DISCOVERY,
          "        if row.publication_state == UNCHECKED:\n"
          "            row.publication_state = CHECK_FAILED",
          "        if True:\n"
          "            row.publication_state = NOT_PUBLISHED")]),
    "E": ("validated term data is downgraded by a later empty answer", TERMS, [(AUTHORITY,
          "        if verdict is not TermAuthority.READY and term_is_ready(states.get(row.term_code)):",
          "        if False:")]),
    "F": ("a plan contradicted by a published term is not marked stale", TERMS, [(SAVED,
          "    if K.NOT_OFFERED_IN_TERM in now:",
          "    if False:")]),
    "G": ("source disagreement is silently dropped", TERMS, [(DISCOVERY,
          "            row.disagreements = compare_sources(probe.courses, probe.open_indexes)",
          "            row.disagreements = {}")]),
    "H": ("a failed ingestion is still promoted to complete coverage", TERMS, [(DISCOVERY,
          "    if error:\n        return _failed(session, row, error, detail)",
          "    if False:\n        return _failed(session, row, error, detail)")]),
    "I": ("strategy outranks a prerequisite the plan needs", TERMS, [(RANKING,
          "        0 if evidence.required_dependency else 1,\n"
          "        strategy_key(evidence, strategy),",
          "        strategy_key(evidence, strategy),\n"
          "        0 if evidence.required_dependency else 1,")]),
    "J": ("subjective ranking ranks a structurally invalid section", INTEL, [(SECTIONS,
          "        if not c.structurally_valid:\n            continue",
          "        if False:\n            continue")]),
    "K": ("unverified professor evidence is used", INTEL, [(SECTIONS,
          "               -(c.professor_preference if verified else 0),",
          "               -c.professor_preference,")]),
    "L": ("an ambiguous instructor gets an external identity attached", INTEL, [(IDENTITY,
          "    if len(candidate_ids) > 1 or not is_specific(key):\n"
          "        return IdentityMatch.AMBIGUOUS, None\n"
          "    return IdentityMatch.UNVERIFIED, None",
          "    return IdentityMatch.VERIFIED, candidate_ids[0]")]),
    "M": ("missing difficulty is worded as low", INTEL, [(FEEDBACK,
          '        return "Not enough data"',
          '        return "Lower reported difficulty"')]),
    "N": ("thin feedback leaks its distribution", INTEL, [(FEEDBACK,
          "    state = AggregateState.AVAILABLE if n >= MIN_SAMPLE else AggregateState.NOT_ENOUGH_DATA",
          "    state = AggregateState.AVAILABLE if n >= 1 else AggregateState.NOT_ENOUGH_DATA")]),
    "O": ("the UI says 'Rutgers hasn't published' without a check", WEB, [(VOCAB,
          '  if (off.target_term_authority === "not_published") {',
          "  if (true) {")]),
}

_PYTEST = ["-q", "-x", "-p", "no:cacheprovider"]
_NPX = shutil.which("npx") or "npx"
SUITES = {
    TERMS: [([PY, "-m", "pytest", "tests/test_term_authority.py", "tests/test_planning_strategy.py",
              *_PYTEST], "ingestion")],
    INTEL: [([PY, "-m", "pytest", "tests/test_intelligence.py", *_PYTEST], "backend")],
    WEB: [([_NPX, "vitest", "run", "tests/unit/vocabulary.test.ts"], "frontend")],
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
