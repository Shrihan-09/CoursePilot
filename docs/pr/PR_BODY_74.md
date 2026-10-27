# Phase 7.4: Degree Navigator audit PDF import

Branch `feature/phase-7-4-degree-navigator-import` → `main`. Implements Prompt 03 under the 03a decisions, which override it where they conflict and are recorded in `docs/PHASE_7_3_PLAN.md` §10.

## What changed and why

A student uploads their own Degree Navigator audit PDF, reviews it, and confirms it. On confirmation:

- its completed and current courses join their history;
- its requirement statuses decide what planning treats as done, for mapped requirements.

A confirmed DN status decides **status** only and never changes a CoursePilot requirement rule. Every disagreement with CoursePilot's own audit is shown as **NEEDS REVIEW**.

- **Extraction and parsing.**
  - Extraction (`services/degree_audit/pdf_worker.py`, `domain/degree_audit.py`) runs in the existing killable child process and returns positioned text plus each icon's position, size and SHA-256.
  - Status icons are classified **only** by pixel identity with DN's own legend in the same PDF. An icon that matches nothing is `unknown` and goes to review.
  - The parser reads degree blocks, requirement headings, Total/Completed/Needs, Core goal sets, two-courses-per-line numbered lists, placement rows, the print timestamp and the degreeID.
  - The name and RUID block is skipped and never stored, returned or logged.
  - Image-only PDFs are refused with the Chrome "Save as PDF" message.
- **Consistency checks** turn every inconsistency into NEEDS REVIEW instead of a guess:
  - the status icon contradicts the "Needs" line;
  - "Completed: n" differs from the listed courses;
  - "Completed Requirements k of n" differs from the statuses read;
  - footer pages are missing;
  - a row is split or unreadable;
  - a row is unclassified (transfer/AP), with its raw text kept.
- **Staging** (migration `74dnimport01`, additive): `degree_audit_import` (one per degree block), `degree_audit_requirement_row`, `degree_audit_course_row`. Original values sit in their own columns; row state is derived from issues.
- **Courses use the existing transcript path.** No second path was built.
  - Courses are staged as a linked `transcript_import` with `source_document='degree_navigator_audit'` (new column, default unchanged).
  - They go through `normalize_row`, ALREADY_RECORDED → skipped, the `conflict` → keep/replace resolution, and `confirm_import`.
  - The transcript API never lists or confirms a DN-owned staging.
- **Mapping tables** (`data/programs/rutgers/nb-undergrad/2026-2027/dn-mapping/`):
  - Matching is exact only (subject, heading, goal code).
  - SAS Core was built from the real audit: R1-R6 and 9 goal codes, all `pending_review`.
  - The track check blocks confirmation on a mismatch ("this audit is for X, your CoursePilot track is Y").
- **Reconciliation** labels each row `agrees`, `dn_satisfied_cp_not`, `cp_satisfied_dn_not`, `allocation_differs`, `unmapped` or `unknown` against CoursePilot's own Degree Engine audit.
  - Disagreements show on the review, the Degree page and the plan's notes.
  - They are also logged anonymously; `scripts/dn_disagreement_report.py` aggregates them per node.
- **Planning** (`services/degree_audit/overlay.py`). `PlanningEngine` receives an `OverlaidAuditEngine` for the confirmed audits.
  - **DN satisfied** → SATISFIED.
  - **"In Progress or Planned"** splits per decision 2. It is IN PROGRESS only when every course behind it is in the confirmed current schedule; otherwise it is PLANNED, which counts as not satisfied. The rule shown says which applied.
  - **Unmapped requirements** keep CoursePilot's status, labelled "estimated".
  - **No import** → the plan says the status is estimated.
- **Freshness.**
  - The audit's "as of" is the Chrome print time, else the upload time.
  - It is stale after 30 days, or if it was printed before the latest term's final grades. Those dates are cited in `core/grade_calendar.py` from the Registrar's 2026 calendars.
  - A new confirmation marks saved plans out of date (`degree_navigator_audit_imported`, through `academic_fingerprint`).
- **API:** `/api/v1/degree-audit-imports` - upload, list, `progress`, get, resolve row, confirm, discard. Owner-only; another student's import is 404.
- **UI:** the Degree page has a **Degree Navigator** tab with:
  - export instructions;
  - upload;
  - a review table covering requirements, Core goals, courses to add (with conflict resolution) and unclassified lines;
  - the reconciliation badges;
  - confirm.

  "My program" shows an "estimated" / "From your Degree Navigator audit" notice with freshness and a re-import nudge. Each requirement says which source decided it and shows the NEEDS REVIEW label.
- **Other:**
  - `scripts/inject_dn_failures.py`: flipped marker, dropped allocation, shifted column. All three are caught.
  - The synthetic fixture generator was rewritten to match the real layout: Chrome's flipped text space, separate fragments, the two-column legend, page breaks.
  - Docker mounts `data/programs` read-only (`DN_MAPPING_ROOT`).
  - Docs: DATA_MODEL §51 and PHASE_7_3_PLAN §10/§10a.

## Tests

