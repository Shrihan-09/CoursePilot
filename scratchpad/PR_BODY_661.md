# Phase 6.6.1: Harden Rutgers linked registration components

> **Stacked on `feature/phase-6-6-schedule-engine` (PR for Phase 6.6, not yet merged).** Review and merge Phase 6.6 first. This PR's diff against `main` will then contain only the 6.6.1 commits.

## Summary

This is a correctness phase, not a redesign. It proves on real Rutgers records that CoursePilot tells apart three different things called a "lab", and that it cannot silently drop a required lab, recitation, workshop or linked course.

| Relationship | Real example | Owner |
|---|---|---|
| **Meeting**: one index, several meetings | 01:750:203 (LEC + RECIT), 01:119:115 (LEC + WORKSHOP) | section meeting model |
| **Registration**: one course, two registration records | 01:750:193, 194 and 202 + their 0-credit "LB" lab record | Schedule Engine (bundle) |
| **Academic**: course X requires course Y concurrently | 01:750:205 → 203, 01:750:229 → 227, 01:119:117 → 116 | Phase 6.4 `check_proposal` |

The investigation covers five archived NB terms and is in `docs/investigations/phase-6-6-1-linked-component-semantics.md`.

## What was silently wrong before

1. **Co-requisites printed on only some sections were dropped.** 60 offerings were affected (Physics labs 750:205/206/229, ECE labs 14:332:223/224/233/363/368, Bio 119:117). Nothing was stored, so eligibility answered "satisfied".
2. **The misspelling "CO-REQUSITE" was never matched.** It appears 72 times, for example on 01:750:229.
3. **"MUST [ALSO] REGISTER FOR [LAB] <course>" was never read.** Examples are ROTC 03:691 and the BTAA language courses.
4. **Some meeting times exist only in section prose.** For example, Summer 01:160:308's structured meetings are lecture-only, while its note says "RECIT: TWH 8:00-8:50AM". These sections were treated as conflict-checked.
5. **Lecture/lab pairings written in prose were never read.** For example, "STUDENTS ALSO REGISTERED FOR LAB SECTION 01:160:314:H1 OR H2 MUST TAKE THIS SECTION OF LECTURE".

## What was flagged but incomplete

- **01:750:194 lost its lab.** Its note says "MUST REGISTER BOTH LEC/REC & LAB", wording Phase 6.6 didn't recognise, so the course was scheduled without its LB lab (with an "unverified" warning).
- **"X IS A CO-REQUISITE" was stored as unsupported** (UNKNOWN) instead of being parsed.

## Found while testing (two pre-existing bugs)

- **Phase 6.4 eligibility:** prerequisite rows were gathered by course string, which included the LB record. 01:750:194's LB note differs only by "FOR ALL SECTIONS", so the course came back UNKNOWN for every student and was never planned. Eligibility now reads the base record's rows.
- **Phase 6.5 planner:** a co-requisite partner that isn't itself a requirement candidate (01:750:227 for 01:750:229) crashed the planner with a `KeyError`.

## Changes (no migration)

- **Co-requisite parser v2:** the forms above are rewritten to "COREQ: <codes>" before parsing. A registration phrase must name a course code; same-course "REGISTER FOR BOTH" stays a registration fact.
- **Partial co-requisites:**
  - A co-requisite on only some sections is stored as `sectionNotes:some`, with "published on N of M sections" recorded as evidence.
  - Unmet, it is **UNKNOWN**; with the partner course proposed, it is SATISFIED.
- **Condition parser v2:** "MUST REGISTER [FOR] BOTH" after a restated prerequisite is treated as registration logistics. "… AND MUST REGISTER …" still stays uninterpreted.
- **Schedule Engine 6.6.1 behaviour:**
  - Companion evidence is read from either record, in any observed wording. The structural test is unchanged: same course string, a supplement code, and 0 credits.
  - A section whose notes contain a time range gets `MEETING_TIME_IN_NOTES` and is not time-verified.
  - A section whose note names a section of another requested course gets `SECTION_NOTE_REFERENCES_REQUESTED_COURSE`.
- **New output fields**, so web/mobile clients never parse notes:
  - `credits` and `total_credits` (the 0-credit lab adds 0; the 1-credit lab course adds 1);
  - `meeting_components`;
  - `component_evidence`;
  - `meeting_text_in_notes`;
  - a result-level `relationships` list (`registration_component`, `registration_component_unverified`, `academic_corequisite`).
- **Planner:** a course whose co-requisite is UNKNOWN only because it's partial is tried with its partner, and the partner bug is fixed.

## Backfill

There's no migration. Re-run the course stage for each archived term (the commands are in DATA_MODEL §42.5). Results on dev:
- `sectionNotes:some`: 73 new rows (32 parsed, 41 unsupported);
- `sectionNotes:all`: 171 → 198;
- course, offering and section counts unchanged;
- a second run changed nothing.

## Tests

`ingestion/tests/test_linked_components.py` has 17 tests. They use whole, unmodified Rutgers records loaded through the production course and section pipelines, built by `scripts/make_component_fixture.py`. They cover:

