# Prompt 10a: first-run setup wizard

New students now get a short step-by-step setup instead of one long preferences form. Returning students go straight Home. Planning preferences are now split into the same sections.

> The original wizard (below "Starting checklist") was merged as PR #29. This PR is the **follow-up**: the owner's decisions on that STOP report, class-standing paths and retake timing. Read "Follow-up decisions" first.

## Follow-up decisions

| # | Item | Status | What changed |
|---|---|---|---|
| 1 | Rebase and migration order | **Done, differently from the brief** | See the note below the table. |
| 2 | Program support labels | **Done** | See below. |
| 3 | First semester as the fallback entry term | **Done** | See below. |
| 4 | Retakes: list them, and choose when | **Done** | See below. |
| 5 | Routing | **No change** | The rule is still "setup isn't finished → wizard". |
| 6 | Class-standing paths and grades reminder | **Done** | See below. |

### 1. Migration order

The brief said task 3 is merged, but it isn't. `main` (87010aa) contains this branch (PR #29) and not `t3dn01`, which is still only on `feature/task3-dn-fixes`.

So:
- this branch was fast-forwarded to `main`;
- `p10a01` stays on `t2quest02`;
- the new `p10a02` follows `p10a01`;
- `alembic heads` shows a single head, `p10a02`.

Pointing `p10a01` at `t3dn01` would have broken `main`, because that revision doesn't exist there. **When task 3 merges, `t3dn01`'s `down_revision` must change from `t2quest02` to `p10a02`.** It's a one-line change on that branch.

The round trip (upgrade → downgrade to `t2quest02` → upgrade) and `alembic check` pass on a throwaway database.

### 2. Program support labels

- **"Beta supported"** only when a version is published with `publication_basis = human_review`.
- **"Preview — not yet reviewed"** for legacy-curated and pending-review programs. This includes CS B.A. and B.S. until you review them.
- **"Coming soon"** for programs with nothing encoded.

It's a frontend-only change in `programSupportLabel`.

### 3. First semester as the fallback entry term

- The order for counting the graduation target is now:
  1. the first Rutgers Fall/Spring on the record;
  2. otherwise **the first semester stated in the wizard**;
  3. otherwise the first planned term.
- The basis reads "8 semesters counted from the first semester you told us, Fall 2026".
- The record still wins when it has a Rutgers semester.
- The About you and Preferences hints say this now.

### 4. Retakes

**Endpoint: `GET /student/plans/retakes`**
- It is read-only, owner-resolved and parameterless.
- It runs the student's own plan with their confirmed preferences, minus retake pins, so the baseline is "no choice made". It lists the courses the planner marks as retakes. That marker is a new `PlannedCourse.retake` field holding the reason in words.
- **Reasons:**
  - "grade F earns no credit";
  - "you withdrew (W)";
  - "grade D is below the C 01:198:314 needs", taken from the prerequisite grade check.
- **Unlocks:** the planner's own `unlocks`, meaning the courses whose prerequisite needs this course, in the plan or its targets.
- **Options:**

| Option | Term | When shown |
|---|---|---|
| ASAP | earliest Fall/Spring from the plan's start that has offering evidence | always |
| Winter session | the next winter | **only** with offering evidence for that winter |
| Next semester | the plan's first Fall/Spring | always; unselectable and labelled "Same semester as ASAP" when it is the same term |
| Summer | the next summer | **only** with offering evidence for that summer |

- **Evidence labels.** The evidence is the planner's own offering index: "Listed in the Spring 2027 Schedule of Classes", or "Historically offered (Summer 2024, Summer 2025, Summer 2026) — not yet confirmed for Summer 2027".
- **Default.** ASAP when the retake unlocks a course the plan places within two Fall/Spring terms after the ASAP term; otherwise Next semester.
- **Impact.** For each course whose prerequisite names the retake directly:
  - the earliest term it could follow the chosen option, compared with the term the baseline plan gives it;
  - shown as "This keeps 01:198:323 on track for Fall 2027" or "Summer delays 01:640:251 to Fall 2027";
  - delay warnings also appear on options that aren't selected.
