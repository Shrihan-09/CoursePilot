# Anchor retake urgency to plan start and expose My records

Retakes now count as blocking when they unlock a course needed within the first two Fall/Spring terms from the plan's start (including its starting term). Previously the window moved with the ASAP retake offering, making later dependencies appear urgent. A Spring 2027 start now counts Spring/Fall 2027; a Spring 2028 dependency does not default the retake to ASAP.

Added `/records` as **My records**, directly below the `/profile` (Progress) navigation entry, in both the Academics sidebar and mobile More menu.

## Validation

- `ingestion/tests/test_retake_timing.py`: **15 passed**, including the updated real planning scenario and boundaries for the first two regular terms, later/unplaced dependencies, and absent ASAP offering evidence.
- Ran ingestion's session fixtures through a temporary PostgreSQL adapter using an isolated schema inside `coursepilot_test`; schema, adapter, and test temporary folder removed afterward. No dev test connections.
- `frontend/tests/unit/setup-followup.test.tsx`: **13 passed**.
- `npm.cmd run typecheck`: passed.
- `git diff --check`: passed. No full test suites run.

## Dev database follow-through after Task 3 merge

- Main updated to merged commit `784df4a` before this branch was created.
- Fresh backup: `C:\coursepilot-private\backups\coursepilot_pre_main_upgrade_20261107_223748.dump` (**25,635,472 bytes**).
- Dev successfully upgraded `t2quest02 -> p10a01 -> p10a02 -> t3dn01`; Alembic current reports `t3dn01 (head)`.
- Backend started cleanly from the existing venv on port 8001. `/api/v1/health` returned `status: ok`; `/api/v1/ready` returned `status: ready`, database `ok`, pgvector `ok`.
- Docker backend image was absent, so verification used the venv with an explicit Windows selector event loop; no image build was performed. Verification process stopped afterward.

## Migrations and data

No new migration or backfill. Owner review and merge only.