- New tests:
  - `test_degree_audit_import.py`: 12 tests, PostgreSQL.
    - staging writes nothing;
    - identity never stored;
    - confirm is idempotent and goes through the transcript path;
    - conflict resolution;
    - **DN overrides CoursePilot for planning**, while the rules still say unsatisfied;
    - estimated label with no import;
    - unmapped requirement kept and shown, never used;
    - track mismatch blocks;
    - image-only PDF refused;
    - multiple degree blocks;
    - another student → 404;
    - flipped icon → review → resolve.
  - `test_degree_audit_overlay.py`: 16 tests.
    - the decision-2 split;
    - overlay lift and ancestors, and never lowering;
    - freshness;
    - mapping-table validation against `sas-core.json`;
    - the three injected failures.
  - `test_dn_fixture.py`: 6 tests, including parser accuracy on the synthetic fixture.
- Full suites: backend 1,102 passed / 4 skipped (was 1,072; +30 new); ingestion 1,057 passed / 1 skipped; frontend 80 passed, `tsc` clean. Ruff clean on all new modules.
- Migration round trip (`verify_migration_roundtrip.py`) and `alembic check`: clean.

## Extraction accuracy

- **Synthetic fixture: 100%** of the generator's model:
  - block fields;
  - 6/6 requirements (title, icon, counts);
  - 14/14 goal sets with their course icons;
  - 10/10 requirement course rows;
  - 13/13 course-list rows.
- **Injected failures:** 3/3 flagged; the undamaged audit flags nothing.
- **Developer's real audit**, run locally from the private folder and never committed:
  - requirements 6/6 on number, title, Needs, Completed count, and icon consistent with Needs;
  - course rows 30/30 exact;
  - 14 goal sets.
- **Real reconciliation** against CoursePilot's own audit of the dev record:
  - 6/6 requirements `agrees`;
  - 9/9 mapped goals `agrees`;
  - 5 goal codes `unmapped` (AHo, AHp, AHq, AHr, NS).

## Migrations

`74dnimport01` (additive). The downgrade **refuses** while any DN import or DN-sourced staging exists.

## Please decide / check

1. **CS mapping not built.** Your real audit contains only "The SAS Core Curriculum (NB) (Summer 2019)"; it has no CS block. `sas-198-ba.json` and `sas-198-bs.json` are therefore empty, so CS DN requirements show as `unmapped` (estimated). Export your CS audit to `C:\coursepilot-private` and I'll build that table.
2. **The SAS Core mapping is `pending_review`.** It is used and labelled "mapping not yet reviewed". Mark the entries `reviewed` after checking them, or say if pending entries should not drive planning.
3. **Goal codes:** the real audit has **14** goal codes, not 13. AHo-AHr are unmapped because CORE_AH is one node over four goals. NS is unmapped as a goal because R2 carries CORE_NS.
4. **DN "not satisfied" vs CoursePilot "met"** is NEEDS REVIEW, but planning does not lower the status, because DN doesn't say which course is missing. Confirm this, or choose a rule.
5. **Print timestamps** are read as Eastern time.
6. **The PDF is deleted right after extraction** (the transcript rule), which is stricter than "after confirmation or expiry".

## Data / steps per environment

1. `alembic upgrade head`.
2. Docker: `docker compose up -d --build backend`. The new read-only mount `./data/programs:/data/programs` and `DN_MAPPING_ROOT` are needed.
3. No backfill.

## Follow-up commit: requirement-by-requirement check against 03 and 03a

- **WebReg schedule counts (03a #2).** The "In Progress or Planned" split now reads the confirmed current schedule from BOTH the WebReg schedule import (`student_registration`) and hand-confirmed sections. Before, it read only the latter.
- **Each recommendation's reason (03a #5).** `PlannedCourse.notes` says when the requirement a recommendation counts toward, or a group above it, takes its status from DN, and starts "NEEDS REVIEW" on a disagreement. The plan card renders the notes.
- **Testable review UI.** The Degree-page review was refactored into `DnReviewBody`, `BlockPicker`, `ExportInstructions`, `RequirementTable` and `HistoryRow`.
- **New tests:**
  - Frontend, 13 (`tests/unit/degree-navigator.test.tsx`): import step; review table; reconciliation badges; NEEDS REVIEW; conflict actions; confirm disabled until ready; track mismatch; estimated vs from-DN labels; stale nudge; block picker; plan-card notes.
  - Backend, 5: WebReg in-progress split; anonymous developer report (subprocess); no identity, course or grade in logs; a saved plan goes stale on a DN confirmation; recommendation notes through ancestors.
- **Suites:** backend 1,107 passed / 4 skipped; ingestion 1,057 passed / 1 skipped; frontend 93 passed; `tsc` clean.

## Follow-up commit: the second real DN print (Undeclared)

- **The print.** The developer's second real print is "Undeclared (NB) (Fall 2003)". It has 0 requirements, because DN has no declared major on record, and lists every course under "Courses Do Not Apply for this Program". It has no Computer Science block, so **the CS mapping is still not built**.
- **Parser bug fixed.** This print first parsed to **no course rows**: the header only ended at "Requirements", and the unknown list title was skipped silently. Now:
  - the header also ends at Conditions/Courses;
  - "Courses Do Not Apply for this Program" is a known list;
  - a row under any unrecognized list title is kept with `UNRECOGNIZED_COURSE_LIST` and goes to review, never dropped.
- **Results on the real prints:** 21/21 rows read on the undeclared print; the SAS Core print is unchanged (6/6 requirements, 30/30 rows, reconciliation all agree).
- **Tests:** the synthetic undeclared model plus 3 tests (parse, unknown list title, undeclared import stages and confirms its courses). Backend 1,110 passed / 4 skipped.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