- **Cost.** One line: "Winter and summer courses are billed separately — check Rutgers' tuition page." There are no dollar amounts.

**Saving the choice**
- The choice is saved as a pin `{course, term_code, retake: true}`.
- **Engine:** a winter or summer term holding a pin is planned **for that pin only**, even with the student's winter/summer preference off, and the general preference is unchanged.
- The term may be the one just before the plan start (Winter 2027 before Spring 2027), but never one the record already reaches.
- Levelling never moves other courses into it.
- `retake: true` also lets a pin place a course passed with too low a grade. Without the flag, the first-term pin is refused as "already completed".

**Where it appears**
- The wizard's Musts step.
- Plan → Preferences → Musts, editable.
- Ordinary pins are listed separately.

### 6. Class-standing paths and grades reminder

**"What year are you?"**
- Added to About you, with the options First-year / Sophomore / Junior / Senior / Transfer.
- Stored as `student.class_standing` via `PUT /student/onboarding/setup`.

**Bring your record order**

| Standing | Main cards | Collapsed |
|---|---|---|
| First-year | WebReg ("Upload your current schedule") | transcript and DN under "Later — we'll remind you after your grades come in" |
| Transfer | DN ("Shows which transfer credits Rutgers accepted"), transcript, WebReg | none |
| Sophomore / Junior / Senior / not stated | transcript, DN, WebReg | none |

- For first-years, "Have AP or transfer credit? Upload your Degree Navigator audit now" moves the DN card up.
- There is no CSP import.

**Reminder: `GET /student/grades-reminder` and `POST /student/grades-reminder/dismiss`**
- It shows after the latest published final-grades date in `app/core/grade_calendar.py`. Dates are copied from the Registrar's calendar and never estimated.
- It shows only for kinds whose latest confirmed import is older than that date.
- A kind never imported counts only when the stated first semester is no later than the graded term. That way a first-year isn't reminded before their first grades.
- Dismissing stores the graded term (`student.grades_reminder_dismissed_term`). The reminder returns at the next term's grades date.
- It appears as a dismissible Home alert with an **Upload** link to the new **`/records`** page. That page shows the same three cards after setup; there was no transcript upload page for students who had finished setup.

### Follow-up tests

**Root suite (PG):** see the results table in "Follow-up results" below.


**New tests: backend and ingestion**

`ingestion/tests/test_retake_timing.py` (7 tests, real SOC samples and the curated CS B.A.):
- a retake is listed with its reason, unlocks and cost note, with no "$";
- **a blocking retake defaults to ASAP**;
- **winter appears only when offered that winter**: there are no winter or summer options without winter data, and a Winter 2026 offering adds Winter 2027, labelled historical;
- **a summer choice shows the delay warning**;
- **the choice is saved as a term pin and the planner respects it**: Winter 2027 is opened for Calc II only, and with no pin no winter term is planned;
- a `retake` pin places a D-graded course, and is refused without the flag.

`ingestion/tests/test_graduation_target.py` (+2 tests):
- a stated first semester is the entry term when the record has none;
- the record wins over a stated first semester.

`backend/tests/test_grades_reminder.py` (3 tests, pure):
- the reminder shows only after the grades date and only for older imports;
- it stays dismissed until the next term's date;
- a first-year with no grades yet isn't reminded.

`backend/tests/test_onboarding_api.py`:
- class standing is saved, omitted, and rejected when unknown;
- the reminder's shape, and dismissing it;
- the retake list's shape, and that it is read-only.

The route inventory now has 3 new routes.

**New tests: frontend**

`frontend/tests/unit/setup-followup.test.tsx` (13 tests):
- the **card order and collapsed sections for each standing**;
- **the AP line reveals DN**;
- the retake defaults, with winter only when listed;
- the summer delay warning;
- the choice becoming a retake pin;
- the "same semester as ASAP" handling;
- no dollar amounts;
- the reminder's wording, dismissal, and hiding.

The label test in `setup-wizard.test.tsx` was updated.

