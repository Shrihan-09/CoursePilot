# Phase 6.9: CoursePilot Live: Production Scheduler, Inbox, Delivery Outbox & ActivityKit Contract

## Summary

The 6.7 refresh and the 6.8 watch/event engine are now a production notification pipeline with three surfaces built on one backend:

```
Rutgers ── ONE request per active term per interval (lease-guarded) ──► 6.7 refresh
  ──► 6.8 event (immutable) + inbox item (same txn) ──► fan-out ──► outbox
  ──► provider: fake (tests) | apns (alert push · ActivityKit Live Activity update)
```

> A CoursePilot opening notification reports an **observed** Rutgers availability change. It does not guarantee eligibility, schedule compatibility, or successful registration.

**What was tested, and how:**
- **Implemented and tested locally:** everything in this PR. Tests run on SQLite and against the real test PostgreSQL database.
- **Real PostgreSQL concurrency** (separate connections and threads):
  - scheduler leases and fencing;
  - delivery claims with 2 and 4 workers;
  - retry races and crash recovery;
  - watch cancellation, token rotation and invalidation.
- **APNs contract (mock transport; FakeProvider for the outbox):** the exact request is checked: path, headers, ES256 JWT signed with a throwaway key, and payload.
- **A physical iPhone: none.** No Apple credentials and no iOS app exist. No Live Activity or push has reached a device.

## Design (reconciled from five read-only investigations)

- **Scheduler.** `live_cli run` claims a per-term **lease** *before* the Rutgers fetch, then fences the apply. The 6.8 advisory lock only de-duplicated transitions, not requests. When a term is due comes from the refresh log, and fetch failures back off exponentially (capped).
  - **Active terms:** come from `availability_poll_schedule`, managed by the CLI. Nothing is hard-coded.
- **Inbox.** One `notification` row per event, created in the 6.8 transaction. Read state lives there, never on the event. Paging is newest-first by keyset.
- **Outbox.**
  - One delivery per (event, endpoint, channel), enforced by a unique key.
  - Claim with a lease and SKIP LOCKED. Re-check the watch, endpoint, activity, account and TTL immediately before sending. Send with no transaction open. Record the result fenced on the claim token.
  - Retries follow Apple's response table: bounded backoff that honours Retry-After; invalid tokens are revoked; permanent errors are never retried.
- **Endpoints.** One generic `delivery_endpoint` table: APNs device, Live Activity token, push-to-start (stored only), FCM and web push (reserved).
  - Raw token plus a hash under a global unique index; responses and logs only ever see a fingerprint.
  - Reassignment when a phone changes accounts; rotation per ActivityKit's `pushTokenUpdates`.
- **Live Activities.** Registered per owned watch and bounded to 8 hours. The opening is sent as an `event: end` with the OPEN state and a dismissal after 30 minutes.
- **Decisions:**
  - **Targeted updates, not broadcast.** Broadcast is iOS 18+ only, gives every subscriber the same state and gives no per-device feedback; Apple recommends targeted updates for one-person updates.
  - **No push-to-start yet.** Apple warns against surprise starts. The token kind is stored as an extension point.
  - **No double alert** (Apple HIG). With an active Live Activity, the ordinary push is recorded as `suppressed: live_activity_alerted`.
- **Security.**
  - Ownership comes only from the credential, and every owner field is a 422.
  - Another account's resources are 404.
  - Tokens never appear in responses, repr, errors or logs. httpx URL logging is silenced and its exceptions are translated.
  - The APNs key goes in SecretStr or a key path, and `*.p8` is gitignored.
  - Registration has its own rate limit.

## Measured

Full real Fall 2026 term (11,992 sections) in the disposable test database; one SYNTHETIC opening; FakeProvider (`scripts/benchmark_live.py`).

| Watchers (each with an endpoint) | Rutgers requests | Refresh SQL | Events SQL | Fan-out SQL | Delivery SQL |
|---|---|---|---|---|---|
| 1 | 2 | 15 | 16 | 7 | 8 |
| 10 | 2 | 15 | 16 | 7 | 35 |
| 100 | 2 | 15 | 16 | 7 | 305 |
| 1,000 | 2 | 15 | 16 | 13 | 3,032 |

