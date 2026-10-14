# Phase 6.7: Live Registration Availability

## Summary

CoursePilot now tells **"this schedule is structurally valid"** apart from **"these sections can be registered now"**. Volatile open/closed data never touches the structural model.

- **Own refresh boundary.** A separate refresh makes one `openSections.json` request per term. It archives every payload and records each poll, accepted or rejected with the reason. It never re-ingests the catalog.
- **Own tables.** Each section has a current-state row, plus an append-only log of state transitions.
- **Schedule Engine 6.7.0.** It reads stored availability in 2 extra queries and never fetches. It evaluates availability per registration bundle (a lecture plus its required LB lab), labels every state with its freshness, and adds `prefer_open` (default) and `require_open` modes.

Three axes stay independent:
- **eligibility:** may I take the course? (6.4)
- **structural validity:** do the sections fit? (6.6)
- **availability:** can I register now? (6.7)

## What the real data showed

The investigation is in `docs/investigations/phase-6-7-live-availability.md`.

- **The source is binary.** Rutgers publishes only open/closed: no seats, capacity, waitlist or timestamp. Membership in `openSections.json` matched `courses.json` `openStatus` on all 12,012 Fall 2026 sections.
- **`campus` is ignored.** The list is term-wide across campuses, and other campuses' indexes are ignored by joining on the term's sections.
- **`[]` is ambiguous.** It comes back for an unpublished term, year 1999 and a nonsense campus alike, so it is rejected and never read as "everything closed".
- **Indexes are reused across terms.** A real Spring 2026 payload shares 116 open indexes with Fall sections, so the body alone cannot prove its term. The request URL's year/term must match (`rejected_term_mismatch`).
- **Unchanged polls are the norm.** Two real polls 80 s apart were identical, so only transitions are stored.
- **An archive's file time is not an observation time.** A copy resets it, so `courses.json` `openStatus` is never labelled current. Only an accepted refresh is.

## Schema (migration `be544a0040f1`, additive)

- `availability_refresh`: one row per poll, with verdict, hash, counts and archive path.
- `section_availability`: the current state per section and since when; CASCADE with the section.
- `section_availability_change`: append-only transitions. `section_id` is SET NULL so history outlives a removed section, and a CHECK enforces `from_status <> to_status`.
- **Indexes:** each serves a stated query. The latest accepted refresh per term uses one; a notifier's "changes since X" uses another.
- **Migration checks:** upgrade → downgrade → re-upgrade on the dev and test DBs, and `alembic check` is clean.

## Engine

- **States and freshness.** States are open, closed and unknown. Freshness is current, stale or unobserved, judged against an explicit `reference_time`.
- **Conflicting sources.** Two sources that disagree within 900 s make a section UNKNOWN, with `sources_disagree:...`.
- **Bundles.** A bundle is OPEN only if every component is OPEN; CLOSED if any is; otherwise UNKNOWN. Lecture OPEN plus lab CLOSED is CLOSED, which a regression test pins. An academic co-requisite (229 needs 227) keeps two bundles.
- **`prefer_open`.** Nothing is excluded. With current data, closed and then unknown sections rank after open ones, behind structural verification and ahead of campus/day/gap preferences. Stale data never reorders options.
- **`require_open`.** Only sections open as of a current observation are searched. A missing current refresh gives an `AVAILABILITY_NOT_CURRENT` blocker. A component with no open section is named by `NO_OPEN_SECTION`.
- **Structured output.** Each option carries per-course bundle availability with component states, plus `as_of` and `all_open_now`. Each choice carries its state, freshness, `observed_at`, source, `since` and reasons. Metadata carries the term's freshness, refresh id and mode.
- **Search, kept exact.** Equivalent sections are no longer split by state: the open twin is the representative. A lookahead lower bound keeps the search exact with the longer objective.

## Measured

**Refresh** (development PostgreSQL, full Fall 2026, real archived payloads replayed; `scripts/benchmark_availability.py`)

