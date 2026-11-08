# Prompt 10a: first-run setup wizard

New students now get a short step-by-step setup instead of one long preferences form. Returning students go straight Home. Planning preferences are now split into the same sections.

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