**Browser walkthrough:** `frontend/tests/visual/wizard-followup.mjs` (local only). It ran at phone width (390px) in dark and light against a throwaway copy of the dev database, since dropped:
- 16 screens, with **0 axe (WCAG 2 A/AA) violations, 0 overflow, 0 failed checks**;
- it covers first-year and transfer card order, the AP reveal, a failed Calc II listed on Musts, the Summer choice saved as a retake pin, the Home reminder shown then staying dismissed after reload, and the retake editable in Preferences.

### Follow-up migrations

`p10a02` (revises `p10a01`) is additive and nullable:
- `student.class_standing`: `VARCHAR(16)`, with a CHECK on the 5 values;
- `student.grades_reminder_dismissed_term`: `VARCHAR(5)`.

The downgrade drops both. `PlannedCourse.retake` and `CoursePin.retake` are JSON fields with defaults, so saved plans and profiles still load.

### Follow-up: for you to decide or check

1. **Task 3's migration** must be re-pointed to `p10a02` when it merges (see 1).
2. **What counts as "blocking."** Right now it means a course the plan places within two Fall/Spring terms after the ASAP term. Tell me if you'd prefer it measured from the plan's start instead.
3. **The impact compares the earliest possible term with the baseline plan's term,** and only for courses that name the retake directly. Later links in a chain move with their own prerequisites; this read-only check doesn't re-plan them.
4. **A summer delay is rare in practice.** Summer comes after a Spring ASAP and before Fall, so it only delays a course the plan had put in a summer term.
5. **The wizard writes the default as a retake pin** when Musts opens, so the plan matches what's shown. "Skip for now" drops it, and the planner then places the retake itself.
6. **`/records`** is a new small page, so the reminder has somewhere to upload. It isn't in the navigation; tell me if it should be.

### Follow-up results

| Suite | Result |
|---|---|
| Root (backend + ingestion, PG), 2,331 tests | **2,326 passed, 5 skipped, 0 failed** (Part A: 2,313 + 5; +18 new). The single full run stopped at about 92% because the C: drive ran out of space, which errored the last tests. Tests 1-2,126 had passed by then. The 236 tests from `test_retake_timing.py` to the end were re-run on their own after space was freed: 236 passed. |
| Frontend `tsc` / vitest / `next build` | clean / **134 passed** (121 + 13 new) / built |
| Migration round trip + `alembic check` + single head | **OK** (`p10a02`) |
| `wizard-followup.mjs`: phone width, dark and light, 16 screens | **0 violations, 0 overflow, 0 failed checks** |

**Data and backfill:** none. Existing students keep `class_standing` and `grades_reminder_dismissed_term` as NULL.

---

## Starting checklist (what existed before this PR)

| Area | Before | Now |
|---|---|---|
| First-run routing | **Partial.** Only users with *no academic profile* were sent to `/onboarding`. | Anyone who hasn't finished setup goes to the wizard; `/onboarding` sends returning users Home. |
| Onboarding flow | **Partial.** 6 views (program → courses → review → placement → one long workload form → done). | 8 one-topic steps; the old flow is removed, and its pieces are reused. |
| Resume | **Partial.** Derived from the transcript import state only. | Every step is autosaved; resuming opens the first unfinished step. |
| Program support label | **Partial.** Reviewed / Legacy curated / Pending review. | "Beta supported" / "Preview — not yet reviewed" / "Coming soon", taken from the same published status. |
| Transcript, Degree Navigator and WebReg uploads, with staged review | **Done**, but on 3 separate pages. | Reused unchanged, inside 3 cards on one step. |
| Workload levels, commitments, schedule shape, pins | **Done**, but as one long form. | The same fields, as cards and chips. |
| Plan → Preferences | **Not done.** One wall of fields. | 6 collapsible sections matching the wizard; `#week` etc. opens a section. |
| First semester at Rutgers | **Not done.** Not stored anywhere. | Stored as stated, and shown back. Planning does **not** use it (see "For you to decide" 2). |
| Glow design tokens | **Not done.** | `--glow-accent`, `--glow-accent-2`, `--glow-edge`, `--glow-halo`, plus 4 CSS classes. |

## What changed

