# Phase 6.6: Deterministic Rutgers Schedule Engine

## Summary

This phase adds a deterministic **section** scheduler for one Rutgers term. Given exact course codes, it returns ranked combinations of real section indexes that fit together. When nothing fits, it returns a structured explanation instead.

```
POST /api/v1/student/schedules/generate
{ "term": "20269", "courses": ["01:198:352", "01:198:334", "01:640:300", "01:750:193"],
  "preferences": { "earliest_start": "10:00", "avoid_days": ["F"],
                   "minimum_minutes_between_classes": 0, "preferred_campuses": ["BUS"] },
  "max_results": 10 }
```

What it never does:
- choose, add, drop or substitute a course;
- register, reserve or touch WebReg;
- write anything;
- call an LLM.

Architecture tests enforce this. The scheduler cannot import the planning or audit packages, or any LLM, retrieval or HTTP client module.

## What the real SOC data showed

The investigation is in `docs/investigations/phase-6-6-soc-scheduling-semantics.md`. It covers five archived NB terms.

- **One index is usually one complete registration.** For example, 1,024 Fall 2026 sections carry lecture and recitation rows under a single index.
- **Contradiction: some courses need two indexes.** Physics 01:750:193/194 publish a second, 0-credit "LB" lab record, with notes saying "MUST REGISTER FOR BOTH A REC & A LAB TOGETHER". These are scheduled as a required companion. Anything less explicit is reported, never bundled.
- **`sessionDates` was dropped at ingestion.** It is on all 1,698 Summer 2026 sections (the earlier "empty on 100%" figure measured Fall only). Two real M/W 18:00–22:00 summer classes in May–July and July do not conflict.
- **Structured restriction lists were dropped too.** `majors`, `minors`, `unitMajors` and `honorPrograms` had been kept only as `openToText` prose.
- **"No meeting time" means four different things:** asynchronous online, TBA, by arrangement, or a malformed time. Only asynchronous is treated as free time.

## Schema (migration `87bec76b788c`, additive)

- `course_section.session_dates_raw`, `session_start_date` and `session_end_date`, with a CHECK that start ≤ end.
- New table `section_restriction`.
- **Ingestion** now stores both.
- **Backfill:** re-running the section pipeline over the archived payloads added 19,033 restriction rows and dated 1,698 sections, and changed no other rows. A second run was a no-op.
- **Migration checks:** upgrade → downgrade → re-upgrade works on dev, and `alembic check` is clean.

## Engine

- **Conflicts:** compares every meeting of both sections, is date-aware, and lets back-to-back classes stand. The student's optional minimum break is the only buffer; CoursePilot assumes no travel time.
- **Unknown times:** TBA, arranged and malformed times are never free time. They produce `time_verified=false` plus `SCHEDULE_TIME_UNKNOWN`, and those options rank after verified ones.
- **Restrictions:** SATISFIED when the student's declared major, their school's unit ("01" for SAS, as SOC itself pairs them) or that unit/major pair is listed. Otherwise the result is UNKNOWN, because CoursePilot records no minors, second majors, honors membership or class standing. Special-permission sections and uninterpreted eligibility prose are UNKNOWN too. UNKNOWN is shown as needs-confirmation, never as eligible.
- **Course eligibility:** always rechecked through Phase 6.4's `check_proposal`. UNSATISFIED gives no options; UNKNOWN gives options plus a needs-confirmation issue.
- **Availability:** reported separately and labelled `archived_snapshot` with its timestamp. A closed section can still prove a schedule exists, and archived data does not reorder options.
- **Cross-listing:** a section is never chosen alongside its own cross-listed index.
- **Search:** constraint satisfaction with backtracking and forward checking, grouping of structurally identical sections, and branch and bound on the ranking. The bound is exact, and brute force verifies it in tests.
- **Ranking order (lexicographic):** needs-confirmation sections, unknown-time sections, preferred-campus misses, class days, gap minutes, closed sections (live data only), then index numbers. Results are called "ranked schedule options", never "best".
- **Preferences:** earliest start, latest end, avoided days and minimum break are hard constraints; preferred campuses is soft. All are labelled CoursePilot settings, not Rutgers policy.
- **When nothing fits:** the blockers name the conflicting course pair or which of the student's own constraints is responsible. Constraints are never relaxed automatically.
- **Unpublished terms:** a term with no section data returns `TERM_SCHEDULE_NOT_PUBLISHED`, and earlier terms are never borrowed.

## Measured (development data, Fall 2026, 15 runs)

| Case | Cartesian product | Search nodes | p50 / p95 | SQL statements |
|---|---|---|---|---|
| 3 courses | 108 | 74 | 31 / 41 ms | 13 |
| 4 courses + lab companion | 8,748 | 1,491 | 34 / 52 ms | 13 |
| 5 courses | 1,980 | 285 | 28 / 35 ms | 13 |
| 5 largest courses (engine stages) | 2,012,800,104 | 142,816 | 288 ms load + 373 ms search | 7 |

The statement count does not grow with section count (no N+1), and every run produced identical output.

## Tests

- `ingestion/tests/test_schedule_engine.py` (28 tests):
  - Uses whole, unmodified real SOC records loaded by the production pipelines, built by `scripts/make_schedule_fixture.py`.
  - Covers matrix cases A–Z, using real examples for both summer date cases, the lab companion, cross-listing, restrictions, asynchronous, TBA, arranged and malformed times, and a weekend meeting.
  - An independent conflict oracle re-checks every pair of meetings in every option.
- `backend/tests/test_schedule_domain.py` (35 tests): normalisation, overlap boundaries, buffers, date ranges, exact search against brute force, branch-and-bound top-K exactness, cross-listing and limits.
- `backend/tests/test_schedule_api.py` (14 tests, PostgreSQL): identity (no body field can select a student), validation bounds, authentication, an unlinked account, read-only table snapshots, no-LLM checks (static and transitive), and the no-course-choice architecture check.
- **Failure injection** (`scripts/inject_schedule_failures.py`): **16 of 16 faults (A–P) detected.**

**Full regression** (all green, no warnings):

| Suite | Before (6.5 merge) | After |
|---|---|---|
| Root (PG) | 1466 passed, 5 skipped | 1543 passed, 5 skipped |
| Backend | 690 passed, 4 skipped | 739 passed, 4 skipped |
| Ingestion PG | 776 passed, 1 skipped | 804 passed, 1 skipped |
| Retrieval | 46 passed | 46 passed |
| Ingestion SQLite | 711 passed, 66 skipped | 739 passed, 66 skipped |

Each suite grew by exactly the number of new tests. One existing test was deliberately updated: the reviewed list of student self-service routes now includes `/student/schedules/generate`. Phase 6.5 planning tests are unchanged and pass. `alembic heads` is `87bec76b788c` and `alembic check` is clean.

## Remaining limitations

- Restrictions on minors, second majors, honors and class standing stay UNKNOWN until CoursePilot records those attributes.
- No live availability yet. The `openSections` poller is a later phase.
- Final-exam conflicts are not checked; SOC gives only exam codes.
- Companion detection relies on an explicit note. Only 01:750:193/194 qualify in Fall 2026.

## Not in this PR

- Course selection, which stays with the Planning Engine.
- Polling, notifications and waitlists.
- WebReg, CSP and Degree Navigator.
- Professor or difficulty data.
- Travel-time models.
- Any LLM.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
