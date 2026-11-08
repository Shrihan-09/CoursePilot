# Task 3: Close the open Degree Navigator items

Branch: `feature/task3-dn-fixes`. Roadmap phase 7.4 follow-up. Design notes: `docs/DATA_MODEL.md` section 56.

## Checklist (state before this branch → after)

| # | Item | Before | Now |
|---|---|---|---|
| 1 | AHo/AHp/AHq/AHr many-to-one to CORE_AH | not done: all four were `unmapped` (`dn-mapping/sas-core.json`) | done: member goals of `CORE_AH` |
| 1 | NS mapped through R2, "listed" ≠ met | done (`sas-core.json`: NS unmapped, R2 → `CORE_NS`) | unchanged, now pinned by a test |
| 2 | DN not met / CP met: keep CP status for planning | done (`services/degree_audit/overlay.py`: only upward changes) | unchanged |
| 2 | Never "complete" / "on track" while unresolved | partial: a per-node "Needs review" badge (`degree-navigator.tsx`), but the node, its groups and the overall summary still said Complete | done |
| 2 | Prominent warning on the Degree page and in the plan | partial: a generic disagreement label only | done, with the exact wording |
| 3 | Other-program DN block → what-if only | not done: always refused (`imports.confirm_import`) | done |
| 3 | Primary keeps the track-mismatch block | done | unchanged, tested |
| 4 | "(Summer 2019)" question | - | answered below (report only) |
| 5 | Readable SAS Core mapping table, "not yet reviewed" label | partial: the label existed in the import review (`degree-navigator.tsx`); no table | table in `docs/DN_MAPPING_SAS_CORE.md`; entries stay `pending_review` until you say so |

## What changed

1. **Member goals.**
   - In `sas-core.json`, AHo, AHp, AHq and AHr map to `CORE_AH` with `role: member_goal`.
   - The row stores `mapping_role`. A member goal is matched, never unmapped.
   - It never decides the node: the overlay skips it, and reconciliation labels it `member_goal`. Requirement R4 decides `CORE_AH`.
   - The pending-review issue now begins "Mapping not yet reviewed".
2. **DN says not met, CoursePilot says met.**
   - `DnOverlay.dn_not_met()`: DN status "not fulfilled" and reconciliation `cp_satisfied_dn_not`.
   - Planning keeps CoursePilot's status, and the plan gets `DN_SAYS_NOT_MET` (needs confirmation) per requirement.
   - **Degree page and progress page:**
     - the warning "Degree Navigator says this isn't met — confirm with your advisor.", listing each requirement;
     - the requirement and every group above it show "Needs review" instead of Complete;
     - the "Complete" filter drops them, and the tally counts them as needs review;
     - the overall state never reads "All requirements complete" or "Complete once current courses finish".
   - **Plan page:** the same warning, and the status reads "Needs review" instead of "Covers every remaining requirement".
   - The `/degree-audit-imports/progress` row label is the exact message.
3. **What-if DN imports.**
   - `POST /degree-audit-imports/{id}/confirm` takes `scope: "primary" | "what_if"`.
   - `what_if` is allowed only for a mismatched block whose program has a mapping. It refuses with `DN_WHAT_IF_NOT_ANOTHER_PROGRAM` otherwise.
   - A what-if import:
     - adds no course to the record and does not invalidate the main audit;
     - only supersedes an older what-if of the same program;
     - is reconciled against that program's audit;
     - is left out of `active_overlay()` unless `include_what_if=True`, which only that program's what-if plan asks for.
   - A primary confirmation still refuses with `DN_TRACK_MISMATCH`.
   - The import UI shows "Use for a what-if only" for such a block, and "Confirmed for what-if plans only - not your record" once it is confirmed.
4. **Docs:**
   - `docs/DN_MAPPING_SAS_CORE.md`, the review table;
   - `docs/DATA_MODEL.md` section 56.

## Migrations

`t3dn01` (revises `p10a02`). It is additive and nullable:

- `degree_audit_import.confirmed_scope`, with the check `confirmed_scope_known`;
- `degree_audit_requirement_row.mapping_role`.

NULL means primary, or node. Existing imports keep working, but their AH goal rows still say `unmapped` until they are re-imported or re-normalized.

## Tests

**New tests:**

- `backend/tests/test_degree_audit_overlay.py`:
  - the SAS Core mapping test now checks the member goals, NS unmapped and R2 → `CORE_NS`;
  - `test_a_member_goal_never_decides_its_node`.
- `backend/tests/test_degree_audit_import.py`, which needs the database:
  - `test_dn_not_met_where_coursepilot_says_met_is_never_shown_as_complete`;
  - `test_another_programs_audit_confirms_only_into_a_what_if`;
  - `test_a_what_if_is_refused_for_the_students_own_program`.
