# Phase 7.3: course demand collection (collect only)

Branch `feature/phase-7-3-course-demand-collection` → `main`.

## What changed and why
The goal is to record how fast sections fill before Spring 2027 registration opens, so that a later demand comparison has real data. This PR adds no ranking, labels or UI.

- **Change log reused instead of a new table.** `section_availability_change` already writes one row per open/closed transition from the existing openSections refresh. DATA_MODEL 50.1 maps the spec's `section_status_change` columns onto it: `from_status`/`to_status` for from_open/to_open, and `refresh_id` → `availability_refresh` for data_source_id. Writing it still costs no extra Rutgers request.
- **Window-aware cadence** (`app/core/registration_windows.py`, `next_due`).
  - 90 s inside a term's registration window, 30 min outside it. Either way, one openSections request per term per poll.
  - Backoff never polls faster than the cadence.
  - Spring 2027 window: Mon Nov 9 2026, 09:00 → Fri Nov 20, 02:00 (America/New_York). Source: https://nbregistrar.rutgers.edu/undergrad/S27prereg.htm (updated Oct 15, 2026).
- **`course_demand_snapshot`**: one row per course per term per Eastern day.
  - Columns: section_count, observed/open/closed counts, closed_share, and per-section `first_closed` after the window opens. Each first-closed time is bounded by the previous refresh (`not_before`).
  - Also records refreshes that day and the longest refresh gap.
  - Days with no accepted refresh, and courses with no observed section, are `insufficient_data`. Their measured values are NULL, enforced by a CHECK constraint, and never imputed.
  - Upserted on (term, course, day), so it is idempotent.
- **Rollup triggers.** The live worker runs the rollup hourly from the database only. The manual command is `live_cli demand [--term T --date D]`, and it refuses days that aren't over.
- **Fix: `backend/Dockerfile` now installs the `postgres` extra.** Without it the `live-worker` container crashed on start (`No module named 'psycopg'`), so collection could not run in Docker at all.

## Tests
- New `ingestion/tests/test_course_demand.py` (14 tests). It uses real Fall 2026 SOC and openSections fixtures, with SYNTHETIC transitions and windows. It covers:
  - rollup math (closed_share 40/41, the three first-closed kinds, refresh gap);
  - insufficient data, both reasons;
  - DST-correct Eastern days;
  - idempotent change writes and rollups;
  - due-day selection;
  - cadence (90 s / 30 min / slower operator setting);
  - the configured Spring 2027 window;
  - a simulated 2-hour worker run: exactly 40 requests in the window hour and 2 outside it, all openSections for the one term, with the demand rollup making none.
- Existing live tests now pin a synthetic in-window period, so they keep testing the 90 s cadence and backoff.
- Full suites: backend 1,072 passed / 4 skipped; ingestion 1,057 passed / 1 skipped (after adding the in-window fixture to `test_live_postgres.py`, whose two lease tests assumed 90 s everywhere). Ruff clean on changed files.
- Migration round-trip and `alembic check`: clean.

## Migrations
`73demand01`: an additive `course_demand_snapshot` table. The downgrade drops it; every row can be rebuilt from the change log.

## Please decide / check
- The add/drop window after Nov 19 is **not** configured: I found no readable source giving its dates. Add it to `REGISTRATION_WINDOWS` once the Registrar publishes it.
- Winter 2027 (20270) is scheduled alongside Spring 2027 (20271) because it registers concurrently. It has no configured window, so it polls every 30 min.
- The worker runs on your laptop. While the laptop sleeps, those hours are a data gap: `insufficient_data` for days missed entirely, and wider `not_before` bounds otherwise. Nothing is backfilled.

## Data / backfill steps (per environment)
1. `alembic upgrade head`.
2. Schedule terms: `live_cli schedule add --term 20271` and `--term 20270`.
3. `docker compose up -d --build live-worker`. The rebuild is needed for the Dockerfile fix.
4. Optional: `live_cli demand` rolls up completed days right away.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
