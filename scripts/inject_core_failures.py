"""Phase 7.2 failure injection: break the core student experience on purpose.

Each fault makes one of the brief's forbidden things TRUE - the frontend shows
UNKNOWN as eligible, hides a pending-review warning, presents a stale plan as
current, claims a registration, draws an untimed meeting; the API lets one
account read another's plan or ignores what changed; the Schedule Engine
allows a conflict or drops a linked component - then runs the suites that
should notice. The file is restored whatever happens. DETECTED = a test
failed. MISSED is reported, never hidden.

Usage:  python scripts/inject_core_failures.py [--check] [fault ...]
"""

from __future__ import annotations

# newline="" throughout: line endings survive byte-for-byte on Windows.

import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PY = sys.executable
VOCAB = "frontend/src/lib/academic/vocabulary.ts"
TIME = "frontend/src/lib/academic/schedule-time.ts"
DEGREE_VIEW = "frontend/src/features/degree/degree-view.tsx"
WHAT_IF = "frontend/src/features/degree/what-if.tsx"
PLAN_RESULT = "frontend/src/features/plan/plan-result.tsx"
OPTION_VIEW = "frontend/src/features/schedule/option-view.tsx"
SAVED = "backend/app/services/planning/saved.py"
PLANS_ROUTE = "backend/app/api/v1/routes/plans.py"
MEETINGS = "backend/app/services/scheduling/meetings.py"
CANDIDATES = "backend/app/services/scheduling/candidates.py"
TEST_DB = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://coursepilot:coursepilot@localhost:5432/coursepilot_test")

WEB, API, ENGINE = "web", "api", "engine"

FAULTS = {
    # ---- frontend: what the student is told -------------------------------
    "A": ("UNKNOWN prerequisite presented as met", WEB, [(VOCAB,
          'label: "Needs confirmation", tone: "warning", icon: "unknown",\n'
          '    meaning: "CoursePilot cannot evaluate this prerequisite',
          'label: "Prerequisites met", tone: "success", icon: "complete",\n'
          '    meaning: "CoursePilot cannot evaluate this prerequisite')]),
    "B": ("UNKNOWN requirement presented as failed", WEB, [(VOCAB,
          'label: "Can\'t determine", tone: "warning", icon: "unknown",',
          'label: "Can\'t determine", tone: "danger", icon: "failed",')]),
    "C": ("pending-review warning hidden when only the lifecycle says so", WEB, [(DEGREE_VIEW,
          '    program.support_status !== "supported" ||\n',
          "")]),
    "D": ("stale plan presented as current", WEB, [(PLAN_RESULT,
          'if (plan.freshness.state === "current") return null;',
          "return null;")]),
    "E": ("schedule claims CoursePilot registered the student", WEB, [(OPTION_VIEW,
          "CoursePilot doesn&apos;t register you, and nothing here adds or drops a course. Enter these index\n"
          "        numbers in Rutgers WebReg when your registration window opens.",
          "You're registered for these sections.")]),
    "F": ("an untimed (TBA) meeting drawn on the calendar", WEB, [(TIME,
          "      if (isTimed(meeting)) {",
          '      if (isTimed(meeting) || meeting.kind === "tba") {')]),
    "G": ("what-if no longer labelled hypothetical", WEB, [(WHAT_IF,
          "Hypothetical. Your program is not changed, and nothing is saved.",
          "Your results.")]),
    "H": ("stale availability shown as 'open as of' (current)", WEB, [(VOCAB,
          '  if (a.freshness === "current") {',
          '  if (a.freshness !== "unobserved") {')]),
    "I": ("frontend fabricates a status: UNKNOWN tallied as complete", WEB, [(
          "frontend/src/features/degree/requirement-tree.tsx",
          "    if (n.children.length === 0) out[n.status] += 1;",
          '    if (n.children.length === 0) out[n.status === "indeterminate" ? "satisfied" : n.status] += 1;')]),
    # ---- API: ownership and freshness ------------------------------------
    "J": ("another account's plan is readable (ownership dropped)", API, [(SAVED,
          "def current_plan(session: Session, student: Student) -> SavedPlan | None:\n"
          "    row = session.scalar(select(StudentPlan).where(StudentPlan.student_id == student.id))",
          "def current_plan(session: Session, student: Student) -> SavedPlan | None:\n"
          "    row = session.scalar(select(StudentPlan).order_by(StudentPlan.updated_at.desc()).limit(1))")]),
    "K": ("freshness never reported (stale plan presented as current)", API, [(SAVED,
          '        reasons.append("start_term_passed")\n    return reasons',
          '        reasons.append("start_term_passed")\n    return []')]),
    "L": ("academic record changes not detected", API, [(SAVED,
          "    elif academic_fingerprint(session, student) != row.academic_fingerprint:",
          "    elif False:")]),
    "M": ("save body tolerates extra fields (program, status, a client plan)", API, [(PLANS_ROUTE,
          '    constraints: PlanningConstraints = Field(default_factory=PlanningConstraints)\n\n'
          '    model_config = {"extra": "forbid"}\n\n\ndef _saved_view',
          '    constraints: PlanningConstraints = Field(default_factory=PlanningConstraints)\n\n'
          '    model_config = {"extra": "ignore"}\n\n\ndef _saved_view')]),
    "N": ("blocked course loses the original Rutgers condition", API, [(SAVED,
          "            item.prerequisite_text = _plain(raw)",
          "            item.prerequisite_text = None")]),
    # ---- Schedule Engine: validity ---------------------------------------
    "O": ("schedule allows overlapping meetings", ENGINE, [(MEETINGS,
          "    return a.start_minute < b.end_minute + buffer and b.start_minute < a.end_minute + buffer",
          "    return False")]),
    "P": ("required linked component (lab/recitation) dropped", ENGINE, [(CANDIDATES,
          '                slots.append((sup, "required_companion", evidence))\n',
          "                pass\n")]),
}

