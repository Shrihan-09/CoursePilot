# Phase 6.8: Section Monitoring & Notification Event Infrastructure

## Summary

A student can ask CoursePilot to **watch** a section. The server keeps that promise with the browser closed or the app terminated.

```
Rutgers --ONE request per term per refresh--> availability refresh (6.7)
                                                   |  closed -> open
                                      event processor (set-based, idempotent)
                                                   |  x active watches of that section
                                      notification_event (one per watch + opening)
```

- **Watches never add Rutgers requests.** It is 1 per refresh with 0 or 1,000 watches, and tests and the benchmark both prove it.
- **No events from guesses.** Only an observed **closed → open** creates an event. A first observation or unknown → open never does.
- **One event per watch per opening.** A database unique key and a per-change dispatch marker guarantee exactly one durable logical event per (watch, opening). This survives crashes, reruns and concurrent workers (tested on real PostgreSQL).
- **Out of scope:** no delivery (push/email/SMS), no credits, no payments.

## Design (reconciled from four read-only investigations)

- **Target:** one term-scoped section (`course_section.id`, with the term and index kept beside it). Bundle and course watches are later types, because SOC links no lecture to a lab, and "usable" mixes eligibility, restrictions and fit.
- **Owner:** `user_account`, so watching needs no linked academic record.
- **Lifecycle:** watches are deactivated, never deleted. The reasons are `user_cancelled`, `term_ended` and `target_removed`. One active watch per (user, term, index), enforced by a partial unique index.
- **Linked components:** an opening reports its required LB record's state and open/total count. "Lecture opened, every lab closed" is explicit, never "course available". An academic co-requisite (229 → 227) stays a separate course with no component context.
- **Removed sections:** ingestion never deletes them. Removal is detected as "not in the term's newest *complete* download". Such a watch can't be created, and an existing one becomes `target_removed` and is never retargeted.
- **Progress tracking:** a per-change dispatch marker, not a time cursor. A cursor would skip late-committing refreshes and replays.
- **Phase 6.7 fixes found here:**
  - `apply_refresh` now takes a per-term PostgreSQL advisory lock. Concurrent refreshes could otherwise record the same opening twice.
  - DATA_MODEL §43.9 wrongly said a later download removes cancelled sections; corrected.

## API

| Method | Path | Notes |
|---|---|---|
| POST | `/api/v1/watches` | `{term, index_number}`. Idempotent: returns `{created, watch}` |
| GET | `/api/v1/watches` | `?include_inactive=` |
| DELETE | `/api/v1/watches/{id}` | Idempotent. Another account's watch is a 404 |
| GET | `/api/v1/watches/events` | `?limit=` |

- Structured responses throughout, with no prose to parse.
- Identity comes only from the credential.
- Logs never carry a term, index or course.

## Schema (migration `72d61aa87031`, additive)

`section_watch`, `availability_change_dispatch`, `notification_event` (UNIQUE `watch_id, change_id`), plus a partial index of openings on `section_availability_change`. Upgrade → downgrade → re-upgrade on the dev and test DBs; `alembic check` is clean.

## Measured

Full Fall 2026 term; one refresh opens 5 sections (SYNTHETIC); every case is rolled back (`scripts/benchmark_monitoring.py`).

| Watches | Rutgers requests | Events | Process time | Process SQL |
|---|---|---|---|---|
| 0 | 1 | 0 | 32 ms | 4 |
| 1 | 1 | 1 | 43 ms | 15 |
| 10 | 1 | 10 | 172 ms | 51 |
| 100 | 1 | 100 | 250 ms | 51 |
| 1,000 | 1 | 1,000 | 943 ms | 51 |

- **Interactive calls:** creating a watch takes about 12 ms (p50); listing watches or events takes about 1.3 ms.
- **Row sizes:** a watch row is 112 B and an event row 208 B.
- **Storage estimate:** about 0.9 GB per term at 100k users (5 watches per user, 3 openings per watched section per term).

## Tests

| Suite | Baseline | Final |
|---|---|---|
| Root (PG) | 1640 passed, 5 skipped | **1684 / 5** |
| Backend | 790 / 4 | **795 / 4** |
| Ingestion PG | 850 / 1 | **889 / 1** |
| Retrieval | 46 | **46** |
| Ingestion SQLite | 785 / 66 | **818 / 72** |

There are 0 failures. The 6 new SQLite skips are the real-PostgreSQL tests, which skip when no PG is configured.

- **`test_section_monitoring.py` (33, real Rutgers structure).** It covers the full matrix A–Q:
  - every transition type, reruns, deactivation, cross-term index reuse;
  - lecture/lab context, the 229/227 co-requisite, removed sections (via a newer complete download through the real pipelines) and deleted sections;
  - ownership, server-side processing with no client, determinism, read-only schedule generation, and processing cost against watcher count;
  - Rutgers request count.
- **`test_monitoring_postgres.py` (6, real PostgreSQL, separate connections and threads).**
  - 2 and 4 concurrent workers produce exactly one event per watch;
  - 6 concurrent identical watch requests produce one watch;
  - a **forced** refresh interleaving proves the advisory lock;
  - crash before commit loses nothing.
- **`test_watch_api.py` (5).** Create/list/deactivate/events shapes; account A vs B isolation (a 404 for another account's watch); 422s for any owner field; 401 without auth; reads write nothing; logs carry no term, index or course.
- **Failure injection:** 6.8 caught 16 of 16. The prior suites caught 47 of 47 (6.7: 17, 6.6: 16, 6.6.1: 14).
- **Migration:** upgrade → downgrade → re-upgrade on the dev and test DBs, and `alembic check` is clean.

**Correction to the Phase 6.7 report:** the ingestion `session` fixture is always in-memory SQLite, so 6.7's real-data availability tests ran on SQLite only, not PostgreSQL as reported. 6.8 adds genuine PostgreSQL coverage for the paths where the two databases differ (locking, ON CONFLICT, partial unique indexes).

## Not in scope

APNs, FCM, browser push, email, SMS, device tokens, read state, credits, prices, payments, subscriptions, prediction, WebReg and LLMs.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
