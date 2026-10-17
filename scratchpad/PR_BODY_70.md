# Phase 7.0: Student Academic Profile & Onboarding Foundation

## Summary

A real student can now start using CoursePilot on their own, entirely through
the API:

```
welcome → choose program + catalog year → transcript import or manual entry
        → review → confirm → complete → dashboard (audit, plans, schedules)
```

No database access, admin API, hard-coded id or frontend rule logic is needed.

**The invariant the transcript path is built around:**

```
upload → extraction → normalization → validation → STAGING → student review
       → explicit confirmation → student_course → cache drop → engines
```

`upload → parser → student_course` never happens. A failure-injection fault that
tries it is caught.

**What is verified, and how:**

- **Locally implemented and tested:** everything in this PR, on SQLite and
  against the real test PostgreSQL database.
- **Real PostgreSQL concurrency:**
  - 8 concurrent first profile creates;
  - 6 concurrent confirmations;
  - a manual add racing a confirmation;
  - a review edit racing a confirmation;
  - 4 concurrent uploads of one document;
  - rollback when the 2nd insert of a confirmation fails;
  - a cache-invalidation failure;
  - the A→B→A cache race;
  - every new constraint.
- **Transcript layout: UNVERIFIED.** Every test document is SYNTHETIC. No real
  Rutgers unofficial transcript sample was available, and there is no public
  one. Imports report `layout_verified: false` and
  `parser_version = rutgers-nb-unofficial/1-unverified-layout`.

## Design (reconciled from six read-only investigations)

- **Self-service profile.**
  - `POST /student/profile {program_key, catalog_year}` creates the caller's
    *own* `student` row; the server sets `user_id` inside the INSERT.
  - `student.user_id UNIQUE` decides races.
  - Idempotent: 201, then 200 with `created: false` and the record unchanged.
  - Writes a `created` link event.
  - Linking an *existing* record is still admin-only.
  - The profile and program are created together because
    `program_version_id`/`catalog_year` are NOT NULL and about eight engine
    paths rely on that.
- **Program selection.**
  - Validated by the scenario resolver (now `resolve_version`): unknown
    program 404, catalog year it lacks 404 (never "the latest"), variant
    required 422, not evaluable 422.
  - Pending-review programs are accepted, with `requirements_pending_review` /
    `school_requirements_not_included` / `rules_not_evaluable` warnings.
  - `PUT /student/program` moves program and catalog year together; courses
    are untouched.
- **Manual history.**
  - `/student/courses` handles one `student_course` at a time.
  - Validation lives in `domain.history.validate_entry`, shared with transcript
    confirmation:
    - a Rutgers grade that `grades.outcome` recognizes;
    - no grade for planned, in-progress, transfer or exam rows, so a Rutgers
      grade is never fabricated;
    - `YYYYT` terms;
    - one catalog course per code;
    - unique (course, term).
  - Retakes are separate rows in different terms.
- **Transcript import.**
  - The PDF is the request **body**, so there is no file name to store, log or
    echo.
  - Size is capped while reading; `%PDF-` magic is required.
  - Stored under a generated key, then **pypdf runs in a killable child
    process** with a 20 s timeout and page/text caps. Encrypted, image-only and
    unreadable PDFs get fixed codes.
  - The raw document is deleted whatever happens.
  - Rows are staged with their *extracted* values kept, normalized against the
    catalog and the current record, and given a structured state
    (matched / needs_review / unrecognized / conflict / invalid) and issues.
- **Review.**
  - Rows can be corrected, excluded, or resolved (`keep_existing` /
    `replace_existing`).
  - Every edit bumps the import version.
- **Confirmation.**
  - One transaction: student row lock → import row lock → state, expiry and
    version checks → **re-normalize against the record as it is now** → write
    → mark confirmed → drop the cached audit.
  - Idempotent: a second confirmation writes nothing.
  - An import never deletes an entry, and overwrites one only through an
    explicit per-row `replace_existing`.
- **Transcript-reported GPA** is stored separately (`transcript_reported_gpa`).
  It is never overwritten by, and never presented as less authoritative than,
  the computed GPA.

## Correctness fixes

- **Graduate courses in eligibility queries.** `query_matches` admits only
  SOC level `U` unless a query opts in with `include_graduate`.
  - Courses named by a rule are never filtered.
  - Dev catalog: CS_ELECTIVES had **60 graduate rows; now 0**. Every other
    program is unchanged; their non-01 rows are named courses.
  - Reconciled on the dev DB: 60 rows deleted, 0 inserted, 490 unchanged.
  - The planner can no longer place them (tested).