- **Meeting relationship:** one index with several meetings.
- **Registration bundles:**
  - 01:750:193/194/202 bundles, including Fall 2026 194's new wording;
  - a zero-credit lab adds no credits;
  - an unproven record is never bundled (synthetic);
  - a conflicting lab is a blocker, never an omission (synthetic times);
  - a lab restriction is evaluated (synthetic restriction).
- **Academic co-requisites:**
  - an academic co-requisite course: blocked alone, scheduled as a pair, credits 3 + 1;
  - 01:750:229's partial co-requisite ("CO-REQUSITE", 26 of 27 sections) and 01:119:117's (2 of 27);
  - a lab course with no co-requisite;
  - the cross-course registration prose.
- **Prose and session dates:**
  - prose-only meeting times;
  - a prose section pairing;
  - components in different sessions.
- **Plan → schedule:** the planner plans 194 once and 229 + 227 together; the scheduler expands 194 into lecture + lab.
- **Determinism:** shuffled input order gives identical output.

**Failure injection** (`scripts/inject_component_failures.py`): **14 of 14 faults detected.**

| Fault | Injected bug | Caught by |
|---|---|---|
| A | required LB lab dropped from the bundle | 7 tests (e.g. the bundle test for 193 and 194, the plan → schedule test) |
| B | unproven record forced into the bundle | the unverified-record test |
| C | academic co-requisites ignored | 10 tests (6.4 / 6.5 / 6.6.1) |
| C2 | partial co-requisite dropped at load (the old behaviour) | 4 tests (229, 119:117, 617:201, plan → schedule) |
| C3 | partial co-requisite treated as satisfied | 3 tests |
| D | registration component presented as a course | 7 tests |
| E | companion's meetings ignored in conflicts | 4 tests, including the conflicting-lab blocker |
| F | companion restriction ignored | the lab-restriction test |
| G | prose meeting times treated as verified | the prose-time test |
| H′ | prose section pairing not flagged | the pairing test |
| I | 0-credit lab adds credits | the bundle test (193, 194) |
| J | 1-credit lab counted as 0 | the academic-co-requisite credit test |
| K | session dates ignored | the date-range test and disjoint-sessions test |
| L | conflicting lab silently dropped to find a schedule | the conflicting-lab blocker test |

H (incompatible lecture/lab pairing accepted) cannot be injected: Rutgers publishes no machine-readable pairing, so none is modelled. H′ covers the prose form instead.

**Benchmark** (`scripts/benchmark_schedules.py`, dev data, Fall 2026, 15 runs):

| Case | Cartesian product | Search nodes | p50 / p95 | SQL statements |
|---|---|---|---|---|
| 3 courses | 108 | 74 | 21 / 36 ms | 13 |
| 4 courses + LB lab | 8,748 | 1,491 | 33 / 55 ms | 13 |
| 5 courses | 1,980 | 285 | 25 / 38 ms | 13 |
| 5 largest courses (engine stages) | 2,012,800,104 | 142,816 | 278 ms load + 303 / 338 ms search | 7 |
| **component-heavy (engine stages)**: 750:193 + LB, 203 + 205, 119:116 + 117 | 44,385,165 | 29,298 | 29 ms load + 179 / 207 ms search | 7 |

Node counts for the Phase 6.6 cases are identical to 6.6. Every run produced identical output, and no case reached the node limit.

**Lint:** ruff is clean for every line this PR adds. The remaining findings in touched files were already present before this branch. The long lines in `inject_component_failures.py` are literal source patterns, the same convention as the Phase 6.5/6.6 injection scripts.

**Full regression** (all green):

| Suite | Before (Phase 6.6 head `c638a5b`) | After |
|---|---|---|
| Root (PG) | 1543 passed, 5 skipped | **1560 passed, 5 skipped** |
| Backend | 739 passed, 4 skipped | **739 passed, 4 skipped** |
| Ingestion PG | 804 passed, 1 skipped | **821 passed, 1 skipped** |
| Retrieval | 46 passed | **46 passed** |
| Ingestion SQLite | 739 passed, 66 skipped | **756 passed, 66 skipped** |

The +17 in each suite are the new linked-component tests. One existing test was deliberately updated: `test_schedule_api` pins `schedule_engine_version`, now `6.6.1`, because the output schema gained fields. `alembic heads` is `87bec76b788c`, unchanged, since there's no migration.

## Limitations

- No Rutgers field links a specific lecture to a specific lab, so prose pairings are flagged, never enforced.
- Text CoursePilot cannot read stays UNKNOWN. Examples: ROTC "MUST REGISTER FOR LAB 03:691:103 F 8:00AM - 1:00PM", the wrap-damaged "01:563:1 31", and "AUTO-REGISTERED FOR 01:160:101:E1".
- About 1% of sections state times in prose. These are never verified, which is honest but may lower confidence for Nursing, ROTC, Summer Biology/Chemistry and BTAA courses.
- No optional lab exists in the data, so none is modelled.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