**Backend**
- `PUT /api/v1/student/onboarding/setup` takes `{completed_steps, first_term?}`.
  - It is owner-resolved, parameterless and `extra="forbid"`.
  - Unknown step keys and badly shaped term codes are a 422.
  - With no profile it returns 409, like the other `/student` routes.
- `GET /student/onboarding` now also returns `setup: {steps, completed_steps}`. The profile now includes `first_term`.
- Neither field feeds an engine, and the audit cache is untouched.

**Frontend**
- `features/setup/`: the wizard, its steps, the choice controls (native radios and checkboxes styled as cards and chips), and a first-semester editor.
- `lib/setup.ts`: the step list, resume, routing, upload-card state and label rules, as pure functions.

**The 8 steps**

| # | Step | What it does |
|---|---|---|
| 1 | Welcome | One sentence on what CoursePilot does, plus "Takes about 3 minutes". |
| 2 | About you | School filter, program list with support label, catalog year, declared / intended, first semester, and On time / Early / No rush or a specific term. Not skippable. |
| 3 | Bring your record | Three cards, described below. |
| 4 | How intense? | Four big cards, each with a one-line range, e.g. "16-19 credits · 3 major courses", plus its workload budget. |
| 5 | Your week | Chips for job band, Greek life, clubs, athletics / research / recruiting hours and honors. Shows the live line "About N hours a week, so each semester's workload budget is X lower". |
| 6 | Schedule shape | Chips for no class before 10am / no Fridays / no evenings, campus chips, an "Add a time" mini-form, and the filler format. |
| 7 | Musts | A note that retakes come from the record automatically, plus course pins. |
| 8 | Build my plan | Saves defaults if needed, marks setup complete and builds the plan, with the existing generation animation. Then lands on Home. Shows "Estimated — import your audit for accuracy" when there is no confirmed Degree Navigator audit. |

**Step 3: Bring your record**
- Three cards: unofficial transcript, Degree Navigator audit and WebReg schedule.
- Each card has a 3-step how-to. The Degree Navigator and WebReg cards say "Save as PDF, not Microsoft Print to PDF".
- Each card shows its state: Not added / Processing / Needs review / Confirmed.
- You can drag a PDF onto any card.
- Opening a card shows the existing staged review → confirm flow inline.
- Manual course entry and the optional math placement sit here too, so the old flow's math placement step isn't lost.

**Every step**
- Shows "Step N of 8" with a glowing progress bar.
- Has Back / Next, and every step except About you has "Skip for now". Skip drops that step's unsaved edits, so the defaults apply.
- Moves focus to the step heading on each change.

**Layout and motion**
- Mobile-first, with a single column and tap targets of 44px or more.
- Steps slide in. Under `prefers-reduced-motion` they don't, and the glow is static.

**Glow** (`globals.css`, documented in `docs/DESIGN_SYSTEM.md`)
- The active card has a 1px conic-gradient border whose `@property` angle turns every 9s.
- The progress fill glows.
- Selected cards and chips get an edge glow.
- Sections are divided by a faint glowing rule.
- The glow is on borders and halos only, never behind text.

**Plan → Preferences**
- `PreferencesForm` is grouped into About you / Intensity / Your week / Schedule shape / Musts / More.
- Only one section is open at a time. All field logic is unchanged.
- A list of anything to check sits next to the submit button, so errors inside collapsed sections are still visible.
- `QuestionnaireFields` is split into exported section components. Its combined output is unchanged.
- First semester and declared status can be edited above the form.

**No new planning logic.** The wizard calls the existing endpoints and writes the same planning-profile fields the form writes:
- `POST /student/profile` and `PUT /student/program`;
- `PUT /student/declaration`;
- `PUT /student/planning-profile`;
- `POST /student/onboarding/complete` and `POST /student/plans`.

## Why

The long form tired new students before they ever saw a plan. One topic per screen, with defaults and skips, gets them to a plan in about 3 minutes, and every answer stays editable later.

## Tests

Full suites, run once at the end, against `coursepilot_test` (PostgreSQL 16):