- **Lifecycle warning on the student's own program.**
  - Every audit of a non-published version carries `program_pending_review`
    (WARNING).
  - The own-program scenario states `requirements_pending_review`.
- **Audit cache race.** The key is re-checked after the engine runs, so a write
  that commits mid-audit can no longer store (old key, new result) to be
  served after an undo.

## Schema (migration `548d1b0329ef`)

- New tables: `transcript_import` and `transcript_import_row` (staging; no
  engine reads them).
- New nullable columns: `student.onboarding_completed_at` and
  `student_course.transcript_import_id`.
- `student_link_event` CHECK gains `created`.
- Verification:
  - upgrade, downgrade and re-upgrade verified;
  - `alembic check` is clean;
  - the downgrade refuses, rather than delete audit history, if `created`
    events exist.

## Tests

| suite | result |
|---|---|
| `test_onboarding_domain.py` (validator, parser, real child-process extractor, hung/crashing parser, storage, streaming size cap) | 21 passed |
| `test_onboarding_api.py` (PG) | 13 passed |
| `test_transcript_import_api.py` (PG) | 15 passed |
| `test_onboarding_postgres.py` (PG concurrency and constraints) | 9 passed |
| `test_onboarding_correctness.py` (PG: graduate gate in audit and planner, lifecycle warning, fingerprint coverage) | 4 passed |
| `ingestion/tests/test_eligibility_reconciliation.py` | 4 passed |

**Failure injection** (`scripts/inject_onboarding_failures.py`, 35 faults):
34 DETECTED, 1 MISSED.

- **T, MISSED and benign by design.** Removing the app-level duplicate check
  changes nothing, because the UNIQUE constraint answers with the same
  `DUPLICATE_ENTRY`. T2 removes both layers and is DETECTED.
- **Two faults (AF, AG) first "timed out" because the laptop slept** (system
  log: resume at 20:30:46). Rerun awake, both were DETECTED in about 17 s.
- **Injection found one real gap.** Removing the streaming size cap went
  unnoticed, because the service's own check still returned 413 after
  buffering the whole body. A test now proves reading stops at the limit.

**Final regression** (HEAD `cf2c81a`; `*_test` PostgreSQL 16; full logs kept):

| suite | before (main `8689a88`) | after |
|---|---|---|
| root (PG) | 1765 passed, 5 skipped | **1828 passed, 5 skipped** |
| backend (PG) | 835 passed, 4 skipped, 1 error* | **898 passed, 4 skipped** |
| ingestion (PG) | 929 passed, 1 skipped | **930 passed, 1 skipped** |
| retrieval | 46 passed | **46 passed** |
| ingestion (SQLite) | 849 passed, 81 skipped | **850 passed, 81 skipped** |
| 6.8 injection (`inject_monitoring_failures.py`) | 16/16 | **16/16 DETECTED** |
| 6.9 injection (`inject_live_failures.py`) | 29/29 | **29/29 DETECTED** |

*A one-off fixture-setup ERROR in `test_multi_program.py::test_a_program_in_another_school_is_evaluated_with_the_boundary_stated` on **unchanged main** during the baseline. It passed in that same baseline's root run, passed 3/3 in isolation, and did not recur in the final run. The traceback was not captured; the final run kept full logs. Skips are pre-existing (environment-gated) and are not reported as passes.

## Limitations (see DATA_MODEL §46.14)

- **The real transcript layout is unverified.** One real, consented, redacted
  sample is the next step.
- **No OCR.** Scans and photos are refused.
- **Transfer credit with no Rutgers equivalent cannot be stored**, because
  `course_id` is NOT NULL. It stays reviewable and must be mapped or excluded.
- **Credit prefixes are read and shown, not stored.**
- **One program per student and one catalog year (2026-2027).** No double
  majors or minors.
- **Pre-existing taxonomy collision, left unchanged here.**
  `/student/scenarios` returns 409 for "variant required", which the middleware
  tags `unlinked_account`. The new onboarding routes use 422.
- **Extractor entry points.** Isolation costs about 2 s per upload on Windows
  (process start-up). A custom script that calls the extractor needs an
  `if __name__ == "__main__":` guard (spawn start method).

## Not done (non-goals)

- No Rutgers CAS, WebReg or Degree Navigator access.
- No Rutgers passwords.
- No LLM in transcript interpretation, requirements or eligibility.
- No lifecycle bypass.
- No frontend.
- No mobile app.
- No engine rebuild.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
