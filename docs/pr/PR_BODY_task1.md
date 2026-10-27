# Task 1: science-sequence alternatives (generic one-of commitments)

Branch `feature/science-sequence-alternatives` → `main`. It recovers the interrupted Task 1 work, audited first, and applies the owner's decisions from the audit.

## The bug

For the CS B.S. science requirement (choose ONE sequence), the planner put Chemistry (01:160:161) and Physics (01:750:193) in the same Spring 2027 term. A generic `any_of` with no progress kept every alternative open.

## What changed

**Generic one-of commitments** (`planning/engine.py`; no major, subject or course literals; pinned by a test).

The flow is: requirement → valid alternatives → planner recommendation (or the student's explicit choice) → selected alternative → plan.

- **One child per choice.** An `any_of` opens only the children it still needs. The unchosen competitors are closed in both the planner and the audit (`excluded_requirements`, planning scenarios only).
- **Ranking.** Each alternative gets an estimated completion term from a branch-only replay of eligibility and projected audits. Unknown rules never become feasible in that replay. Alternatives are ranked: already started → soonest → extra requirement coverage → catalog order.
- **Exposed** in `metadata.alternative_choices[]` (`AlternativeChoice`):
  - `status`: `undecided` / `planner_recommended` / `student_selected` / `needs_reselection`;
  - `chosen`;
  - `recommended`, with its `recommendation_reason`;
  - `selected` (the student's request);
  - `unavailable`;
  - `alternatives`;
  - `fallback_from`.
- **A student's explicit choice is never switched.** If it can't be completed, up front or during construction, it is held as `needs_reselection`, with the `ALTERNATIVE_NEEDS_RESELECTION` needs-confirmation issue. Only a planner recommendation may be re-planned with another branch, and that case is flagged.
- **Not persisted per student.** The choice is still request-level (`PlanningConstraints.alternative_selections`); Task 6 owns persistence and UI.

**Prerequisite notes** (`domain/conditions.py`, parser 4):

- **What's enforced.** A plain OR-list course note that names a *subset* of the published prerequisite is enforced as an extra any-of (e.g. 01:160:171: "Prerequisite 01:160:159 or 01:160:161 or 01:160:163").
- **What's left alone.** A list naming any course outside the published prerequisite, or attached to a row with no published prerequisite, stays uninterpreted. That caps the course at UNKNOWN and never enforces the list.

**Speed** (`audit/optimizer.py`, plus planner caches). The cost was not the alternatives logic: on main, plans already took up to 38 s. Two exact changes fix it, plus two caches:

1. **Incremental satisfaction in the allocation search.** The leaf no longer rescans the allocation once per requirement per objective component. That rescan was the measured hot spot, over 80% of plan time.
2. **A component memo.** A component solve is a pure function of its requirements, courses, natural order, objective, bound and visible baseline. Placing one course changes only its own component, so the others repeat exactly.
3. **Lookahead caches.** One dependency planner is shared per choice, and branch estimates are cached within one `plan()` call.

Same allocations, same optimum: a randomized test compares against a full scan and a fresh solve.

**Other**

- **`plan_quality.py`** leaves unselected, inactive branches out of "remaining", and times only `generate_plan`. It used to include the JSON reporting in the measured time.
- **`test_core_needs_no_new_tables`** was failing on main because of Phase 7.6's `offering_core_code`. That table stores per-offering SOC facts, not Core requirements, so the test now names it as an exception and says why.
- **Failure injection** (`inject_recommendation_failures.py`):
  - a fresh `--basetemp` outside the repo for each fault;
  - tests run against the test database explicitly;
  - only assertion failures count as detection; setup errors mean INVALID;
  - new faults Q (explicit choice ignored) and R (unparseable prerequisite treated as satisfied).
- **Hygiene.** `scratchpad/` is ignored, the PR bodies moved to `docs/pr/`, and the owner's private previews moved out of the repo.
- **Backlog.** `docs/NEXT_TASKS.md`: section-level narrowing notes (160:160 prerequisite, 160:162 C minimum) and the missing "major courses per term" setting.

## Data

- **Backup first:** `coursepilot_pretask1_202610271413.dump`, taken before any database change. It's verified readable (58 table-data entries) and stored in the private backups folder.
- **`reparse_conditions.py`:**
  - dry run: 3,555 rows to bump to parser 4, 95 semantic changes;
  - applied, and the re-run changes 0 rows (idempotent);
  - of the 179 narrowings the first version applied, **84 remain**;
  - **95 were reverted**: 58 name a course outside the published prerequisite (e.g. 07:700:303's note names 07:701:176), and 37 have no published prerequisite expression. Each case is in `docs/investigations/evidence/task1-reparse-narrowings.json` (public catalog data only).

## Tests

- **New:**
  - `backend/tests/test_requirement_alternatives.py`: 16 tests;
  - `ingestion/tests/test_science_alternatives.py`: 7 tests on a real public SOC fixture with a fabricated student;
  - an optimizer exactness test.
- **They cover:**
  - zero progress opens only one alternative;
  - a started alternative is continued (unit and real data);
  - an explicit choice wins, and its competitors are excluded;
  - an impossible choice → `needs_reselection`, never switched (unit and real data);
  - `undecided`;
  - the recommendation reasons;
  - Chemistry and Physics never both appear in a B.S. plan;
  - unparseable prerequisites and unsupported corequisites stay UNKNOWN;
  - no major-specific code.
- **Failure injection: 18/18 detected by assertion failures, 0 setup errors.**
- **Full suites:** backend 1,136 passed / 4 skipped; ingestion 1,085 passed / 1 skipped (0 failed, including the fixed core-table test); frontend 97 passed, `tsc` clean.

## Plan quality and speed (`plan_quality.py`, 24 cases per program)

| | main (`74c3900`) | this branch |
|---|---|---|
| B.A. full coverage | 18/24 | 18/24 |
| B.S. full coverage | 3/24 | **17/24** |
| B.S. extra science alternatives planned | 57 | **0** |
| B.S. mean credit deviation | 1.76 | **1.52** |
| B.A. plan time p50 / max | 6.6 s / 38.0 s | **4.9 s / 10.4 s** |
| B.S. plan time p50 / max | 7.4 s / 25.7 s | **6.6 s / 17.2 s** |

The merge gate (p50 < 10 s and max < 20 s) is met for both programs.

None of the 7 remaining partial B.S. plans is about science; each commits to one sequence. They come down to:

- Core goals (College Writing, HST, SCL, CCO/CCD) and CS electives with no offering evidence for the planned terms, or with prerequisites CoursePilot can't evaluate;
- graduation targets too short for the remaining work: a third-year student on "finish earlier" gets only 2 terms.

## Please check

- Whether **`needs_reselection`** says enough to the student before Task 6 builds the choice UI.
- The **37 reverted rows with no published prerequisite.** Their note *is* the only prerequisite statement. They are UNKNOWN now; enforcing them would need its own rule.
- The six fabricated `user_account` rows are **deleted** from the dev database (see the follow-up below).

## Follow-up commit: owner decisions

- **Dev database cleaned.** The 6 fabricated `user_account` rows that an unscoped test run wrote are **deleted**; `user_account` went from 16 to 10. No account-linked student was touched, and the pre-task backup predates both the rows and the deletion.
- **Test-database guard** (`app/db/test_db_guard.py`):
  - Installed by the backend and ingestion conftests before any engine exists.
  - It hooks SQLAlchemy's pool `connect` event, so it covers every engine in the test process: fixtures, the app's own sessions and scripts.
  - It refuses, and closes, any PostgreSQL connection whose database name doesn't end in `_test`, with a message that names the database and the environment variables to set. SQLite test databases are allowed.
  - `backend/tests/test_test_db_guard.py` (12 tests) covers the name rule, every driver connection shape, a real connection to the dev database refused before any statement, and a real connection to `coursepilot_test` allowed.
- **Backlog** (`docs/NEXT_TASKS.md`):
  - Major courses per term is **Task 2**: a default from the workload level (Light 1–2, Balanced 2–3, Semi-rigorous 3, Challenging 3–4), counting any course toward the major including required math and science, with a per-semester override. Not built here.
  - Enforce the 37 section-note-only prerequisites (10 courses), labelled "from section note (Rutgers SOC)", with the same parse and unknown rules.
- **No change:** the `needs_reselection` wording stays until Task 6.
- **Suites with the guard:** backend 1,148 passed / 4 skipped (+12 guard tests); ingestion 1,085 passed / 1 skipped. No test connected to a non-`_test` database. The frontend is unchanged (97 passed).

🤖 Generated with [Claude Code](https://claude.com/claude-code)