| Suite | Result |
|---|---|
| Root (backend + ingestion, PG) | **2,313 passed**, 5 skipped (backend 1,201 = 1,200 before + 1 new; ingestion 1,112, unchanged) |
| Frontend `tsc` / vitest / `next build` | clean / **121 passed** (107 before + 14 new) / built |
| Migration `p10a01` round-trip (upgrade, downgrade, re-upgrade, `alembic check`) on a throwaway database | **OK** |
| `tests/visual/wizard.mjs`: dark and light × 390px and 1280px, 44 screens | **0 axe (WCAG 2 A/AA) violations, 0 overflow, 0 failed checks** |

**New tests**
- `backend/tests/test_onboarding_api.py::test_setup_wizard_progress_is_saved_validated_and_owned` checks:
  - saving progress, and keeping steps in wizard order;
  - omitting `first_term` leaves it unchanged, and `null` clears it;
  - unknown steps, bad terms and identity fields are a 422;
  - 409 before a profile exists;
  - another account sees only its own progress.
- The route inventory in `test_student_context_api.py` now includes the new route.
- `frontend/tests/unit/setup-wizard.test.tsx` covers:
  - **routing:** a new user goes to the wizard, a returning user goes Home, and an unreachable API never traps anyone;
  - **resume:** the first unfinished step, including gaps;
  - **skipping:** which steps can be skipped, and the "estimated" label;
  - **upload cards:** the state mapping, the state in words, and the review opening inline;
  - **Preferences:** wizard answers appear grouped into sections, with only one section open;
  - **accessibility:** native labelled inputs, plus the support-label and level-line wording (no "easy"/"hard").
- `frontend/tests/visual/wizard.mjs` (local, not CI) drives the real stack in Chrome and checks:
  - a new user is routed to the wizard, and `/home` bounces back to it;
  - leaving after About you and returning opens "Bring your record";
  - focus lands on `#setup-step-title` on every step;
  - Build lands on Home with a plan, and is labelled "estimated" with no audit;
  - `/onboarding` sends a returning user Home;
  - Preferences shows the job band and level chosen in the wizard;
  - the glow has `animation: none` under reduced motion;
  - axe and overflow on every screen.
- `tests/visual/qa.mjs` is updated to the wizard. It was **not run**: it needs the sanitized transcript fixtures directory.

## Migrations

`p10a01` (revises `t2quest02`) is additive and nullable:
- `student.setup_steps`: a JSON list of step keys;
- `student.first_term`: `VARCHAR(5)`, with the CHECK `^[0-9]{4}[0179]$`.

The downgrade drops both columns.

**SQLite-portable CHECK.** The CHECK is `length(first_term) = 5`; the exact shape is validated by the service. My first version used the Postgres `~` regex operator, and the full run caught it: the ingestion suite builds this schema in SQLite. Fixed and re-run.

**Two heads.** Task 3's `t3dn01` (PR not merged yet) also revises `t2quest02`. Whichever PR merges second must point its `down_revision` at the other so there is one head. If task 3 merges first, I'll re-point `p10a01` to `t3dn01` on this branch; it's a one-line change.

## For you to decide or check

1. **Support labels.** "Beta supported" is shown for any program version marked `supported`, including "legacy curated" ones. "Preview — not yet reviewed" is shown for `pending_review`, and "Coming soon" when every version is unavailable. Tell me if "legacy curated" should read differently.
2. **First semester is stored, not used.** The brief says "no new planning logic", so planning still counts On time from the first Rutgers Fall/Spring on the record, or else from the first planned term. Should a stated first semester be the fallback when the record has none? It would be a small engine change.
3. **Retakes on the Musts step are described, not listed.** Retakes are detected inside the plan engine; no endpoint lists them on their own. The step says they are added automatically and marked in the plan. Listing them before the plan exists would need a small read-only endpoint.
4. **Routing now uses "setup finished" (`onboarding_complete`), not "has a profile".** Anyone with a profile who never pressed complete will be sent to the wizard, at the first unfinished step. Locally that's 0 of 1 linked accounts.
5. **Build my plan.** If plan generation fails, setup still completes and the student lands Home; the Plan page explains what is missing. Completing is refused while a transcript import is still open, and the wizard says to finish or discard it.

## Data and backfill

None. Existing students keep `setup_steps = NULL` and `first_term = NULL`.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
