# Planner/audit correctness fixes from the owner's plan review

Twelve fixes from the owner's review of their real plan. I checked each one
before and after on that plan with a local, read-only preview (rolled back,
output kept in the private folder). No record data is in this PR. Every
fixture and test student is fabricated.

## What changed, and why

### P0

1. **Graduation target and the first semester.**
   - Why: one transcript row dated before matriculation (the parser read it
     correctly; Degree Navigator lists the same term) made the plan count
     8 semesters from that term. The target came out a year early.
   - Change: the first semester the student states in setup now sets the
     entry term. The record's earliest term is used only when there is no
     answer.
   - The plan says when the two disagree (`GraduationTarget.conflict`, issue
     `FIRST_SEMESTER_CONFLICT`).
   - New check `record_checks.term_order_conflicts`: a record course dated
     before its prerequisite is flagged NEEDS REVIEW
     (`RECORD_TERM_ORDER_IMPOSSIBLE`) and never changed.
2. **In progress means transcript or WebReg.** A Degree Navigator "In progress
   or planned" row counts as in progress when its course is in progress on a
   confirmed unofficial transcript import, or in the confirmed current
   schedule. A course with neither counts as planned. DN's own staged courses
   never count, so DN cannot vouch for itself. A goal row's course with no term
   now matches any confirmed term (before, it never matched).
3. **Choose-one requirements are one unit.** An ANY_OF (for example the B.S.
   science requirement, 8 alternatives) counts as one unit in the progress
   metrics and the Degree page tally. That unit is the alternative the plan
   chose, else the one furthest along. Unchosen alternatives add no unit, no
   uncertainty and no "Still needed" entry. Before, the Degree page tallied
   every leaf of all 8 alternatives.
4. **In-progress courses in the current term.**
   - The bug did not reproduce on today's data. Every in-progress course,
     including a non-01 online course, comes back from `/current-schedule`
     and is listed.
   - No school-code filter exists in that path. Online sections appear under
     "Not on the calendar".
   - Hardening: a regression test for a non-01 course with no section. An
     unconfirmed course now reads "In progress on your record - section not
     confirmed".

### P1

5. **At least one course in the major's own subject each term.**
   - New preference `major_subject_each_term`, default ON, in settings when a
     workload level is set. The best eligible 01:198-style course goes right
     after the double-counters, so a related-department elective no longer
     takes the term's last major slot.
   - It may use the budget room the commitments removed, up to the level's own
     budget. It never goes past the major-related range or the credit and
     course limits.
   - Two engine bugs the new order exposed are fixed: a lab is placed right
     after its lecture, and the major-related range never refuses a lab
     joining its lecture. Before, a course placed between Physics 203 and its
     205 lab could strand the lab and force another science sequence.
6. **One credit range.** With a workload level, the student's own range (else
   the level's) is the target and maximum everywhere: planner constraints,
   stored profile, plan header. Before, the header showed the old target and
   maximum (for example "aiming for 15, never more than 18") beside a 16-19
   level.
7. **Flexible fillers.**
   - The engine already prefers async/online fillers under "Prefer flexible",
     and Spring 2027 data has async options for the same requirements.
   - In-person fillers appear when the SAVED preference is not "Prefer
     flexible". CoursePilot never changes a profile on its own.
   - New: under "Prefer flexible", an in-person filler now says "no online
     option this term", or names the online options that did not fit.
8. **Restricted ECE electives.**
   - 14:332:452 is on the 2026-27 catalog's CS electives list ("category 3",
     newbrunswick-26-27-undergrad.catalogs.rutgers.edu, Computer Science 198).
   - Every section in Fall 2025, Spring 2026, Fall 2026 and Spring 2027 is
     open only to School of Engineering (unit 14) students.
   - Changes: school-only restrictions are now decided for every student
     (a student is in one school). A term with no published sections borrows
     a school restriction when the course's last recent terms (up to 4, at
     least 2) all agree.
   - Major restrictions are never borrowed: a student may declare by then.
   - Result: 14:332:452, 423, 424, 451, 472 and similar courses are excluded,
     with the reason, for non-SoE students.

### P2

9. **"Counts twice" matches the final allocation.** A lab taken with a
   double-counting lecture is now "Major", not "Counts twice". A filler's
   "completes ..." also names the requirements it is finally credited to.
10. **"Consider Light for your rush semester".** One plan-level suggestion
    appears when every over-budget term is over only because of commitments
    (it would fit the level's own budget without them). One tap writes Light's
    range for that semester as a per-term answer and rebuilds the plan. The
    planner never applies it on its own.
11. **Conditional prerequisites name the courses.** "Prerequisites met once you
    pass 01:640:152 in Spring 2027" (with grade and "in progress" when they
    apply), on the badge and in the "why".