- `frontend/tests/unit/dn-not-met.test.tsx`: the requirement, its groups and the summary never read Complete; member goals are never flagged; the exact warning text.

**Results:**

| Suite | Result |
|---|---|
| backend | **1,204 passed**, 4 skipped |
| ingestion | **1,112 passed**, 1 skipped |
| frontend (`vitest`) | **110 passed** (107 existing + 3 new), `tsc` clean |

The DN files alone (`test_degree_audit_import.py`, `test_degree_audit_overlay.py`, `test_dn_fixture.py`): 45 passed. Migration `t3dn01` is applied to the test database, and `alembic check` reports no drift. It is **not** applied to the dev database yet: run `alembic upgrade head` there after merging.

## Item 4: "(Summer 2019)" in the SAS Core block

- **It is a requirement version, not the student's catalog year.**
  - DN prints "The SAS Core Curriculum (NB)", then the version of that requirement set: here, the Core as revised for Summer 2019.
  - The student's catalog year belongs to the program, and in DN to the student's entry term.
  - The owner's first Rutgers semester is Fall 2025, so 2019 cannot be their catalog year.
  - Their stored catalog year is 2026-2027. No DN import is stored in the dev database; "Summer 2019" comes from the DN print the mapping was built from.
  - Their real catalog year is likely 2025-2026, which CoursePilot does not have. A "planned against the 2026-27 catalog" notice is now in the `docs/NEXT_TASKS.md` backlog (not built).
- **Does the mismatch affect the owner's audit? No, not as built.**
  - The DN track check compares the printed version with the mapping's `dn_versions` (`["Summer 2019"]`). They match, so the mapping is used.
  - CoursePilot's own Core rules come from the 2026-27 sources, and the owner's plans use the 2026-27 CS B.S.
  - The risk is in the content, not the label: wherever the 2019 Core version that DN enforces differs from CoursePilot's 2026-27 encoding, the two audits can disagree. The reconciliation shows that as NEEDS REVIEW.
  - A DN version nobody has mapped (say "Fall 2030") already maps nothing. Every row is then shown as unmapped.
- **What supporting older catalog years would take:**
  1. Archived catalog pages per year. The 2025-26 host is known; others must be discovered from `main.catalogs.rutgers.edu`, never guessed.
  2. Curated program versions per year (`data/programs/.../<year>/`), each reviewed.
  3. A SAS Core requirement set and a `dn-mapping/` table per year, plus per-year degree totals (see the spread task).
  4. Selecting the student's own catalog year everywhere: audit, plan, DN mapping lookup.
  5. Course and offering data back to that year.

  This is a large change; nothing here builds it.

## Item 5: SAS Core mapping table

See `docs/DN_MAPPING_SAS_CORE.md`: 6 requirements and 14 goal codes. Every entry is still `pending_review`, shown as "Mapping not yet reviewed", until you say to mark it reviewed.

## For you to decide or check

1. Apply `t3dn01` to the dev database after merging.
2. Review the mapping table. Say "mark it reviewed" to flip every entry to `reviewed`.
3. What-if DN imports reach what-if **plans**. The what-if **audit** (`/student/scenarios`) doesn't use a DN overlay at all, now or before.

## Data and backfill

None required. Existing confirmed SAS Core imports keep their old AH goal rows (`unmapped`) until the next import.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

## Migration re-pointed after the wizard merge

Rebased onto origin/main (a34ccac). Migration t3dn01 now follows p10a02, keeping the wizard and DN changes in one chain; alembic heads reports only t3dn01 (head).

Before changing dev, created C:\coursepilot-private\backups\coursepilot_pre_t3fix_20261107_223139.dump (25,635,795 bytes). Safety queries found zero non-NULL confirmed_scope rows, zero non-NULL mapping_role rows, and zero Degree Navigator imports. Dev was successfully downgraded from the old t3dn01 to t2quest02 and remains there pending merge. After merge, upgrade dev to head so p10a01, p10a02 and t3dn01 run in order. No backfill is needed.

Validation for this update:
- Single head: t3dn01 (head).
- Fresh coursepilot_t3fix_test: upgrade head, downgrade p10a02, upgrade head, alembic check all passed (No new upgrade operations detected). Throwaway database dropped afterward.
- coursepilot_test upgraded from p10a02 to t3dn01. Only backend/tests/test_degree_audit_import.py, test_degree_audit_overlay.py and test_dn_fixture.py ran: 45 passed in 23.04s. Test temporary folder removed.
- No full suites run. Dev upgrade and health verification await the owner's merge confirmation.