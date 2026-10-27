# Phase 7.2: Core Student Experience (Degree → Plan → Schedule)

## Summary

The deterministic engines now reach the student as one workflow:

```
DEGREE    "What remains?"         Degree Engine           (+ what-if, same engine)
   ↓
PLAN      "What should I take?"   Planning Engine         (saved; knows when it is out of date)
   ↓
SCHEDULE  "When can I take it?"   Schedule Engine         (real Rutgers sections; register yourself)
```

**No engine was rewritten.** The additions are one derived table, four
endpoints, the pages and a shared vocabulary. Every academic statement on a
page is an engine's output, rendered as returned.

## Investigation findings (read-only, before coding)

- **Degree:** `GET /student/audit` and the scenario endpoints were
  sufficient.
  - Requirement explanations are the engine's English sentences; there are no
    structured reason codes, so they are shown as written.
  - The pending-review state appears both in the profile lifecycle and as an
    engine finding.
- **Plan:** one stateless `POST /plans/generate` endpoint, taking 1–8 s.
  - It had no persistence, no plan id and no term context, so a student would
    have had to guess the start term and could get a 422.
  - Blocked and UNKNOWN courses appeared only as codes inside issue details.
- **Schedule:** one endpoint and no term listing (`/meta` hard-codes
  `loaded_terms=[]`).
  - Structure and live availability were already separate in the schema.
- **Data:** the local database holds sections for Fall 2025, Spring 2026,
  Summer 2026, Fall 2026 and Winter 2027 (partial).
  - **There are no Spring 2027 sections:** the Rutgers feed returned none when
    it was last polled.

## Backend

- **`student_plan`** (migration `edd2bebed386`, a new table only) holds one
  saved plan per student, as derived state:
  - the Planning Engine's exact `PlanResult`;
  - the request snapshot;
  - the engine versions and the engine's own input fingerprints;
  - deletion cascades from the student.
- **`POST /student/plans`** generates and saves MY plan for MY program,
  under the Phase 7.0 writer lock.
  - The body is a start term and constraints only. A program, status or plan
    is a 422, and what-if plans are never saved.
- **`GET /student/plans/current`** returns the plan with **server-computed
  freshness**. The fingerprints are recomputed with the engine's own
  functions, and the reasons are:
  - `program_changed`;
  - `academic_record_changed`;
  - `program_rules_changed`;
  - `rutgers_data_changed`;
  - `engine_updated`;
  - `start_term_passed`.
- **`blocked_courses`** gives each course the engine would not place its
  catalog title and its **original Rutgers prerequisite text**. Nothing is
  re-evaluated.
- **`GET /student/plans/context`** returns the earliest valid start term,
  using the engine's own refusal query, and the terms that actually have
  section data, using the Schedule Engine's own rule.
- **`DELETE /student/plans/current`.**

## Web

- **Home:** one next step (set up → plan → refresh a stale plan → schedule),
  plus summaries of the degree and the plan, and the transcript-reported GPA
  labelled as reported.