- **Rutgers requests stay constant:** 2 at every scale (the first observation plus the opening poll). An idle tick makes 0 requests.
- **Events and fan-out are set-based:** their SQL does not grow with watchers.
- **Delivery is one provider call per endpoint, by design.** A one-query re-check immediately before each send halved its statement count (6,032 → 3,032 at 1,000 deliveries).
- **Throughput:** one worker sent 37–87 deliveries per second through FakeProvider (wall clock was noisy on this laptop). Inbox listing and the unread count take about 1–4 ms.
- **Not measured:** real APNs latency, because there are no credentials.

## Tests

| Suite | Baseline (clean main) | Final |
|---|---|---|
| Root (PG) | 1684 passed, 5 skipped | **1765 / 5** |
| Backend | 795 / 4 | **836 / 4** |
| Ingestion PG | 889 / 1 | **929 / 1** |
| Retrieval | 46 | **46** |
| Ingestion SQLite | 818 / 72 | **849 / 81** |

There are 0 failures. The 9 new SQLite skips are the real-PostgreSQL tests, which skip when no PG is configured.

**SQLite tests** (`test_coursepilot_live.py`, 31, real Rutgers structure):
- inbox: listing, paging and read state;
- endpoints and Live Activities;
- the scheduler: due, backoff, inactive terms, failures surfaced;
- fan-out, retries and the watch-cancellation race;
- read-only schedule generation.

**Real PostgreSQL tests** (`test_live_postgres.py`, 9, separate connections and threads):
- 3 schedulers make 1 Rutgers request;
- a worker that lost its lease writes nothing;
- 2 and 4 delivery workers each send every delivery exactly once;
- retry races;
- crash and lease recovery;
- the cancellation race;
- token rotation and invalidation.

**APNs contract** (`test_live_domain.py`, 28; fake provider and mock transport):
- Apple's response table;
- the request's path, headers and ES256 JWT, signed with a throwaway key;
- payload limits;
- no token in logs, errors or repr.

**API** (`test_live_api.py`, 13, PostgreSQL):
- account A vs B isolation (404);
- every owner field is a 422;
- 401 without auth;
- tokens never echoed;
- the registration rate limit;
- admin-only health.

**A physical iPhone: none.** No Apple credentials and no iOS app exist.

**Failure injection: 92 of 92 detected.**

| Matrix | Detected | Notes |
|---|---|---|
| 6.9 | 29 / 29 | A–T plus 9 found during implementation |
| 6.8 | 16 / 16 | Detectors G and N updated for code moved in 6.9; M and N re-verified after the test fix below |
| 6.7 | 17 / 17 | |
| 6.6 | 16 / 16 | |
| 6.6.1 | 14 / 14 | |

**Weak tests found and fixed while running the matrices:**
- **6.9:** three faults were missed at first.
  - **N:** the event was read at the same instant it was created, so the test couldn't see a mutation.
  - **AC:** no test covered a Live Activity when its watch was cancelled.
  - **B:** only one of two term guards was removed, so the fault didn't really remove term scoping.
- **6.8:** a test counted every `INSERT INTO NOTIFICATION…` and so started catching the new inbox insert. It now counts only the event insert.

**Migration** `e26b2e81f400`: upgrade → downgrade → re-upgrade on the dev and test DBs, and `alembic check` is clean. The backfill was verified on populated data (3 events → 3 inbox items, none pushed).

## Production setup still required

- **Apple account setup:** an Apple Developer account, the APNs `.p8` key, Key ID, Team ID and bundle id, kept outside the repository.
- **Configuration:** `PUSH_PROVIDER=apns`. `httpx[http2]` is declared; `h2` is not installed in this dev environment.
- **The iOS app:** an ActivityKit `ContentState` mirroring `opening_state` v1, and `Localizable.strings` for `CP_SECTION_OPENED_*`.
- **A sandbox device test.**
- **Run the worker:** `live_cli schedule add --term …` and the `live-worker` compose service. The compose config is validated, but the image was not built in this phase.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