SUITES = {
    WEB: [(["npx", "vitest", "run"], "frontend")],
    API: [([PY, "-m", "pytest", "tests/test_saved_plan_api.py", "-q", "-x", "-p", "no:cacheprovider"], "backend")],
    ENGINE: [([PY, "-m", "pytest", "tests/test_schedule_domain.py",
               "tests/test_schedule_api.py", "-q", "-x", "-p", "no:cacheprovider"], "backend"),
             ([PY, "-m", "pytest", "tests/test_linked_components.py", "tests/test_schedule_engine.py",
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


def _run(cmd: list[str], cwd: pathlib.Path, env: dict) -> tuple[bool, str]:
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=env, check=False,
                          timeout=1800, shell=os.name == "nt" and cmd[0] == "npx")
    lines = (proc.stdout + proc.stderr).strip().splitlines()
    summary = next((ln for ln in reversed(lines) if "passed" in ln or "failed" in ln), lines[-1:] or [""])
    # pytest exit 4/5 = usage error / nothing collected: the suite did not
    # run, so it cannot have detected anything.
    if cmd[0] != "npx" and proc.returncode in (4, 5):
        raise RuntimeError(f"suite did not run in {cwd.name}: {summary}")
    return proc.returncode != 0, str(summary).strip()


def run(letter: str) -> tuple[bool, str]:
    try:
        originals = _apply(letter)
    except LookupError as exc:
        return False, f"INJECTION FAILED: {exc}"
    try:
        env = {**os.environ, "DATABASE_URL": TEST_DB, "DATABASE_URL_SYNC": TEST_DB,
               "TEST_DATABASE_URL": TEST_DB}
        detected, details = False, []
        for cmd, cwd in SUITES[FAULTS[letter][1]]:
            try:
                failed, summary = _run(cmd, ROOT / cwd, env)
            except RuntimeError as exc:
                return False, f"INJECTION FAILED: {exc}"
            details.append(f"{cwd}: {summary}")
            if failed:
                detected = True
                break
        return detected, " | ".join(details)
    finally:
        for path, text in originals.items():
            path.write_text(text, encoding="utf-8", newline="")


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
