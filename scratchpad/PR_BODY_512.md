# Phase 5.12 — Real Provider and Deployment Verification

## Summary

This phase's output is mostly **evidence**, and its most important content is
the two entries that could not be turned green.

**Not externally verified, with specific reasons:**

- **Anthropic** — `ANTHROPIC_API_KEY` is empty and absent from the environment.
  No credential was fabricated, no call was made, no live test marked passed.
  The `CLAUDE_*` variables present belong to the tool running this session, not
  to CoursePilot; using one would have been manufacturing a credential.
- **Rutgers SSO** — no client registration and no credential exist, and Rutgers
  publishes CAS/Shibboleth/LDAP rather than an OIDC endpoint to register
  against. An access fact, not an implementation failure.

**What was pushed as far as it goes:** the OIDC *protocol* is now fully verified
against a **real HTTP issuer**, leaving only the Rutgers-specific part unproven.

## Two real findings

### 1. Production ran in debug — and echoed SQL parameters

`debug: bool = True` with nothing lowering it for production. Two consequences a
deployment would have inherited silently:

- `/docs` served publicly;
- **`create_async_engine(echo=debug)`** — SQLAlchemy logs every statement *with
  its parameters*. Verified: a query bound to a catalog year emitted
  `{'y': '2026-2027'}` into the log. In a deployment that stream would carry
  `external_ref`, account ids and academic values.

Phase 5.10 spent a whole phase ensuring observability didn't become a second
source of sensitive data. This would have undone it — through a **default nobody
revisited**, not a mistake anyone made.

Fixed by forcing `debug = False` in production. Plus `audit_production_settings()`
reporting, at startup, what it doesn't enforce — naming settings, never values.

### 2. Key rotation is not instant

A newly published signing key is **refused for up to 30 s**, because PyJWKClient
suppresses refetch inside its `cooldown_duration`.

My first test asserted rotation was instant and failed. **The test was wrong, not
the library** — the required property is that a new key *eventually* verifies.
The cooldown is a DoS protection (without it, random `kid`s force unbounded JWKS
fetches), so it's documented and the test corrected rather than the protection
disabled.

Also measured: the JWKS fetch timeout is an inherited `30 s` default, not a
decision.

## What a real issuer found that a stub could not

Phase 5.4's `StaticKeyVerifier` is handed a key object directly — it never
exercises `PyJWKClient`, the HTTP fetch, the JWKS format, `kid` selection or
refresh-on-unknown-kid. A new harness serves real RSA keys over a real socket.

27 tests: happy path with a genuine fetch, minimal claims, and rejection of an
unpublished key, wrong issuer/audience, expired, not-yet-valid, missing subject,
`alg:none`, malformed, and no-`kid`. JWKS faults (500, malformed, empty, dead
socket) all fail closed; **a cached key keeps verifying through an outage** with
signature/issuer/audience/expiry all still checked.

## Cross-process, with real processes

Phase 5.11 *documented* "one index per process". A thread shares the registry, so
that was untested. With real subprocesses: two workers each build once and derive
identical tokens independently; a catalog write in one process is detected by
another with no shared memory and no restart; three simultaneous builders agree
on version, count and ranking.

It also verifies the **limitation**: three workers each report `builds == 1` —
aggregation would have made later ones report more.

## Metrics decision (Part 8)

Kept in-process/in-memory. **No backend built** — that would turn a verification
phase into an infrastructure project. Revisit trigger is operational: the first
time an operator must SSH to several workers to add up a hit rate.

## End-to-end ledger

```
REAL       PostgreSQL, Degree Engine, audit cache + triggers, BM25 lifecycle
           + triggers, ownership, explanation validation, correlation, metrics
SIMULATED  the AI provider (no credential — deterministic path is the authority)
SIMULATED  the authenticated principal (no registration) — though the OIDC
           verifier is exercised for real in test_oidc_live_path.py
```

Asserted: 5 distinct correlation ids, audit cache miss→hit with identical bodies,
**BM25 built once across two explanation requests**, no `external_ref` or subject
in any response.

## Performance (real dev-database copy, 4,415 courses)

```
401 unauthenticated                mean   3.20  p50   3.05  p95   4.22
GET /student/context               mean  24.19  p50  23.95  p95  28.13
GET /student/audit  cache HIT      mean  18.07  p50  17.69  p95  21.31
GET /student/audit  cache MISS     mean  53.41  p50  52.16  p95  67.22
POST /explanations (deterministic) mean  77.78  p50  78.01  p95  87.71

search_index_builds 1 | reuse 22 | audit cache 27/14 | hit rate 0.659
```

## Tests

```
PostgreSQL   619 passed,  1 skipped   unchanged
SQLite       554 passed, 66 skipped   unchanged
Backend      418 passed,  3 skipped   was 365; +53
retrieval     46 passed               unchanged
alembic check                         clean — no migration added
```

3 consecutive clean backend runs. Phase 5.10 mutation leak tests re-run and still
catch a reintroduced raw identifier.

## Deployment state

The dev database was one migration behind — Phase 5.11 applied only to copies.
Applied and verified: `2b11bfbd8874 → ce2b9afd3fa7`, all row counts unchanged,
triggers present. **No production deployment exists.**

## Remaining limitations

1. Anthropic not externally verified (no credential).
2. Rutgers SSO not externally verified (no registration).
3. Key rotation delayed up to 30 s.
4. JWKS timeout is an inherited 30 s default.
5. Metrics don't survive restart or aggregate across workers.
6. Rate limiting per process — N workers, N× budget.
7. Cross-process tests ran on one machine.
8. No load, failover, TLS or proxy testing — none exists to test.
9. Live SOC/catalog ingestion not re-run.

---

PR not created because `gh` is unavailable.

Compare:
https://github.com/Shrihan-09/CoursePilot/compare/main...feature/phase-5-12-deployment-verification?expand=1

🤖 Generated with [Claude Code](https://claude.com/claude-code)
