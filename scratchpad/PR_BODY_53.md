# Phase 5.3 — API Security, Request Identity & AI Cost Controls

## Summary

Hardens the API boundary before CoursePilot gains chat, memory or agents. Adds authentication, real data isolation, per-identity rate limiting and server-owned AI cost controls — **with the Degree Engine untouched and no migration**.

> Security wraps CoursePilot. Security does not redefine CoursePilot.

Based on `main`, which now contains Phase 5.2.

## Investigation first (Part 1)

Before writing anything:

| | Status before this phase |
|---|---|
| authentication | **none** — no `get_current_user`, no bearer handling, no JWT |
| rate limiting | **none** |
| middleware | CORS only |
| user table | **does not exist** |
| ownership link | **does not exist** — `Student` has no owner column |
| student identity | `Student.external_ref`, nullable unique `String(64)` |

`Student`'s own docstring already said authentication *"must land before this table holds a real person"*.

## Security model

```
Authorization: Bearer <credential>
        |
        v
Principal(subject=..., student_ref=...)    <- server-derived
        |
        v
Student.external_ref == principal.student_ref
```

**`student_ref` was removed from the request schema.** That is the entire isolation property:

> Student A cannot request Student B's audit because there is nowhere to put "B".

Ownership enforced by the **absence of a field** is stronger than a check somebody can forget to write — and it needs no `user` table, so `alembic check` stays clean.

**Development auth** (`Bearer devtoken:<ref>`) is off by default, refused when the environment is production even if enabled, and **fails closed** when disabled. Tests override the dependency rather than bypassing it, so the authorization path is exercised rather than skipped.

### Error contract

| Condition | Response |
|---|---|
| missing / malformed / unverifiable credential | **401** + `WWW-Authenticate` |
| over request or model budget | **429** + `Retry-After` |
| malformed body, unknown field | **422** |
| student or course not found | **404**, identical detail for both |
| provider failure or invalid output | **200**, deterministic explanation |
| Degree Engine failure | **500** |

401s never say *why*, and a missing student and missing course are indistinguishable — otherwise the endpoint becomes an account oracle. There is no `403`: "authenticated but not allowed" is not a reachable state.

## AI protection

```
requests / identity / minute      60    protects database and audit engine
model calls / identity / minute   10    protects MONEY
```

Two budgets because one cannot serve both: loose enough for free deterministic explanations would permit 60 billable calls a minute. The model budget is checked **before** any database work and skipped entirely when `EXPLANATION_PROVIDER=none`.

| Bound | Setting | Value |
|---|---|---|
| output tokens | `explanation_max_output_tokens` | 1500 |
| context chars | `explanation_max_context_chars` | 12000 |
| provider calls / request | `explanation_max_provider_calls` | 1 |
| SDK retries | `explanation_provider_retries` | 1 |
| timeout | `explanation_timeout_seconds` | 30 |

None is a request field. `provider`, `max_tokens`, `temperature` and `student_ref` are all absent from the schema, so attempting them is a **422**.

**Retries:** transient classes only (connection, 408, 409, 429, 5xx) — never 400/401/403/404, which would fail identically. A **validation** failure is never retried: a rejected response does not trigger a second "corrective" call, pinned by a test asserting the scripted model's second response is left unconsumed.

## Privacy

Logged: request id, **hashed** principal handle (12 chars of SHA-256), route, provider, outcome, latency, rejection reasons.

Never logged: credentials, `Authorization` headers, API keys, NetIDs, prompts, model responses, academic content. A test asserts the NetID and token are absent from captured logs.

## Tests

```
PostgreSQL:  619 passed, 1 skipped     (unchanged from 5.1/5.2)
SQLite:      554 passed, 66 skipped    (unchanged from 5.1/5.2)
Backend/API:  70 passed, 1 skipped     (was 38; +32 security tests)
```

The 1 skipped backend test is the opt-in live-provider smoke test.

Phase 5.2 API tests were **updated, not weakened** — they now send credentials and no longer send `student_ref`, because the contract deliberately changed. The forbidden-field set grew to include `student_ref`, `provider`, `max_tokens`, `temperature`.

## Live provider

```
Live provider execution: NOT VERIFIED
Reason: no API credential available in this environment
```

The opt-in test (`RUN_LIVE_AI_TESTS=1`) skipped, which is correct default behaviour.

## Database

```
alembic check: clean — "No new upgrade operations detected"
migration: NO
```

No schema change was needed: identity maps to the existing `Student.external_ref`.

## Deterministic regression

Allocation, category allocation, the global optimizer, baseline semantics, requirement satisfaction, course eligibility, sharing policy, BM25 ranking, catalog authority and Phase 5.1 explanation facts are all unchanged — proven by identical suite counts (554 / 619).

## Performance

```
warm endpoint latency    ~320 ms   (Phase 5.2 baseline ~320 ms — unchanged)
  index rebuild          ~230 ms   still dominant, still not cached (Part 24)
  audit + baseline        ~65 ms
unauthenticated request   1.4 ms   rejected before any database work
```

Authentication and rate limiting add **no measurable overhead**.

## Known limitations

**Genuine:**
1. **Not production authentication.** The dev scheme trusts the token's contents; it verifies nothing.
2. **No user/account model** — isolation works because the schema cannot name a student, not because ownership is modelled.
3. **Rate limiting is per-process and in-memory** — N workers means N × the limit.
4. **Live provider unverified** — no credential available.
5. No global request-ID middleware; the explanation route generates its own.

**Intentionally out of scope:** BM25 caching (Part 24), chat memory (Part T of 5.2), autonomous agents, frontend work.

---

PR not created because `gh` is unavailable.

Compare:
https://github.com/Shrihan-09/CoursePilot/compare/main...feature/phase-5-3-api-security?expand=1

🤖 Generated with [Claude Code](https://claude.com/claude-code)