| Step | Verdict | Transitions | Time | SQL |
|---|---|---|---|---|
| First observation (11,992 sections) | accepted | 11,992 | 3.3 s | 31 |
| Same payload again | accepted | 0 | 0.29 s | 7 |
| Real next poll (+80 s) | accepted | 0 | 0.38 s | 7 |
| `[]` (Spring 2027) | rejected_empty | — | 0.71 s | 7 |

- **One refresh is one request:** 96 KB, 28 KB on the wire. Parsing takes 3.8 ms.
- **Lookup of the whole term:** 3 statements.

**Schedule generation** (engine stages, minimum of 15 runs, main and branch alternated)

| Case | 6.6.1 (main) | 6.7, stale | 6.7, current |
|---|---|---|---|
| 5 high-section courses | 286–298 ms, 142,816 nodes | 272–298 ms, 125,976 nodes | ~400 ms, 187,384 nodes (exact, no limit) |
| Component-heavy | 169–176 ms, 29,298 nodes | 175–189 ms, 29,298 nodes | 36–49 ms, 11,635 nodes |
| 4 courses with an LB lab | 9.1–9.6 ms | 9.6–10.4 ms | 4.7–5.4 ms |

- **SQL per request:** 13 on 6.6.1; 14 with no refresh; 15 with a refresh. The count is the same for 1 course or 4.
- **No network:** generation makes no network calls.
- **Same results as 6.6.1:** with stale availability, the ranked options are identical on all 10 benchmark requests.

Two regressions were found and fixed while measuring:
- **Search time doubled.** The generalized ranking prefix was recomputed at every node; it is now incremental.
- **Node limit hit.** The high-section case hit its node limit with current data. The fixes: try open sections first, don't split equivalent sections by state, and add an exact lookahead bound. That bound is checked against brute force on 30 random weighted cases.

## Tests

| Suite | Baseline | Final |
|---|---|---|
| Root (PG) | 1560 passed, 5 skipped | **1640 passed, 5 skipped** |
| Backend | 739 / 4 | **790 / 4** |
| Ingestion PG | 821 / 1 | **850 / 1** |
| Retrieval | 46 | **46** |
| Ingestion SQLite | 756 / 66 | **785 / 66** |

There are 0 failures; the skips are unchanged and pre-existing.

- **Offline tests (`test_availability_domain.py`, 39).** They cover parsing, duplicates, malformed shapes, empty, no overlap, the campus being ignored, implausible drops, term-from-URL, change detection, resolution and conflicts, the bundle rule, and freshness.
- **Real-data tests (`test_live_availability.py`, 29, SQLite and PG).** They cover:
  - bundles A–F on 01:750:193/194/203/227/229/202, with the lecture-OPEN + lab-CLOSED regression; synthetic cases are labelled;
  - idempotent re-apply, real wrong-term payloads, the pipeline with an injected transport and no network, archive replay, and history outliving a removed section;
  - table hashes showing the refresh never touches structural rows and generation writes nothing;
  - a constant query count with no N+1.
- **Failure injection.**
  - **6.7 (`scripts/inject_availability_failures.py`):** 17 of 17 detected. The faults cover lecture lookup, linked-lab lookup, term scoping, bundle aggregation, stale handling, unknown handling, disagreement handling, the empty/no-overlap guards, the wrong-term guard, OPEN→OPEN events, hidden writes, N+1, `prefer_open` discarding, ranking, `require_open` accepting stale data, co-requisite folding, and download-as-current.
  - **6.6 schedule matrix:** 16 of 16 detected. Faults H, I and K were repointed to the 6.7 code. J and M had stale patterns: J since 6.6.1, and M after this phase's `load_term` change. Both are fixed and verified.
- **Migration:** upgrade → downgrade → re-upgrade on both DBs, and `alembic check` is clean.

## Not in scope

No push or email notifications, no prediction, no automatic registration, no WebReg, CAS or Degree Navigator access, and no LLM. The design supports CLOSED → OPEN detection and prediction later: the transition log is indexed for "changes since X".

🤖 Generated with [Claude Code](https://claude.com/claude-code)
