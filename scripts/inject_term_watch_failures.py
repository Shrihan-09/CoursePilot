"""Phase 7.3 failure injection: break the term watcher and section view on purpose.

Each fault makes one forbidden thing TRUE - an empty term read as a failed
fetch (or the reverse), completeness promoted without stability evidence or
without its time span, an unchanged payload reloaded, a not-due term fetched,
a structural change ignored, validation alone claiming completeness, the
"offered in N of M" fact inflated, the section view inventing a name, the
live worker running the watcher every tick - then runs the suites that should
notice. Files are restored whatever happens. DETECTED = a test failed.
MISSED is reported, never hidden.

Usage:  python scripts/inject_term_watch_failures.py [--check] [fault ...]
"""

from __future__ import annotations

# newline="" throughout: line endings survive byte-for-byte on Windows.

import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PY = sys.executable
WATCH = "ingestion/coursepilot_ingestion/term_watch.py"
DISCOVERY = "ingestion/coursepilot_ingestion/term_discovery.py"
WORKER = "ingestion/coursepilot_ingestion/live_worker.py"
OFFERINGS = "backend/app/services/planning/offerings.py"
VIEW = "backend/app/services/sources/sections.py"
TEST_DB = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://coursepilot:coursepilot@localhost:5432/coursepilot_test")

ING = "ingestion"

FAULTS = {
    "A": ("an empty (200 + []) term is classified as a failed fetch", ING, [(WATCH,
          "    return {PUBLISHED: WatchState.PUBLISHED, NOT_PUBLISHED: WatchState.EMPTY_UNPUBLISHED,",
          "    return {PUBLISHED: WatchState.PUBLISHED, NOT_PUBLISHED: WatchState.FETCH_FAILED,")]),
    "B": ("a failed fetch is classified as an unpublished term", ING, [(WATCH,
          "            CHECK_FAILED: WatchState.FETCH_FAILED}.get(row.publication_state,",
          "            CHECK_FAILED: WatchState.EMPTY_UNPUBLISHED}.get(row.publication_state,")]),
    "C": ("coverage is promoted with too few fetches", ING, [(WATCH,
          "    if len(recent) < STABLE_FETCHES:\n        return None",
          "    if not recent:\n        return None")]),
    "D": ("coverage is promoted without the time span", ING, [(WATCH,
          "    if len(counts) != 1 or last - first < STABLE_SPAN:",
          "    if len(counts) != 1:")]),
    "E": ("an unchanged payload is reloaded (not idempotent)", ING, [(WATCH,
          "        if row.ingestion_state == INGESTED and row.content_hash == previous_hash:",
          "        if False:")]),
    "F": ("a term that is not due is fetched anyway", ING, [(WATCH,
          "    if row is None or row.last_checked_at is None:\n        return True",
          "    if True:\n        return True")]),
    "G": ("a structural change does not block the load", ING, [(WATCH,
          "                if not report.ok:",
          "                if False:")]),
    "H": ("validation alone claims completeness (the old behaviour)", ING, [(DISCOVERY,
          '        source.coverage = row.coverage_state or "partial"',
          '        source.coverage = "complete"')]),
    "I": ("'offered in N of M' is inflated to every loaded term", ING, [(OFFERINGS,
          "                    same_season_offered=len(same), same_season_loaded=len(loaded),",
          "                    same_season_offered=len(loaded), same_season_loaded=len(loaded),")]),
    "J": ("the section view invents a name for a code with no public name", ING, [(VIEW,
          "                                   name=name, name_source=source))",
          '                                   name=name or f"Program {r.code}",\n'
          '                                   name_source=source or "soc_subject_description"))')]),
    "K": ("the live worker runs the watcher on every tick", ING, [(WORKER,
          "                or now - self._term_watch_last >= self.term_watch_every):",
          "                or True):")]),
}

_PYTEST = ["-q", "-x", "-p", "no:cacheprovider"]
SUITES = {
    ING: [([PY, "-m", "pytest", "tests/test_term_watch.py", "tests/test_upcoming_terms.py",
            "tests/test_term_authority.py", *_PYTEST], "ingestion")],
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
