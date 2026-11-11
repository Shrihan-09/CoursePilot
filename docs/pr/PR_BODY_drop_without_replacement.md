# A dropped course is dropped: no auto-replacement, clear preview

## The bug

Removing a course from the plan still added a replacement. On a real plan,
removing one Spring 2027 course produced a preview that added a different
course "now planned to fill" the requirement the dropped one covered. The
summary read "1 other course move", and the term stayed at 18 → 18 credits.
Expected: a pure drop.

**Root cause.** A remove became an avoid for that term, and the planner was
re-run with every other course only *softly* held. The vacated requirement
was still open, so the planner did what it always does: it filled it, in the
same term, with the best eligible course. It also re-spread elective credits.

## Existing branch checked first

`feature/fix-assistant-search-course-drop` (one unmerged commit, based on the
main before #40) has drop work, but it was **not reused**:

- It keeps the dropped term's roster, but still auto-places a replacement in a
  *later* term. On the same plan, the dropped course's requirement reappeared
  in Fall 2027, taking that term to 19 credits and a workload of 23 against a
  budget of 17.5.
- It left a second requirement the dropped course covered unmet, without
  saying so in the summary.
- It re-spread elective credits into other terms.
- Its avoid covers one term only, so the course can come back later.
- It bumps the planning engine version, which makes every saved plan stale.
- It bundles an unrelated search rewrite (async search, BM25 cutoff).

This branch is built on main and reuses its good ideas: a held roster for the
dropped term, and the below-minimum override. That branch is left untouched.

## What a drop does now

**The preview: strict holds** (`PlanningEngine(strict_holds=True)`; preview
only, never stored):

- Every remaining course stays exactly where it is. **No course is added to
  any term.** Pruning and levelling are off.
- Free-elective credits stay where they were. The dropped credits are not
  made up elsewhere, so no new semester is opened and no term gets heavier.
- A later course that needed the dropped one can no longer be placed. It
  drops out and is listed on its own line ("its prerequisite … is no longer
  planned").
- One plain summary line, for example: *"Spring 2027: 18 → 15 credits.
  Discipline-Based Writing and the 120-credit degree total (3 credits short)
  are now unmet. Graduation unchanged."*

**Saved with the plan** (`PlanEdits`; undoable):

- **Avoid in every term** (`CourseAvoid(term_code=None)`), so the course
  doesn't come back. Adding it back, or Undo, lifts this.
- **The term stays as the student left it.** Its other planner-picked
  courses become the student's own selections, and its anchors (needed
  retakes) become "must" pins that keep the retake flag.
- **"Keep it at N credits for this term":** a `TermCap(N, N,
  pinned_only=True)`. `pinned_only` is a new `TermPlanningOverride` flag: the
  planner adds no course to that term, not even on a rebuild. Elective
  credits can still sit there.

**The preview, in the UI:**

- **Below the minimum:** a warning ("15 credits is below your 16-credit
  minimum for Spring 2027."), and the confirm button reads *"Keep it at 15
  credits for this term"*.
- **Workload over budget:** a warning line ("Workload 21 is over your 17.5
  budget for Spring 2027.").
- **"Suggest a replacement":** shows course search results for the unmet
  requirements. Choosing one opens a normal **swap** preview, so nothing is
  added until the student confirms.
- **Title first, normal case,** with the code small and muted after it.
- The "affects more than one course" alert shows only when another course
  actually moves.
- **The same from the assistant.** Assistant drops call the same
  `preview_change`, and the proposal card shows the same lines, the same
  confirm wording and a "Suggest a replacement" button (which asks; it never
  applies).

**No ALL-CAPS titles anywhere.** There is now a `CourseTitle` type,
`app/domain/text.py`, on every course-title field the API returns: plan,
preview, options, search, choices, retakes, records, schedules, scenarios,
and assistant cards. ALL-CAPS SOC titles become title case ("Structure of
Information", "Intro to Music", "Calculus II", "Women's History in the
U.S."). Titles that already have lowercase letters are kept as written.
Stored data is never rewritten; saved plans are cased when read.
Transcript-extracted titles are deliberately left as the transcript printed
them.

## Tests

- **`ingestion/tests/test_course_drop.py`** (11 tests, fabricated student on
  real public SOC records):
  - a drop leaves every other course where it was and adds nothing
  - the summary line
  - apply → avoid in every term; a rebuild doesn't refill the term; Undo
    restores the course and lifts the avoid
  - below the minimum: a warning, and the term kept at its new total
    (`TermCap(N, N)`)
  - the workload warning
  - a replacement is added only when chosen (a swap preview, and the plan is
    unchanged until it's applied)
  - an assistant drop gives the same diff
  - other change kinds are not strict
  - the preview requests strict holds and the held electives
  - strict planning adds nothing and keeps electives, while the same request
    without strict holds refills (proves the test is meaningful)
- **`backend/tests/test_course_titles.py`** (17 tests): title casing,
  unchanged titles, preview lines, the saved-plan field type.
- **`frontend/tests/unit/course-drop.test.tsx`** (6 tests):
  - a pure-drop preview, with warnings
  - "Keep it at 15 credits for this term" and the button order
  - no suggestions until asked
  - title first with a muted code
  - an ordinary change keeps "Confirm"
  - the assistant card offers the same choices
- **Failure injection:** `python scripts/inject_drop_failures.py`, **7/7
  detected**. Files are restored byte for byte.

  | Fault | Caught by |
  |---|---|
  | A: backfill re-enabled | rebuild test |
  | B: preview not strict | strict-request test |
  | C: avoid for one term only | apply/rebuild test |
  | D: term not kept (no pins) | apply/rebuild test |
  | E: rebuild may refill (no `pinned_only`) | apply/rebuild test |
  | F: electives re-spread | strict-planning test |
  | G: ALL-CAPS titles | 12 title tests |
- **Suites:** run one at a time.

  | Suite | Result |
  |---|---|
  | Backend (`coursepilot_test`) | 1348 passed, 3 failed, 4 skipped |
  | Ingestion (`coursepilot_test`) | 1203 passed, 2 failed, 1 skipped |
  | Frontend | 186/186 passed; typecheck clean |
  | Visual (`tests/visual/drop-preview.mjs`) | passed |

  Backend failures:
  - `test_deployment_verification::…fully_configured…` and
    `test_student_context_api::…route_exists` fail on main too. They are
    fixed by the unmerged `feature/test-fixes-and-cost-limits`.
  - `test_planning_questionnaire::…fixed_times…` came from the new
    `pinned_only` field; the expected dict is updated and the file passes
    (43/43).

  Ingestion failures:
  - Both `test_semester_builder::…outside_pool…` cases came from the drop
    rules wrongly applying to the builder's "set this term" (which reuses
    remove). Fixed with `build_edit(..., as_drop=False)`.
  - Rerun after the fix: `test_semester_builder.py`, `test_course_drop.py`
    and `test_plan_options.py`, 51/51 passed. The full 27-minute ingestion
    suite was not rerun for that one-line scope fix.
- **Visual:** the visual check renders the preview dialog against a mock
  API. It found the dialog footer clipping its first button when there are
  three; the footer now wraps.
- **Real repro:** checked **read-only**. The same `preview_change` ran
  against the dev database inside a transaction that was always rolled back,
  so nothing was saved. Main: the bug as reported. This branch: the pure
  drop above, both warnings, no new issues, normal-case titles.

## Migrations / data

None. `pinned_only` is a new JSON field with default `False`. Saved
constraints compare after validation, so existing plans don't go stale. The
planning engine version is **not** bumped: ordinary planning output is
unchanged, and a drop is a preview setting. No stored data is rewritten.

## For you to decide or check

- **Remaining courses become "chosen by you".** After a drop, the term's
  other planner-picked courses are pinned as your selections, so a rebuild
  can't swap them. Their badges change from "planner selected" to "chosen by
  you". Unlock one to let the planner change it.
- **Rebuilds can still fill the requirement in a later term.** The dropped
  term is protected on every rebuild, but a later rebuild may place the
  requirement in a later term. That is ordinary planning. The drop preview
  itself never adds anything.
- **The 120-credit total is reported, not made up.** Dropping 3 credits can
  leave the total short. The preview says so ("3 credits short") instead of
  opening a new semester. "Suggest a replacement" fills both gaps.
- **Emptying a term:** if a drop empties the term, the cap falls back to one
  credit and `pinned_only` still holds.
- **A frontend lint config is still missing**, so `next lint` wasn't run.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
