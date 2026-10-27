# Phase 6.5: Deterministic Multi-Semester Planning Engine

## Summary

This phase adds a semester-level **course** planner. It works out which courses a student should take in which term. It does not handle sections, times, rooms or registration. It's exposed as:

```
POST /api/v1/student/plans/generate     { "start_term": "20271", "program_key"?, "catalog_year"?, "constraints"? }
```

The planner makes no academic decision of its own. It asks the existing engines, and asks them again after every choice:

| Question | Answered by |
|---|---|
| What remains? Does a planned course count, where, and only once? | Degree Engine 6.4.0. Planned courses are passed in as **projected** in-progress attempts with no grade. |
| May the student take X in term T, with which same-term partners? | `check_proposal` (Phase 6.4), covering prerequisites, minimum grades, co-requisites and PRE OR COREQ |
| Is X offered in T? | SOC offering evidence: confirmed, historical in the same season, or none |

**No LLM is called** anywhere on this path. A test checks the imports transitively, in a fresh interpreter.

## How a plan is built

- **Eligibility, asked twice per candidate:**
  - from the student's history alone → `satisfied_by_history`;
  - as if planned-earlier and in-progress courses were passed → `conditional_on_plan`. Each dependency is listed with the interpreted minimum grade ("pass 112 with C").
  - Under the second question, UNKNOWN → `needs_confirmation`, and the course is **never placed**. A future grade is never assumed.
- **Prerequisite paths:**
  - The published AND/OR is preserved: OR picks one branch, AND takes them all.
  - Alternatives are chosen by a deterministic key: fewest added courses, then most that also count toward the degree, then canonical text.
  - Cycles and depth limits are reported, not followed.
  - A low recorded grade becomes a planned **retake**.
- **Co-requisites:** a course whose co-requisite is unmet alone is placed together with a partner from its published rule, and `check_proposal` decides whether that works. An uninterpretable co-requisite is UNKNOWN and never placed.
- **Offerings:**
  - Confirmed or historical-same-season offerings are placed. Historical ones are labelled as evidence, never as confirmed.
  - Courses offered only in other seasons, or with no evidence, aren't placed.
  - A future term is evaluated under the latest published rules from the same season (`rules_from_earlier_term`).
- **Selection is greedy and deterministic, never called "optimal".**
  - The documented order: needed dependency, then unlocks, then scarcity, then confirmed before historical, then history before conditional, then course string.
  - A course is accepted only if the Degree Engine shows progress in *useful* units.
  - A final pass prunes courses the allocation credits nowhere.
- **Load limits** (15 credits, 5 courses, 8 terms) are labelled `coursepilot_planning_setting_not_rutgers_policy`, and the client cannot change that label. Variable-credit courses are never placed. There is no difficulty model.
- **Every placed course** carries reason codes, its prerequisite evidence, its offering evidence and its requirement contribution, which comes from the engine's allocation.
- **Every gap** is reported as a structured issue: blocker, needs-confirmation or warning.

## Findings fixed along the way

1. **Batches vs proposals.** `check_proposal(courses=[...])` treats its courses as one same-term proposal. Batching *alternatives* let 01:198:205, which was only another candidate, satisfy 211's `CO-REQ: 01:198:205`. A new `independent=True` judges each candidate alone, and the planner always uses it.
2. **Unknown is not free.** Newark's 21:640:113/114 aren't in CoursePilot's data, so they looked like a zero-cost path to 01:640:135. A course with no catalog row or no offering evidence is now infeasible.
3. **The level constraint.** Six 200-level Sociology electives counted as full progress on "six electives, three at the 300 level". The progress measure now counts only slots that can still be part of a valid selection. It uses the Degree Engine's own predicate (`constraint_counts`, extracted, behaviour unchanged).
4. **The category constraint.** Psychology took three COGNITIVE courses for "four subdisciplines". Category repeats beyond N − K no longer count.

## Changes to existing code (all optional and read-only)