- **Degree:**
  - the overall result, with each credit figure defined in place;
  - requirement counts by status, never a percentage;
  - the tree, filterable (all / still needed / can't determine / complete).
    Each requirement opens to the engine's reason, the courses and grade
    conditions used, and the Rutgers text;
  - program rules and courses that aren't counted;
  - the pending-review warning, shown if EITHER the lifecycle or the engine
    says so.
- **What-if:** the same engine against another program, shown side by side,
  with the course and requirement differences. A banner that stays on screen
  says it is **hypothetical and the program is unchanged**. This was
  verified: the profile was unchanged afterwards.
- **Plan:**
  - settings the engine honours, and nothing else;
  - a branded generation state with ONE honest stage, because the engine
    reports no progress;
  - semesters with "Why this course, this term", built only from the
    engine's reasons and evidence;
  - blockers, and courses needing confirmation with the Rutgers condition
    ("not a finding that you are ineligible");
  - a stale notice naming the reasons, with a regenerate button.
- **Schedule:**
  - Courses come from a saved-plan semester (by reference) or are chosen
    explicitly.
  - An unpublished term is said to be unpublished and is **never swapped**.
    Exploring another term starts with no term chosen.
  - The comparison shows days, start and end times, gaps (from the engine's
    score) and weekly time. The ranking is explained, and no option is called
    "best".
  - The week is a grid on wider screens and day tabs with an agenda on
    phones. It is one accessible representation, and untimed meetings are
    never drawn.
  - **Structural validity and live availability are separate panels.**
    "Open as of" appears only for a current observation.
  - Index numbers are listed for WebReg, with the statement that CoursePilot
    registers no one.
- **One vocabulary module** translates engine enums. **UNKNOWN never shares
  a label, icon or tone with FAILED.**

## Found and fixed during this phase

- **Dark-mode primary hover** (`#0E93FC`) carried white text at about 3:1.
  This was a 7.1 token that only showed up when 7.2 left the pointer resting
  on primary buttons. The hover is now the logo's deep blue `#0154E0` in both
  themes.
- **Touch targets** on Home links, schedule option buttons and chip remove
  buttons were too small.
- **A raw engine code** (`no_corequisite_published`) reached the student.
  Every literal code now has words, pinned by a test.
- **The new injection script initially counted a pytest "file not found" as
  a detection.** A suite that does not run is now reported as a failed
  injection, never as a catch.

## Performance (local, real engines and Rutgers data, fabricated student)

| Call | Time |
|---|---|
| Degree audit | 132 ms cold, 38 ms warm |
| Generate and save plan (5 terms) | 3.3 s |
| Read saved plan (decode, freshness, blocked courses) | 72 ms |
| Schedule, Fall 2026, 4 courses, 10 options | 56–188 ms |

The plan is the only multi-second call. Saving means the student pays that
cost once per change of inputs, not once per visit. Nothing was optimized
speculatively, and no academic result is cached client-side beyond the
request cache; the audit and plan refetch on every view.

## Tests

| Suite | Result |
|---|---|
| `backend/tests/test_saved_plan_api.py` (PostgreSQL): ownership, exact engine output, replace-not-accumulate, the strict save body, delete, reads write nothing, cascade, each staleness cause, context, 401/409/422, blocked courses with the Rutgers text | 25 passed |
| Route enumeration (`test_student_context_api.py`) updated as a reviewed change | passed |
| Frontend vitest: UNKNOWN-is-not-FAILED invariants, every enum has words, placement-reason sentences, dated availability, schedule display arithmetic, presenters rendered to static markup | 31 new (61 total) |
| `tests/visual/core.mjs`: Home, Degree, what-if, Plan, Schedule, errors, loading, pending program, mobile navigation, sign-out; light and dark; 320–1600 px; axe, overflow, touch targets | **80 screens, 0 findings; 32/32 assertions** |
| `scripts/inject_core_failures.py` | **16/16 DETECTED** |

**Final regression** (HEAD `44e02d3`; `coursepilot_test` on PostgreSQL 16; full logs kept):

| Suite | Before (main `110c60f`) | After |
|---|---|---|
| Frontend typecheck / vitest / production build | clean / 30 passed / built | **clean / 61 passed / built** |
| Root (PG) | 1867 passed, 5 skipped | **1892 passed, 5 skipped** |
| Backend (PG; Degree, Planning, Schedule API and domain, onboarding, auth) | 937 passed, 4 skipped | **962 passed, 4 skipped** |
| Ingestion PG: planning engine, schedule engine, linked components (real SOC fixtures) | - | **68 passed** |
| Migration `edd2bebed386`: upgrade, downgrade, re-upgrade, `alembic check` on a throwaway database | - | **OK** |
| Failure injection, 7.2 core | - | **16/16 DETECTED** |
| Failure injection, 7.1 auth | 18/18 | **18/18 DETECTED** |

**Not rerun, with reasons:**

- The full ingestion suites (SQLite, retrieval) and the 6.8, 6.9 and 7.0
  injections: none of the code they exercise changed. The only change near
  the planner is a one-function refactor of the dataset fingerprint, covered
  by the planning engine and API suites above.
- The secrets/PII scan of the full 7.2 diff found no credential values, email
  addresses, token or authorization-code patterns, or 9-digit identifiers.
  The only env file in git is `.env.example`.

## Limitations

- **Spring 2027 sections are not loaded** (Rutgers had not published them).
  A plan's Spring 2027 semester cannot be scheduled yet; the page says so.
- **No hand-editing of plans.** Students change the settings and regenerate.
  There are no pins or exclusions, because the engine exposes no
  validate-an-edit operation.
- **One saved plan per student,** with no history.
- **Requirement explanations are the engine's English sentences,** not
  structured codes.
- **Blocked-course lists inherit the engine's cap** of 10 courses per issue.
- **Course search, Notifications and the assistant** remain "later". The
  grounded explanation endpoint is unchanged and not surfaced.
- **Programs:** CS BA is published. The others are pending review and say so
  everywhere.
- **Phase 7.0 transcript and 7.1 authentication limitations carry over:**
  - transfer, AP and retake layouts are unverified;
  - there is no OCR;
  - no production identity provider is configured;
  - no ScarletMail `hd` rule exists.

## Not done (by design)

- No LLM in any academic decision, plan or schedule.
- No WebReg automation, no authenticated Rutgers access, no registration
  claims.
- No academic logic in TypeScript: nothing on the frontend decides
  requirements, eligibility, validity, conflicts or components.
- The PR is not merged.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
