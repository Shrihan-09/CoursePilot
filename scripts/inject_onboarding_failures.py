"""Phase 7.0 failure injection: break onboarding on purpose, prove the tests notice.

Each fault is a source edit; the onboarding suites run - pure domain and
extraction, real-PostgreSQL API, real-PostgreSQL concurrency, correctness,
and the ingestion eligibility tests - against the dedicated `*_test`
database; the file is restored whatever happens. DETECTED = at least one test
failed (`-x`). A fault with no observable effect is reported as MISSED, never
claimed as caught.

Usage:  python scripts/inject_onboarding_failures.py [--check] [fault ...]
        --check only verifies every pattern still matches exactly once.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PY = sys.executable
IMPORTS = "backend/app/services/transcripts/imports.py"
EXTRACT = "backend/app/services/transcripts/extract.py"
WORKER = "backend/app/services/transcripts/pdf_worker.py"
ONBOARD = "backend/app/services/onboarding.py"
HISTORY = "backend/app/services/history.py"
DOMAIN = "backend/app/domain/history.py"
PARSER = "backend/app/domain/transcript.py"
ROUTES = "backend/app/api/v1/routes/onboarding.py"
CACHED = "backend/app/services/audit/cached_audit.py"
ENGINE = "backend/app/services/audit/engine.py"
SCEN = "backend/app/services/scenarios.py"
ELIG = "backend/app/services/eligibility.py"
TESTS = ["backend/tests/test_onboarding_domain.py", "backend/tests/test_onboarding_api.py",
         "backend/tests/test_transcript_import_api.py",
         "backend/tests/test_onboarding_postgres.py",
         "backend/tests/test_onboarding_correctness.py",
         "ingestion/tests/test_eligibility_reconciliation.py"]
TEST_DB = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://coursepilot:coursepilot@localhost:5432/coursepilot_test")

FAULTS = {
    # --- the core invariant -------------------------------------------------
    "A": ("staging bypassed: upload writes extracted rows straight to history", [(IMPORTS,
          "    imp.state = ImportState.EXTRACTED\n    imp.extracted_at = now\n",
          ("    for r in imp.rows:\n"
           "        if r.course_id and r.term_code and r.status:\n"
           "            session.add(StudentCourse(student_id=student_id, course_id=r.course_id,\n"
           "                                      term_code=r.term_code, status=r.status,\n"
           "                                      grade=r.grade))\n"
           "    imp.state = ImportState.EXTRACTED\n    imp.extracted_at = now\n"))]),
    "B": ("blocking rows do not block confirmation", [(IMPORTS,
          "    if blocking or before != after:",
          "    if before != after:")]),
    "C": ("confirmation not idempotent (a confirmed import is confirmed again)", [(IMPORTS,
          "    if imp.state == ImportState.CONFIRMED:\n        return ConfirmResult(imp, True)",
          "    if False:\n        return ConfirmResult(imp, True)")]),
    "D": ("confirmation without row locks (student and import)", [(IMPORTS,
          ("    student = lock_student(session, account_id)\n"
           "    if student is None:\n"
           "        raise ProfileMissing(\"No academic record exists for this account.\")\n"
           "    imp = get_import(session, student.id, import_id, lock=True)\n"
           "    if imp.state == ImportState.CONFIRMED:"),
          ("    from app.services.onboarding import owned_student\n"
           "    student = owned_student(session, account_id)\n"
           "    if student is None:\n"
           "        raise ProfileMissing(\"No academic record exists for this account.\")\n"
           "    imp = get_import(session, student.id, import_id, lock=False)\n"
           "    if imp.state == ImportState.CONFIRMED:"))]),
    "E": ("stale review version accepted at confirmation", [(IMPORTS,
          ("    if imp.version != expected_version:\n"
           "        raise ImportRefused(\"IMPORT_VERSION_STALE\",\n"
           "                            \"This import changed since you reviewed it; review it again.\")"),
          "    pass")]),
    "F": ("no re-validation against the CURRENT record at confirmation", [(IMPORTS,
          ("    before = [(r.id, r.state, r.resolution, r.existing_record_id) for r in imp.rows]\n"
           "    renormalize(session, imp)\n"),
          "    before = [(r.id, r.state, r.resolution, r.existing_record_id) for r in imp.rows]\n")]),
    "G": ("expired staged import still confirmable", [(IMPORTS,
          "    if imp.state == ImportState.EXTRACTED and imp.expires_at is not None \\\n",
          "    if False and imp.expires_at is not None \\\n")]),
    # --- ownership ------------------------------------------------------------
    "H": ("imports reachable across accounts (not owner-scoped)", [(IMPORTS,
          ("    q = select(TranscriptImport).where(TranscriptImport.id == import_id,\n"
           "                                       TranscriptImport.student_id == student_id)"),
          "    q = select(TranscriptImport).where(TranscriptImport.id == import_id)")]),
    "I": ("history entries editable across accounts", [(HISTORY,
          ("    row = session.scalar(select(StudentCourse).where(StudentCourse.id == entry_id,\n"
           "                                                     StudentCourse.student_id == student.id))\n"
           "    if row is None:\n        raise EntryNotFound(\"No such entry.\")"),
          ("    row = session.get(StudentCourse, entry_id)\n"
           "    if row is None:\n        raise EntryNotFound(\"No such entry.\")"))]),
    "J": ("a staged row id from another import accepted", [(IMPORTS,
          "    row = next((r for r in imp.rows if r.id == row_id), None)",
          "    row = session.get(TranscriptImportRow, row_id)")]),
    "K": ("concurrent first profile creation not resolved (constraint loser errors)", [(ONBOARD,
          ("    except IntegrityError:\n"
           "        # The concurrent first create won. Its row is THE record.\n"
           "        session.rollback()"),
          ("    except IntegrityError:\n"
           "        raise\n"
           "        session.rollback()"))]),
    "L": ("a retried profile create re-binds the program", [(ONBOARD,
          "    existing = owned_student(session, account_id)\n    if existing is not None:\n"
          "        return existing, False",
          ("    existing = owned_student(session, account_id)\n    if existing is not None:\n"
           "        v = resolve_selection(session, program_key, catalog_year)\n"
           "        existing.program_version_id, existing.catalog_year = v.id, v.catalog_year\n"
           "        return existing, False"))]),
    # --- academic truth ---------------------------------------------------------
    "M": ("a missing status is guessed (no grade -> in progress)", [(IMPORTS,
          "        row.status = \"completed\" if extracted.grade else None",
          "        row.status = \"completed\" if extracted.grade else \"in_progress\"")]),
    "N": ("transfer/AP grade carried over as a Rutgers grade", [(IMPORTS,
          ("        row.grade = None\n"
           "        row.status = \"completed\"\n"),
          ("        row.grade = extracted.grade\n"
           "        row.status = \"completed\"\n"))]),
    "O": ("an unrecognized grade symbol is accepted", [(DOMAIN,
          "        elif outcome(stat, grade, origin).kind is OutcomeKind.UNRECOGNIZED:",
          "        elif False:")]),
    "P": ("an unrecognized course row silently dropped", [(IMPORTS,
          "    for extracted in parsed.rows:\n        row = TranscriptImportRow(",
          ("    for extracted in parsed.rows:\n"
           "        if not session.scalar(select(Course.id).where(\n"
           "                Course.course_string == extracted.course_code).limit(1)):\n"
           "            continue\n"
           "        row = TranscriptImportRow("))]),
    "Q": ("a conflict with recorded history overwrites it without a resolution", [(IMPORTS,
          "        if existing is not None and row.resolution == Resolution.REPLACE_EXISTING:",
          "        if existing is not None and row.resolution != Resolution.KEEP_EXISTING:"),
          (IMPORTS,
           ("            if _same(existing, entry, match.course.credits):"),
           ("            if True:"))]),
    "R": ("same course twice in one document not flagged", [(IMPORTS,
          "    _mark_in_import_duplicates(list(imp.rows))\n    imp.state = ImportState.EXTRACTED",
          "    imp.state = ImportState.EXTRACTED")]),
    "S": ("a line with two course codes is not flagged", [(PARSER,
          "            if len(codes) > 1:\n                problems.append(\"MULTIPLE_COURSE_CODES\")",
          "            if False:\n                problems.append(\"MULTIPLE_COURSE_CODES\")")]),
    "T": ("manual entry: same (course, term) duplicate not refused", [(HISTORY,
          ("    if _duplicate(session, student.id, match.course.id, entry.term_code) is not None:\n"
           "        raise EntryInvalid([_duplicate_issue()])\n"
           "    count ="),
          "    count =")]),
    # T is expected to be MISSED: the (student, course, term) UNIQUE constraint
    # is the real guard and returns the same DUPLICATE_ENTRY. T2 removes the
    # mapping of that constraint too, proving the layering is what holds.
    "T2": ("manual duplicate: app check removed AND constraint error unmapped", [(HISTORY,
           ("    if _duplicate(session, student.id, match.course.id, entry.term_code) is not None:\n"
            "        raise EntryInvalid([_duplicate_issue()])\n"
            "    count ="),
           "    count ="), (HISTORY,
           ("    session.add(row)\n    try:\n        with session.begin_nested():\n"
            "            session.flush()\n    except IntegrityError:"),
           ("    session.add(row)\n    try:\n        with session.begin_nested():\n"
            "            session.flush()\n    except ZeroDivisionError:"))]),
    # --- hostile input ----------------------------------------------------------
    "U": ("file type trusted without magic bytes", [(EXTRACT,
          "    if PDF_MAGIC not in data[:1024]:\n        raise ExtractionFailed(\"FILE_NOT_PDF\")",
          "    pass")]),
    "V": ("upload size not enforced while reading", [(ROUTES,
          "        if len(buf) > limit:\n            raise ExtractionFailed(\"FILE_TOO_LARGE\")",
          "        pass"), (ROUTES,
          "    if declared is not None and declared.isdigit() and int(declared) > limit:",
          "    if False:")]),
    "W": ("encrypted PDFs not refused", [(WORKER,
          "    if reader.is_encrypted:\n",
          "    if False:\n")]),
    "X": ("a hung parser is not killed (no timeout)", [(EXTRACT,
          "            if not parent.poll(timeout_seconds):",
          "            if not parent.poll(None):")]),
    "Y": ("the raw document is retained after extraction", [(IMPORTS,
          "    try:\n        storage.delete(key)\n",
          "    try:\n        pass\n")]),
    # --- privacy ----------------------------------------------------------------
    "Z": ("transcript text written to the logs", [(IMPORTS,
          "    for extracted in parsed.rows:\n        row = TranscriptImportRow(",
          ("    for extracted in parsed.rows:\n"
           "        logger.info(\"row %s\", extracted.raw_text)\n"
           "        row = TranscriptImportRow("))]),
    "AA": ("parser exception text kept (it can quote the document)", [(IMPORTS,
           "        failure = \"PDF_EXTRACTION_CRASHED\"",
           ("        failure = \"PDF_EXTRACTION_CRASHED\"\n"
            "        logger.error(\"transcript_extraction_detail\", extra={\"detail\": str(_exc)})"))
           , (IMPORTS, "    except Exception:\n        # Never the exception's text",
              "    except Exception as _exc:\n        # Never the exception's text")]),
    # --- derived state / correctness -------------------------------------------
    "AB": ("cache key not re-checked after the engine (A -> B -> A stale hit)", [(CACHED,
           "    if _key_still_holds(session, student, key):",
           "    if True:")]),
    "AC": ("a cache-invalidation failure rolls back the academic write", [(ONBOARD,
           ("    except Exception:\n"
            "        logger.warning(\"onboarding_cache_invalidate_failed\", exc_info=True)"),
           ("    except Exception:\n"
            "        session.rollback()"))]),
    "AD": ("graduate courses admitted by eligibility queries", [(ELIG,
           ("    if not query.get(\"include_graduate\", False) and course.level not in (None, \"U\"):\n"
            "        return False\n"),
           "")]),
    "AE": ("no review warning on an audit of a non-published program", [(ENGINE,
           "        if version.lifecycle_state != \"published\":\n            findings.append(",
           "        if False:\n            findings.append(")]),
    "AF": ("own-program scenario omits the pending-review assumption", [(SCEN,
           ("    if target.support_status == SupportStatus.PENDING_REVIEW.value:\n"
            "        out.append(\n"
            "            ScenarioAssumption(\n"
            "                code=\"requirements_pending_review\","),
           ("    if target.support_status == SupportStatus.PENDING_REVIEW.value \\\n"
            "            and not target.is_current_program:\n"
            "        out.append(\n"
            "            ScenarioAssumption(\n"
            "                code=\"requirements_pending_review\","))]),
    "AG": ("a pending-review program selected without its warnings", [(ONBOARD,
           "    if info.support_status is SupportStatus.PENDING_REVIEW:",
           "    if False:")]),
    "AH": ("an unavailable catalog year silently replaced by the latest", [(SCEN,
           "    version = next((v for v in program.versions if v.catalog_year == wanted), None)",
           ("    version = next((v for v in program.versions if v.catalog_year == wanted), None) \\\n"
            "        or max(program.versions, key=lambda v: v.catalog_year)"))]),
}


def _apply(letter: str) -> dict:
    _, edits = FAULTS[letter]
    originals: dict[pathlib.Path, str] = {}
    for rel, old, new in edits:
        path = ROOT / rel
        originals.setdefault(path, path.read_text(encoding="utf-8"))
        current = path.read_text(encoding="utf-8")
        if current.count(old) != 1:
            for p, text in originals.items():
                p.write_text(text, encoding="utf-8")
            raise LookupError(f"pattern not found exactly once in {rel}: {old[:60]!r}")
        path.write_text(current.replace(old, new), encoding="utf-8")
    return originals


def run(letter: str) -> tuple[bool, str]:
    try:
        originals = _apply(letter)
    except LookupError as exc:
        return False, f"INJECTION FAILED: {exc}"
    try:
        env = {**os.environ, "DATABASE_URL": TEST_DB, "DATABASE_URL_SYNC": TEST_DB,
               "TEST_DATABASE_URL": TEST_DB}
        proc = subprocess.run([PY, "-m", "pytest", *TESTS, "-q", "-x", "-p", "no:cacheprovider",
                               "-rfE"], cwd=ROOT, capture_output=True, text=True, env=env,
                              check=False, timeout=1800)
        lines = proc.stdout.strip().splitlines()
        tail = lines[-1] if lines else proc.stderr[-200:]
        failed = sorted({ln.split("::")[-1].split(" ")[0] for ln in lines
                         if ln.startswith(("FAILED", "ERROR"))})
        return proc.returncode != 0, f"{tail} | {', '.join(failed[:6])}"
    except subprocess.TimeoutExpired:
        return True, "TIMEOUT (a hang is a detected failure; the run was stopped)"
    finally:
        for path, text in originals.items():
            path.write_text(text, encoding="utf-8")


def main() -> None:
    args = sys.argv[1:]
    if args[:1] == ["--check"]:
        for letter in args[1:] or list(FAULTS):
            try:
                for path, text in _apply(letter).items():
                    path.write_text(text, encoding="utf-8")
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