12. **Free electives as a course slot.** "one 1-credit elective", "one ~3-credit
    elective", "two electives (about 6 credits)". The card, the term notes and
    the fit line are updated, and "1 free-elective credits" is gone.

## Migration

`cf01` (revises `t3dn01`):

- adds `student_planning_profile.major_subject_each_term` (boolean, NOT NULL,
  default true);
- for profiles with a workload level, rewrites `target_credits_per_term` and
  `max_credits_per_term` to that one range: the student's own credit_min/max,
  else the level's (Light 12-14, Balanced 14-17, Semi-rigorous 16-19,
  Challenging 17-21). The profile `version` is not bumped.

The downgrade drops the column and does not restore the old target/maximum.

- `scripts/verify_migration_roundtrip.py`: upgrade, downgrade, re-upgrade and
  `alembic check` all passed on a throwaway DB, which was then dropped.
- `coursepilot_test` was upgraded to `cf01` to run the suites.
- **The dev database was NOT migrated.** Apply it after merging (below).

## Tests

Full suites, run once at the end:

- **backend** (`backend/tests` against `coursepilot_test`): **1234 passed, 4 skipped**.
- **ingestion** (`ingestion/tests`, all 62 files, in 4 batches so the low
  disk would hold): **1057 passed, 81 skipped, 0 failed**. The Postgres
  integration file skips without `TEST_DATABASE_URL`.
- **frontend** (`vitest run`, one worker): **14 files, 153 tests passed**.
  `tsc --noEmit` is clean.
- `ruff`: no new findings on the lines this branch adds. Older findings in the
  touched files are unchanged.

New or updated tests:

- `backend/tests/test_correctness_fixes.py`: items 5, 6, 8, 10, 12; the
  migration's level ranges match the planner's `LEVELS`.
- `backend/tests/test_progress_contract.py`: item 3, one unit, chosen and
  furthest-along alternative, uncertainty, "still needed".
- `backend/tests/test_degree_audit_overlay.py` and `test_degree_audit_import.py`
  (DB): item 2, including the confirmed-transcript path without WebReg.
- `backend/tests/test_personalization_api.py` (DB): item 4, a non-01
  in-progress course with no section is listed.
- `ingestion/tests/test_graduation_target.py`: item 1. The stated first
  semester wins (rule reversed on purpose), the conflict is shown, and a
  course dated before its prerequisite is flagged.
- `ingestion/tests/test_workload_levels.py`: items 5, 7, 9; a lab never split
  from its lecture; the scenario test's lab badge now follows the allocation.
- `frontend/tests/unit/choose-one.test.tsx`, `plan-correctness.test.tsx`:
  items 3, 6, 10, 11, 12.

`scripts/plan_quality.py sas-198-bs`, main vs this branch (fabricated
students, rolled back):

- **status** is the same on every persona/mode row, and **untraced** and
  **extra_alt** stay 0.
- **Empty-record persona:** its first term no longer gets 14:332:423, a
  400-level ECE course open only to ECE majors (item 8), so major/term starts
  0 there.
- **"Counts twice":** Core courses main badged that way, which the final
  allocation credited once, are now fillers (item 9).
- **Load changes:** credit loads shift by a few credits in some workload-level
  rows. The "one credit range" and own-subject changes cause this; no row
  loses coverage.

## For you to decide or check

- **Item 1:** the parser was not wrong. The transcript row sits under that
  term's heading, and DN lists the same term. If that course really was taken
  then, change nothing: the plan now counts from your stated first semester
  and shows the conflict. If the term is wrong, correct it in the transcript
  import.
- **Item 7:** check the filler preference in Settings > Planning. The chat
  report gives the saved value. Async options exist for Spring 2027.
- **Item 5 budget rule:** the own-subject course may use the budget room your
  commitments removed, never past the level's own budget. Say if you want it
  stricter (budget always wins) or looser.
- **Item 10 trigger:** I read "every term is over budget because of
  commitments" as "every over-budget term is over only because of
  commitments". Terms that are within budget don't block the suggestion.
- **Item 8 history rule:** a school restriction is borrowed for an unpublished
  term only when at least 2 recent terms all agree. ECE courses whose
  restriction varies by term are still planned.
- Saved plans become stale (the constraints changed), so regenerate once.

## Data / backfill

After merging, migrate the dev DB:

1. Back up to `C:\coursepilot-private\backups\` first.
2. `alembic upgrade head` (t3dn01 -> cf01).
3. Regenerate the plan.

No other backfill.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
