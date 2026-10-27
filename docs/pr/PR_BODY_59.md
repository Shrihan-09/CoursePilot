# Phase 5.9 — Audit Cache Metrics & the Per-Program Invalidation Decision

## Summary

**Decision: A — keep global invalidation.** At CoursePilot's actual scale the
collateral cost is *exactly zero*, and the alternative carries a verified
correctness hazard.

This phase adds almost no behaviour. It adds the instrumentation needed to
answer a question Phase 5.8 deliberately left open, measures it, and records
the answer together with the condition under which it expires.

## The question

Phase 5.8 chose a **global** trigger-maintained `rules_version`: any rule change
anywhere invalidates every student's cached audit. Phrased so it can be
measured:

> When one program's rules change, how much recomputation does the global
> version cause that a per-program version could have avoided?

## Instrumentation

Phase 5.8 could see *that* a row went stale but not *why* — and the question
turns entirely on why:

```
stale because the student's coursework changed   unavoidable, any design
stale because the engine changed                 unavoidable, any design
stale because the rules version moved            MAYBE avoidable
```

Added:

```
audit_cache_stale_academic_total
audit_cache_stale_rules_total
audit_cache_stale_engine_total
audit_cache_stale_rules_only_total     <- the decision counter
audit_failures_total
```

Three separate **names**, not one counter with a `cause` label — the registry
deliberately has no label API, and that property survives intact.

**Cost, measured:**

| | |
|---|---|
| Phase 5.8 `matches()` | 0.623 µs/call |
| Phase 5.9 `differences()` | 0.596 µs/call |
| `metrics.increment` | 0.228 µs/call |

The read path was already comparing all three components and discarding which
one differed. A stale miss adds ≤4 increments (~0.9 µs) against a ~30,000 µs
recomputation; **a cache hit adds none.**

`audit_failures_total` exists because a hit rate climbing to 99% looks identical
whether the remaining 1% succeeded or exploded. The exception is counted and
**re-raised**, never swallowed.

## A number that was true and misleading

The first benchmark run reported **71 of 81 misses avoidable, 72.8% of audit
time**. Arithmetically correct, completely wrong as evidence — it counted as
avoidable:

* **cold-start misses** — there was no cached row at all;
* **engine-version misses** — Python semantics changed, so every student
  legitimately needed recomputing.

The real figure was **30**. The fix was to stop inferring the cause from the
scenario name and read it from the counters production actually incremented, so
the benchmark's classification cannot drift from what the cache did.

## Results — the sweep, not one number

"30 collateral misses" is not a fact about CoursePilot; it is a fact about
`PROGRAMS = 4`, a number I picked. Sweeping it changed the conclusion:

| programs | audits | hit rate | rules-only | necessary | **collateral** | share of audit time |
|---|---|---|---|---|---|---|
| **1** | 50 | 58.0% | 10 | 10 | **0** | **0.0%** |
| 2 | 100 | 59.0% | 20 | 10 | 10 | 22.0% |
| 4 | 200 | 59.5% | 40 | 10 | 30 | 29.9% |
| 8 | 400 | 59.8% | 80 | 10 | 70 | 36.1% |
| 16 | 800 | 59.9% | 160 | 10 | 150 | 37.8% |
| 32 | 1600 | 59.9% | 320 | 10 | 310 | 40.2% |

Collateral follows **(P−1)/P** exactly — 10/20, 30/40, 70/80, 310/320 —
confirmed by measurement rather than asserted.

Latency at P=4: warm p50 **4.86 ms** / p95 7.14; cold p50 **31.62 ms** / p95
47.22.

## The decisive number

**CoursePilot has one program.** At P=1 collateral is zero *by construction* —
with a single program there is no other program whose rules could invalidate
anyone. Global and per-program versioning are **behaviourally identical** on the
current data.

Per-program would today be a correctness risk taken on for a measured saving of
**0 ms**.

## Pricing the alternative

Read out of the schema, not asserted:

```
requirement_course_option.requirement_id -> requirement      ON DELETE CASCADE
requirement.parent_id                    -> requirement      ON DELETE CASCADE
requirement.program_version_id           -> program_version  ON DELETE CASCADE
program_version.program_id               -> program          ON DELETE CASCADE
```

A per-program trigger on `requirement_course_option` must find its owning
program version *through* `requirement`. Deleting a program cascades four
levels, so when that trigger fires the parent may already be gone — and so may
the counter row it was meant to bump. The design must then decide what a missing
counter means, and only one answer ("unknown, so invalidate") is safe.

That is a checkable hazard in exactly the place where a mistake produces a stale
degree audit.

## When to revisit

Now observable in production:

```
audit_cache_stale_rules_only_total / audit_cache_misses_total
```

Revisit when **all** hold: more than one program carries students; rules-only
misses are a sustained high share of misses; and the absolute wall time is worth
the cascade-correctness work. Until then the counter costs 0.228 µs and answers
the question for free.

## Correctness

All Phase 5.8 guarantees re-verified, plus new regression tests. The sharpest:
**simultaneous** academic + rules changes attribute to both and explicitly
**not** to `rules_only` — a miss that was required anyway must never be counted
as avoidable. Also: a cold miss is not an invalidation, `rules_only <= rules`,
`stale <= misses`, and an engine failure is counted and re-raised.

The cardinality guard was `assert len(declared) == 10` — which broke on every
addition while testing nothing. Rewritten as an **AST** check that every
`increment`/`observe` argument is a declared module constant. Mutation-verified:
`f"stale_{student.id}"` fails it.

## Tests

```
PostgreSQL   619 passed,  1 skipped    unchanged
SQLite       554 passed, 66 skipped    unchanged
Backend      284 passed,  2 skipped    was 275; +9
alembic check                          clean — no migration added
```

Backend suite run 5 consecutive times, all clean. Suites run **serially** — the
Phase 5.8 lesson about two suites sharing one database.

## Known limitations

1. **The benchmark is synthetic and tiny** — ≤32 programs × 5 students × 6
   courses on one laptop. It establishes the *shape* of the cost, not
   CoursePilot's production cost.
2. **The recuration rate is invented** and far above reality.
3. **P=1 is a datum about a project with one ingested program**, not evidence
   that per-program versioning is unnecessary at scale.
4. Metrics remain per-process, in memory, no export, no percentiles.
5. Trigger behaviour is PostgreSQL-specific; SQLite uses the fingerprint
   fallback and proves nothing about triggers.
6. **Not measured:** many-distinct-student concurrency, storage growth over
   time, recuration on a full-sized requirement tree (~800 eligibility rows vs
   the benchmark's 6).

Two measurement readings during this phase were machine-suspend artifacts (a
5.67 s test file reporting 30 minutes). Caught with `--durations` and an
unchanged control endpoint rather than assumed.

## Unchanged

The Degree Engine and its objective, allocation, baseline semantics, the Phase
5.6 API contract, Phase 5.7 pooling, and the Phase 5.8 cache mechanism,
compression and trigger set. No migration required or added.

---

PR not created because `gh` is unavailable.

Compare:
https://github.com/Shrihan-09/CoursePilot/compare/main...feature/phase-5-9-cache-metrics-decision?expand=1

🤖 Generated with [Claude Code](https://claude.com/claude-code)