- `DegreeAuditEngine.audit(projected=())`: transient attempts, never added to the session.
- `DegreeAuditEngine.baseline_memo`: opt-in reuse of the earned baseline.
- `constraint_counts`: a shared predicate; violation messages are unchanged.
- `check_many(projected=)` and `check_proposal(projected=, as_of_term=, independent=)`.
- `app/services/planning/__init__.py`: the original **unimplemented** LLM `Planner` protocol is kept unchanged, with a note that the deterministic engine never uses it. Deleting it should be an explicit decision.
- `docs/AGENT_ARCHITECTURE.md` §3.4 now says that an LLM may at most *explain* a plan.

## Identity, side effects and persistence

- The record is always the authenticated principal's. `extra="forbid"` turns `student_id`, `account_id` and every other unknown field into a 422.
- A variant-only key returns 409, and `start_term` must come after every recorded term.
- Nothing is written. Tests compare every table's content hash before and after what-if plans.
- **No persistence and no migration.** Plans are generated on demand. `metadata` carries `planning_engine_version` 6.5.0, the audit engine version, the academic and rules fingerprints, and the SOC dataset identity, so staleness can be detected later.

## Measured on development data (`scripts/benchmark_planning.py`, 7 runs)

| Case | Placed | p50 | p95 | SQL statements |
|---|---|---|---|---|
| CS dev student | 14 | 0.82 s | 1.43 s | 142 |
| What-if, 7 programs | 8–21 | 0.24–1.09 s | 0.29–2.53 s | 138–216 |
| Empty record, CS | 35 | 5.21 s | 7.58 s | 261 |
| Empty record, Math Option A | 33 | 3.31 s | 5.39 s | 321 |

- **No N+1:** 7.5–25.6 statements per placed course, stable across warm runs. Prerequisite closures are prefetched one batch per level, which halved the statement count for large plans.
- **Deterministic:** every run was byte-identical.
- **Where the time goes:** about 77% of a large plan is the Degree Engine's exact global optimizer, at about 0.47 s per projected audit.

## Tests

- `ingestion/tests/test_planning_engine.py` (23 tests):
  - Uses real archived CS and Math SOC records loaded for four terms by the production pipeline, with the real curated programs.
  - Covers scenarios A–V: chains, nested AND/OR, minimum grade and retake, conditional future prerequisites, same-term co-requisite, PRE OR COREQ, unsupported co-requisite, confirmed/historical/no offering, UNKNOWN, dead end and horizon, variants, read-only what-if, determinism, and catalog-year and program-version isolation.
  - Includes an **independent oracle** that re-checks every placed course with `check_proposal`, counting only strictly-earlier terms as passed.
  - Synthetic rule edits are labelled, with the real Rutgers wording they imitate.
- `backend/tests/test_planning_domain.py` (18 tests): terms, dependency paths (AND/OR, AT LEAST, cycles, depth, order independence) and the progress measure.
- `backend/tests/test_planning_api.py` (13 tests, PostgreSQL): identity, no body field can select a student, constraints can't be relabelled as policy, honest errors, auth, read-only table hashes, and no-LLM checks (static and transitive).

**Failure injection** (`scripts/inject_planning_failures.py`): every listed fault, A through J plus C2, is detected. Two more faults are neutralised by the design, and are recorded as such rather than counted as detected: same-term picks counted as passed, and candidate pools from other versions.

**Full regression** (all green):

| Suite | Before (6.4 merge) | After |
|---|---|---|
| Root (PG) | 1412 passed, 5 skipped | 1466 passed, 5 skipped |
| Backend | 659 passed, 4 skipped | 690 passed, 4 skipped |
| Ingestion PG | 753 passed, 1 skipped | 776 passed, 1 skipped |
| Retrieval | 46 passed | 46 passed |
| Ingestion SQLite | 688 passed, 66 skipped | 711 passed, 66 skipped |

Each suite grew by exactly the number of new tests. One existing test was deliberately updated: `test_no_path_parameterised_student_route_exists` enumerates the student self-service routes so that adding one is a reviewed change, and it now lists `/student/plans/generate`. `alembic check` reports no drift at head `6ba645ee0f9c`.

## Not in this PR

- Sections, times, rooms, instructors and index numbers.
- WebReg, CSP and Degree Navigator.
- Transcripts.
- Polling and notifications.
- Frontend.
- Difficulty, grade or GPA prediction.
- Any LLM.
- Double-major sharing.
- Publishing or reviewing any program.

No credentials are collected and no Rutgers system is automated.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
