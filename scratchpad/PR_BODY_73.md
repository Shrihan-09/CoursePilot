# Phase 7.3: upcoming terms, current-term intelligence, workload levels

Branch `feature/phase-7-3-csp-dn-discovery` → `main`. Contains the unmerged Phase 7.2.4 work
(`feature/phase-7-2-4-current-term-intelligence`) it was built on.

## What changed and why

**Terms and offerings**
- SOC term watcher (`term_watch_cli`, live worker): loads each term when Rutgers publishes it; coverage stays `partial` until stable across fetches. Spring 2027 loaded; 18 historical terms (Spring 2021 – Summer 2025) backfilled for offering evidence only.
- Prerequisite rules come only from each course's most recent loaded term (history was applying 2021–2024 rules).
- A published term (non-empty SOC payload, any coverage) places only courses it lists; history is used only for unpublished terms and labelled "historically offered — not yet confirmed". A full manual CLI load now registers the term like the watcher (Spring 2027 had been invisible to the registry).

**Prerequisites and eligibility**
- Section-level notes ("MUST REGISTER FOR 640:001:20") are scheduling constraints, not corequisites.
- "Any Course EQUAL or GREATER Than (X)" read literally (same unit+subject, number ≥ X); unblocks 72 courses (Gen Physics, Linear Algebra, CS 111…). Prerequisite parser v2.
- Optional math placement on the profile; placement meets prerequisites of the placed course and everything below it (Rutgers Math Department policy, cited).

**Planning**
- Never the same class twice (cross-listed twins); topics courses show the term's topic and at most one per term.
- Workload levels (Light / Balanced / Semi-rigorous / Challenging) with a published-facts workload weight; fill order anchors → multi-requirement → one major course → fillers by delivery preference; pinned courses as hard constraints with explanations.
- Graduation target counted from the student's entry term (On time 8 / Early 7 / No rush 10) or an optional expected graduation term; warns instead of extending the plan.
- `ANY_OF` with no progress no longer opens only its alphabetically first alternative.

**Programs**
- CS B.S. encoded from the catalog (pending human review).
- CS electives (B.A. and B.S.) are the department's designated list (55 courses) instead of `{198, ≥300}`.
- SAS Core attaches at the school level; no program embeds a copy; other schools never get it.

## Tests
- Backend 1,072 passed / 4 skipped; ingestion 1,043 passed / 1 skipped (after fixing the 6 tests that encoded the old elective query); frontend 80 passed, `tsc` clean.
- New suites include: term watcher, published-term rule, duplicates/topics, math placement, workload levels (scenario: Calc II retake + Gen Physics with lab + 1 CS + async filler = 16 credits, workload 22.0/24), graduation target (class of 2028 → Spring 2027, Fall 2027, Spring 2028).
- Migration round-trip and `alembic check` clean.

## Migrations
`73termwatch01`, `73placement01`, `73workload01`, `73gradterm01` (all additive).

## Please decide / check
- CS B.A. is now `needs_rereview` (elective list change) and CS B.S. is pending review — both need a named human review (`registry_cli review`).
- The department electives page is undated; confirm it applies to 2026-2027. "At most one 493/494" is recorded but not yet modeled.
- Plan time p50 is 2.6–8.3 s (target < 3 s), recorded as a known issue.
- Saved plans are marked out of date (planning engine 7.3.2) and need regenerating.

## Data / backfill steps (per environment)
1. `alembic upgrade head`.
2. Register/load terms: `python -m coursepilot_ingestion.cli --year 2027 --term 1 --stage both --all` (registers the term), or run `term_watch_cli run`.
3. Re-read prerequisites/corequisites with parser v2: `python scripts/reload_corequisites.py`.
4. Load CS B.S. and the elective list: `registry_cli load`, `link`, `validate`, `packets`.
5. Load SAS Core at the school level (Core pipeline with the combined SOC archive) and remove any program-embedded Core copies (dev data had them on CS B.A. and Math B.A.).

🤖 Generated with [Claude Code](https://claude.com/claude-code)
