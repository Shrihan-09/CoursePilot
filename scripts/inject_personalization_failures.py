"""Phase 7.2.1 failure injection: break personalization and planning quality on purpose.

Each fault makes one of the brief's forbidden things TRUE - an ahead student
silently planned to finish early, the target ignored, the hard maximum
exceeded, UNKNOWN used as filler, an in-progress course treated as passed, a
current section guessed, an academic fact accepted as a "preference", the
planner fed something other than the confirmed profile, an unconfirmed history
marked ready, an exploratory program treated as planned, a returning student
planned into the past, preference staleness ignored, an observation turned
into a graduation claim - then runs the suites that should notice. Files are
restored whatever happens. DETECTED = a test failed. MISSED is reported.

Usage:  python scripts/inject_personalization_failures.py [--check] [fault ...]
"""

from __future__ import annotations

# newline="" throughout: line endings survive byte-for-byte on Windows.

import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PY = sys.executable
PROFILE = "backend/app/domain/planning_profile.py"
ENGINE = "backend/app/services/planning/engine.py"
READY = "backend/app/services/planning/readiness.py"
CALENDAR = "backend/app/services/planning/calendar.py"
SAVED = "backend/app/services/planning/saved.py"
PLANS = "backend/app/api/v1/routes/plans.py"
CURRENT = "backend/app/services/scheduling/current.py"
TEST_DB = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://coursepilot:coursepilot@localhost:5432/coursepilot_test")

API, ENG = "api", "engine"

FAULTS = {
    "A": ("an ahead student silently defaults to FINISH_EARLIER", API, [(PROFILE,
          "    pace: Pace = Pace.BALANCED\n", "    pace: Pace = Pace.FINISH_EARLIER\n")]),
    # (The first version only changed where a term stops; BALANCED's
    # closeness rule kept terms near the target anyway - an equivalent mutant,
    # reported MISSED. This version ignores the target outright.)
    "B": ("the target is ignored: terms fill to the maximum", ENG, [(ENGINE,
          "        stop_at, hard_cap = self._caps()",
          "        stop_at = hard_cap = c.max_credits_per_term"), (ENGINE,
          "            if c.pace is Pace.BALANCED and c.target_credits_per_term is not None and count:",
          "            if False:")]),
    "C": ("the hard maximum is exceeded", ENG, [(ENGINE,
          "                    credits + group_credits > hard_cap:",
          "                    credits + group_credits > hard_cap + 6:")]),
    "D": ("an UNKNOWN course is used to fill a term", ENG, [(ENGINE,
          """        ready = {k for k, s in states.items()
                 if s in (EligibilityState.SATISFIED_BY_HISTORY,
                          EligibilityState.CONDITIONAL_ON_PLAN)""",
          """        ready = {k for k, s in states.items()
                 if s in (EligibilityState.SATISFIED_BY_HISTORY,
                          EligibilityState.CONDITIONAL_ON_PLAN,
                          EligibilityState.NEEDS_CONFIRMATION)""")]),
    "E": ("an in-progress course is treated as completed", ENG, [(ENGINE,
          """                elif a.status == "in_progress":
                    self.in_progress[key] = a.term_code""",
          """                elif a.status == "in_progress":
                    self.passed.add(key)""")]),
    "F": ("a current section is guessed from the course code", API, [(CURRENT,
          "        for row in sorted(confirmations.get(sc.id, []), key=lambda r: r.component):",
          "        if cands and not confirmations.get(sc.id):\n"
          "            item.confirmed.append(_choice(cands[0], []))\n"
          "        for row in sorted(confirmations.get(sc.id, []), key=lambda r: r.component):")]),
    "G": ("a 'preference' can carry academic facts (extra fields accepted)", API, [(PROFILE,
          '''class PlanningProfileInput(BaseModel):
    """What a student confirms. Every field is a CoursePilot setting, not Rutgers policy."""

    model_config = ConfigDict(extra="forbid")''',
          '''class PlanningProfileInput(BaseModel):
    """What a student confirms. Every field is a CoursePilot setting, not Rutgers policy."""

    model_config = ConfigDict(extra="ignore")''')]),
    "H": ("the planner input is not the confirmed profile", API, [(PLANS,
          "                              constraints=to_constraints(profile_input(profile)))",
          "                              constraints=PlanningConstraints())")]),
    "I": ("an unconfirmed history is marked ready", API, [(READY,
          "    history_confirmed = student.onboarding_completed_at is not None and not status.open_imports",
          "    history_confirmed = True")]),
    "J": ("an exploratory program is presented as part of the plan", API, [(READY,
          '''            checks.append(Check("additional_goals", "info",
                                "Additional programs you're exploring are not included in the "
                                "plan: CoursePilot doesn't combine programs yet."))''',
          '''            checks.append(Check("additional_goals", "done",
                                "Your additional programs are included in your plan."))''')]),
    "K": ("a returning student is planned into past semesters", API, [(CALENDAR,
          "    floor = next_regular_after(current_term(on))",
          "    floor = \"00001\"")]),
    "L": ("a preference change does not mark the plan stale", API, [(SAVED,
          '            reasons.append("planning_preferences_changed")',
          "            pass")]),
    "M": ("earlier completion stated as a graduation date", ENG, [(ENGINE,
          '''                                 f"by {label(end)} instead of {label(last)}. This is not a "
                                 "graduation date, and your plan has not been changed: choose "''',
          '''                                 f"by {label(end)} instead of {label(last)}. You will "
                                 "graduate early, and your plan has not been changed: choose "''')]),
}

SUITES = {
    API: [([PY, "-m", "pytest", "tests/test_personalization_api.py", "tests/test_saved_plan_api.py",
            "-q", "-x", "-p", "no:cacheprovider"], "backend")],
    ENG: [([PY, "-m", "pytest", "tests/test_planning_pace.py", "tests/test_planning_engine.py",
            "-q", "-x", "-p", "no:cacheprovider"], "ingestion")],
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
