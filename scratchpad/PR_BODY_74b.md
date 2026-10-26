# Phase 7.4 part 2: WebReg schedule import - Prompt 04 gaps

Branch `feature/phase-7-4-webreg-schedule-import` → `main`. The WebReg importer was built in Phase 7.2.2. This PR checks Prompt 04 item by item against it and builds **only what was missing**.

## Checklist: Prompt 04 vs the code

| Requirement | Status | Where |
|---|---|---|
| Pipeline upload → extract → normalize → validate → staging → review → confirm | Done (7.2.2) | `services/scheduling/imports.py`, `domain/webreg.py`, `services/scheduling/pdf_worker.py` |
| Text-layer PDFs only | Done (7.2.2) | image-only and outlined glyphs refused; UI explains Chrome "Save as PDF" |
| Reading-order problems detected | Done (7.2.2) | unexplained course text, conflicting ids and two terms are each refused, never dropped |
| New tables `schedule_import` + `schedule_import_row`, originals kept | **Partial** | `schedule_import` exists. Rows are a JSON column holding each row's `extracted` originals, not a separate table. See "decide" |
| Match by (term, index) in the combined view | Done (7.2.2) | SOC is the combined view (CSP is out) |
| Course string and section must agree, else `conflict` | Done (7.2.2) | `IDENTIFIERS_DISAGREE` |
| No section → `unrecognized`, never retargeted | Done (named `metadata_unavailable`) | never retargeted; **now** writes only its identity, no history or section |
| Index match pre-selects the section; written only on confirm | Done (7.2.2) + **shown** | the review now shows "Pre-selected: section, index" and exactly what confirming will do (`history_effect`, `section_effect`) |
| Confirmation creates/updates in-progress `student_course` rows | **Built** | `confirm` → `_write_history`. Missing → added; planned → in progress; in progress → skipped; completed → conflict (`RECORD_SAYS_COMPLETED`, resolved with `keep_record`) |
| Confirmation creates current-section rows | **Built** | `_write_section` → `student_current_section` (`source=webreg_schedule_import`). A different confirmed index needs `replace_existing` |
| …in one transaction; in-progress course skipped, not duplicated | **Built** | one caller transaction; atomicity test |
| Dropped courses flagged "not on your latest WebReg schedule — dropped?", never deleted | **Built** | `possibly_dropped`, `on_latest_import` (registrations), `not_on_latest_webreg` (My current schedule) |
| Endpoints: upload, staged rows, resolve, confirm | Done (7.2.2) at `/api/v1/student/schedule-imports` | path differs from the prompt's `/api/v1/schedule-imports`; `keep_record` added to resolve |
| Schedule page: import step with export instructions | **Built** (instructions) | `WebRegExportSteps` |
| Review table with the pre-selected section | **Built** (effects shown) | `rowEffectText`, keep-record control, outcomes after confirm |
| Confirmed schedule in the week view | Done (7.2.2) | `ImportedWeek` |
| Tests: synthetic fixtures | Done | |
| Tests: index-reuse conflict | **Built** (DB) | `test_schedule_import_record.py` |
| Tests: missing section | **Built** (DB, plus 7.2.2 unit) | |
| Tests: idempotent re-import | **Built** (DB, plus 7.2.2 same-bytes test) | |
| Tests: dropped-course detection | **Built** | |
| Tests: atomic confirmation | **Built** | |
| `scripts/inject_schedule_import_failures.py` | **Built** | six faults; pinned by a test |

## What changed

- **`services/scheduling/imports.py`.** Each row carries its record effects. `confirm` writes history, current sections and registrations in one transaction, with a per-row `outcome`. New helpers `possibly_dropped` and `latest_import_courses`.
- **`SourceKind.STUDENT_UPLOADED_SCHEDULE`** (`student_uploaded_schedule`) marks history written from a WebReg import.
- **API.** `keep_record` on row edit; `possibly_dropped` on staged imports; `on_latest_import` on registrations; `not_on_latest_webreg` on the current schedule.
- **UI.** Export steps, pre-selected section and effects, keep-record control, dropped list, dropped flags on registrations and My current schedule.
- **Docs.** DATA_MODEL §52; PHASE_7_3_PLAN §11. This reverses 7.2.2's "a schedule import never writes academic history", as Prompt 04 requires.
- **No migration.**

## Tests

- **New backend tests:**
  - `test_schedule_import_record.py`, 8 tests (PostgreSQL): writes history + sections + registrations; re-import writes nothing new; reused index → conflict, never retargeted; missing section → identity only; completed record → conflict → keep; different confirmed section → explicit replace; dropped course flagged, never deleted; atomic rollback.
  - One injection test pinning all eight outcomes.
- **New frontend tests:** 4 (effects text, completed-conflict wording, dropped list, export steps).
- **Full suites:** backend 1,119 passed / 4 skipped; frontend 97 passed, `tsc` clean; ingestion 1,057 passed / 1 skipped.
- **Also fixed:** an order-dependent check in the 7.4 DN test (`test_confirm_writes_history_and_status_and_is_idempotent`). It read every student's history from the shared test DB; it is now scoped to its own student.

## STOP report

- **Row match rates:**
  - committed synthetic fixture: **4/4** rows parsed exactly (3 grid sections + 1 by-arrangement);
  - record tests: every valid row matched; the reused index and the missing section were correctly *not* matched;
  - injection: 4/4 malformed prints refused; reused index blocked; missing section identity-only.
- **Developer's real WebReg print** (local only, never committed):
  - 5/5 rows matched a published Fall 2026 section exactly;
  - 5/5 are already in progress on the record, and 5/5 pre-selected sections equal the sections the student had confirmed by hand;
  - confirming adds only 5 registration identities and no duplicate history. This was verified in a rolled-back transaction, so nothing was written.
- **WebReg fields not stored:**
  - per-row and total credits (SOC credits are authoritative);
  - printed meeting days, times, campus and building-room (meetings come only from the exact SOC section);
  - the "Location:" note (modality evidence only);
  - the print has no instructor field.

## Please decide / check

1. **A `schedule_import_row` table**, or keep the JSON rows? Behaviour is identical and originals are kept per row; a table would mean a migration and a rewrite of the service's row handling.
2. **API path** `/api/v1/student/schedule-imports`, or move it to `/api/v1/schedule-imports`?
3. **A completed course on a current schedule** is a conflict the student resolves by keeping the record (or excluding the row). Should there be a "replace" option?

🤖 Generated with [Claude Code](https://claude.com/claude-code)
