# Phase 5.10 — Production Observability and Failure-Path Hardening

## Summary

No new product functionality. This makes the path CoursePilot already has
diagnosable, and closes the gaps a trace of the real request path exposed —
including **four identifier leaks the previous phases' own documentation claimed
did not exist**.

**No migration.** `alembic check` clean.

## What the trace found

| gap | before | after |
|---|---|---|
| correlation | `request_id` existed, but **only inside the explanations route** | one id per request, in a ContextVar, on every log record and response |
| unhandled errors | **no exception handler registered** — bare 500, no id, no metric | correlated, counted, opaque to the client |
| request metrics | none | count by status class, latency histogram with percentiles |
| error shape | `{"detail": ...}`, no machine-readable code | additive taxonomy alongside unchanged `detail` |
| explanation outcomes | one `used_model` boolean | unavailable / attempted / succeeded / failed / rejected |
| **raw identifiers in logs** | `str(account.id)`, `str(student_id)`, `student.id`, `course_key` | hashed handles, or removed |

## Security findings (Part 8)

Four call sites logged identifiers in plaintext, contradicting Phase 5.5's own
stated policy ("counts and timing only… the principal is the hashed handle"):

- `accounts.py` — `str(account.id)`, `str(performed_by.id)`, `str(previous)`
- `admin.py` — `str(student_id)` on link **and** unlink
- `engine.py` — `student.id` in the optimizer fallback line
- `explanations` — `course_key` in the route log and the rejection log

Plus `logger.exception` in the model-failure path, whose traceback can quote the
rendered academic evidence. Now `type(exc).__name__` only.

Nobody was careless — the policy lived in prose next to the compliant code, so it
was invisible from everywhere else. **A rule that is documented but not enforced
decays at the rate the codebase grows.**

## The guards took two attempts

An AST check for `x.id` inside a `logger.*` call passed. Then I re-introduced the
exact bug just fixed — and **it still passed**, for two separate reasons:

- the static check matched `Attribute(attr='id')` but not `Name('student_id')`;
- the dynamic log-capture test never exercised the admin mutation path.

Static analysis sees code tests never run; dynamic tests see indirection static
analysis cannot follow. Both are now present, and both fail on the re-introduced
bug. `request_id` is explicitly exempted — a statement about the design, not
tuning a test until it stops complaining.

## Correlation (Part 2)

Server-minted, opaque, 16 hex chars, never derived from identity. A
client-supplied `X-Request-ID` is **ignored** — honouring it would let a caller
forge a shared id across users or write attacker-chosen text into the log stream.

Injected by a logging **filter**, so the Degree Engine, cache and providers are
correlated without knowing an HTTP layer exists. A test asserts records emitted
*inside the worker thread* carry the request's id — if the ContextVar did not
cross `run_in_threadpool`, every line an operator most needs would be
uncorrelated.

## Error taxonomy (Part 5) — additive

The first draft *replaced* `detail` and two existing tests failed, one asserting
the 409 sentence Phase 5.4 chose deliberately. The tests were right. `detail` is
unchanged; `error {code, message, request_id}` sits alongside it.

422 no longer echoes the input — a test posts a fake API key as `course_key` and
asserts it does not appear in the response.

## Cache failure matrix (Part 6)

| failure | recompute | rollback | response |
|---|---|---|---|
| read failure | yes | **yes** | 200 fresh |
| write failure | n/a | yes | 200 |
| corrupted payload | yes | no | 200 fresh |
| decompression failure | yes | no | 200 fresh |
| malformed JSON | yes | no | 200 fresh |
| table unavailable | yes | **yes** | 200 fresh |
| transaction already failed | yes | **yes** | 200 fresh |

The rollback column is the Phase 5.7 defect restated. Database-level failures
need it; Python-level ones do not — a distinction invisible until the table
forces it. Injected **for real** (table renamed, bytes corrupted), because mocks
test control flow and only real failures test state.

## Provider failure matrix (Part 7)

Timeout, connection error, 408/409/429/500/502/503 → `model_failed`. Empty
response, malformed JSON, unsupported claim → `model_rejected` (the provider
answered; the output was unusable). Not configured → `provider_unavailable`.

**Every row returns a correct deterministic explanation. None returns an error.**
A test pins that the service calls the model exactly once — retries belong to the
SDK.

## Performance (Part 10)

The first run was **sequential** A/B and produced deltas from −22.8% to +25.0% —
impossible for a middleware doing a `uuid4` and three dict operations. It was
measuring drift. Interleaved sample-by-sample:

```
GET /student/context      p50 16.67 -> 17.22   +0.55 ms  (+3.3%)
GET /student/audit (warm) p50 13.91 -> 14.12   +0.21 ms  (+1.5%)

components: new_request_id 1.099us | ContextVar 0.247us
            increment 0.242us | observe 0.957us   -> ~2.8us total
```

The instrumentation costs ~2.8 µs; the endpoint delta is 0.2–0.55 ms. **The gap
is Starlette's `BaseHTTPMiddleware`, not the instrumentation** — different fixes,
and at 1.5–3.3% neither is worth taking yet.

Not measured meaningfully: the explanation endpoint, at 4–5 s because it rebuilds
the BM25 index per call (a deferred Phase 5.0 limitation that swamps any signal).

## Tests

```
PostgreSQL   619 passed,  1 skipped   unchanged
SQLite       554 passed, 66 skipped   unchanged
Backend      334 passed,  2 skipped   was 284; +50
alembic check                         clean — no migration
```

Backend run 3× consecutively, all clean. Suites run serially.

## Limitations

1. **Metrics remain per-process and in-memory** — no export, no cross-worker
   aggregation, lost on restart. **This is not distributed observability.**
2. Percentiles cover the last 512 observations per histogram, not a time window.
3. **Admin URLs contain a student id** (`/admin/students/{id}/link`). The
   application's logs no longer carry it; an ASGI server or proxy access log
   will. Not closable from inside the application.
4. No tracing spans — one flat id per request.
5. Rate limiting still in-memory/per-process.
6. Several declared stage metrics are available but not yet wired by callers.
7. Live Rutgers SSO and the live Anthropic API remain unverified; the provider
   matrix uses a scripted model.
8. The BM25 rebuild per explanation request is untouched.

## Unchanged

Degree Engine, objective, allocation, baseline semantics; Phase 5.6 response
contracts; Phase 5.7 pooling; Phase 5.8 cache and triggers; Phase 5.9's
global-invalidation decision; all status codes and their meanings.

---

PR not created because `gh` is unavailable.

Compare:
https://github.com/Shrihan-09/CoursePilot/compare/main...feature/phase-5-10-production-observability?expand=1

🤖 Generated with [Claude Code](https://claude.com/claude-code)
