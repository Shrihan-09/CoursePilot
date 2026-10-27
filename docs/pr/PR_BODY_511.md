# Phase 5.11 — BM25 Index Lifecycle and Search Performance

## Summary

The BM25 index is now built **once per corpus version** instead of once per
request. Explanation endpoint p50: **324 ms → 64.5 ms (5.0×)**.

The headline claim is proven by **instrumentation, not timing**: six explanation
requests produce `search_index_builds_total == 1` and `search_index_reuse_total
== 5`.

## A correction to the premise

The brief said the endpoint took 4–5 s. **It does not reproduce** — it measures
336.53 ms mean / 324.15 ms p50 today. The Phase 5.10 figure came from a loaded
machine, where the *unchanged* `/student/context` control also read 137 ms
against 16–22 ms in every other session.

The **diagnosis** was correct (the BM25 build dominates at 78% of the request);
the magnitude was an artifact. Reporting "12× faster" would have been false.

## Where the time went

```
build_course_documents   130.36 ms
build_bm25               133.21 ms
                         --------
per request              ~264 ms   of ~336 ms   (78%)
one search on the index    0.09 ms
```

A 3,000:1 setup-to-use ratio. That ratio, not the duration, is what says "build
this once".

## Index inputs (traced, not assumed)

| table | columns |
|---|---|
| `course` | `course_string`, `supplement_code`, `subject_code`, `course_number`, `title`, `credits`, `level`, `offering_unit_code`, `id` |
| `subject` | `offering_unit_code`, `code`, `description` |
| `catalog_course_entry` | `course_id`, `description`, `catalog_year` |

**Not inputs:** requirement/program tables, student tables, `data_source`
(`course_provenance` is a hardcoded literal). **`CURATED_EXPANSIONS` is a code
constant**, so the identity carries a code half.

## Designs — three rejected for specific reasons

| design | verdict |
|---|---|
| build at startup | **rejected** — breaks the documented invariant that the app boots with Postgres down |
| lazy singleton, no invalidation | **rejected** — silently serves a stale catalog |
| explicit `invalidate()` from ingestion | **rejected** — ingestion is a *separate process*; the discipline isn't merely fragile, it's **impossible** |
| **version-keyed reuse** | **chosen** |

Signal cost measured before choosing:

```
single-row counter (chosen)              0.73 ms
count(*) + max(updated_at) × 3 tables    2.13 ms   + the raw-SQL hole
full content read                        8.65 ms
```

The trigger counter was both cheapest *and* the only one without a correctness
gap — no trade-off to make.

## Why "just make it a singleton" would have been wrong

`ExpandingSearcher.search()` writes `self.last_expansions`, and `context.py`
reads it afterwards — two concurrent requests sharing one wrapper would read each
other's expansions. `BM25Searcher` has no such problem (every assignment lives in
`BM25Index.build`).

So the **expensive immutable** part is shared and the **free mutable** part is
per-request.

## Publication and failure

Publication is a reference swap onto a frozen dataclass — never cleared first,
never mutated in place.

| situation | behaviour |
|---|---|
| rebuild fails, corpus unchanged | keep serving (it's still correct) |
| rebuild fails, **corpus moved** | serve old index + `stale_served_total` + warning — **not silent** |
| rebuild fails, nothing published | raise `IndexUnavailable`; route degrades to empty corpus, no 500 |
| empty corpus | legitimate publishable state |
| version table missing | per-request builds, publish nothing unverified |

## Multi-worker

**One index per process, and not pretended otherwise.** Each worker reads the
same database counter and converges independently. Cost: N builds (~250 ms each)
and N × 1.95 MB after an ingestion. **No restart required** — which is what this
design existed to avoid. No Redis, no pub/sub; the counter *is* the shared signal.

## Results

```
                          before      after
endpoint p50              324.15 ms   64.52 ms   (5.0x)
endpoint mean             336.53 ms   67.62 ms
cold first request        -           352.88 ms  (builds once)
warm reuse (version check) -          0.64 ms p50
warm search               0.09 ms     0.08 ms    (unchanged)
```

**Concurrency** (development environment only):
```
 1 concurrent   builds 1   suppressed 0   {200: 1}
10 concurrent   builds 1   suppressed 9   {200: 10}
50 concurrent   builds 1   suppressed 14  {200: 50}   0 exceptions
```

**Ingestion:** write → next request 328.90 ms (rebuilds) → following 65.53 ms
(reuses).

**Memory:** 1.95 MB per index; 2 copies during rebuild; ~3.90 MB peak. Accepted.

## Retrieval regression

Phase 5.0 evaluation suite unchanged: **46 passed**. Ranking, field weights,
expansion and provenance untouched — this phase changed *when* the index is
built, never what it contains.

## Tests

```
PostgreSQL   619 passed,  1 skipped   unchanged
SQLite       554 passed, 66 skipped   unchanged
Backend      365 passed,  2 skipped   was 334; +31
alembic check                         clean
```

Backend run 3× consecutively, all clean. Migration round-tripped on a fresh DB
and applied to a copy of the real dev DB with all counts preserved.

One test **deliberately breaks invalidation** and asserts the stale result
*appears* — so the nine staleness tests are proven load-bearing rather than
possibly asserting nothing.

## Migration

One table + three triggers. Justified by a **demonstrated cross-process
requirement**: ingestion runs in a separate process, so no in-memory hook can
observe a catalog write.

## Limitations

1. One index per process — N workers, N builds, N copies.
2. `CORPUS_CODE_VERSION` is discipline; the expansion count catches add/remove
   but not an edit to an existing expansion, or tokenization/field-weight changes.
3. The post-ingestion rebuild is paid by a user request (~250 ms). No warming.
4. Triggers are PostgreSQL-only; SQLite falls back to per-request builds.
5. Serving a known-old index on rebuild failure is deliberate, counted, and
   still a window of stale descriptive text.
6. Concurrency figures are development-environment only.
7. Memory is a pickled-size proxy, not a heap measurement.

---

PR not created because `gh` is unavailable.

Compare:
https://github.com/Shrihan-09/CoursePilot/compare/main...feature/phase-5-11-bm25-index-lifecycle?expand=1

🤖 Generated with [Claude Code](https://claude.com/claude-code)
