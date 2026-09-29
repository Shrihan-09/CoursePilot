# Phase 6.0 — Multi-Program Degree Architecture, Program Discovery & Scenario Foundation

## Summary

The same Degree Engine now evaluates one student's real record against two
structurally different, source-backed Rutgers programs. It doesn't change the
student's actual state, has no major-specific branches, can't contaminate the
cache, and asks no language model anything.

- **Second program:** SAS **Mathematics B.A., Option A** now sits beside
  Computer Science. It was fetched from the official catalog, archived, and
  encoded with every node quoting its source sentence. It's marked
  `unverified` / `pending_review` because an AI assistant encoded it; see
  *Source authority*.
- **Engine:** one optional argument,
  `DegreeAuditEngine.audit(student, program_version=…)`. With no argument the
  output is byte-identical to before.
- **Scenarios:** a read-only scenario service, a deterministic comparison, and
  program discovery with natural keys.
- **API:** four authenticated endpoints. The client chooses a program and never
  a student.
- **No migration.**

## Architecture investigation

**Was the engine already generic?** Its logic was, but its entry point wasn't.

- There's no program branch anywhere, and every constraint is read from
  requirement data.
- But `audit(student)` could only read `student.program_version_id`. Evaluating
  another program would have meant writing a different major onto the student.
- The optimizer's internal baseline audit re-read the enrolled version too.
  That was found by mutation: making it wrong left every other test green. A
  dedicated test now pins it.

### CS-specific assumptions found

| where | classification | action |
|---|---|---|
| `DegreeAuditEngine.audit` read only the enrolled version | production coupling | optional `program_version` |
| `sas_core_26_27.json` `target_program` = CS | data assumption | `CoreIngestionPipeline.run(target_program=…)` |
| `PROGRAM_PATHS` held only CS | configuration | Mathematics added as data |
| CS mentions in engine docstrings | documentation | none; the behaviour is data-driven |
| `_CODE_ALLOWLIST`, search synonyms | generic configuration | none |
| CS test fixtures | test-only | kept; Mathematics added beside them |

A new test scans every string constant in `app/services/audit/` and finds no
subject codes, course keys or curated requirement codes.

## Second program: SAS Mathematics (640), Option A

**Why Mathematics:**

- it was confirmed from the catalog's own program index (97 pages archived);
- it has the most SOC rows among the candidates (49);
- its page parses cleanly;
- it exercises what the CS B.A. never does: choose-one-of-N, a CS course
  filling a *different* role (01:198:111), a ranged query with exclusions
  (491, 492), and a category constraint on a major requirement ("one
  analysis, one algebra").

**Source path:** Rutgers catalog index → `CatalogFetcher` (HTTP 200, both catalog years archived) → `__NUXT_DATA__` prose → `math_ba_requirements_26_27.json` → `RequirementLoader`. A test checks that every quoted sentence appears verbatim in the archive.

**Source authority:** the definition is `unverified` because an AI assistant
encoded it, and a model's reading isn't authoritative until a person checks it
against the prose. Program discovery reports it as `pending_review`, and every
scenario against it carries that assumption.

**Loaded into the development database:**

- 9 requirements, 41 eligibility rows and 1 non-evaluable residency rule;
- 4 named courses that aren't in the dev DB's SOC terms, **reported as
  unresolved and never created**;
- SAS Core attached, with 13 requirements and 714 eligibility rows;
- catalog descriptions for 62 entries per year, using the parser unchanged
  with 0 failures.

Every pre-existing student, record, account, CS requirement and CS eligibility
row hashed identically before and after.

**Not modeled:** per-course minimum grades, "all but one C or better",
graduate-course substitution, and Options B and C. The "411-412" sequence
reading is flagged as ambiguous for the reviewer.

## Scenario model

```
Student record ──┬── actual ProgramVersion ──> Degree Engine ──> actual audit   (cached)
                 └── target ProgramVersion ──> Degree Engine ──> scenario audit (never cached)
```

- **Read-only:** 15 tables hash identically before and after three scenarios
  plus a comparison. The service also refuses to return if its session has
  pending changes after evaluation.
- **Catalog year:** it defaults to the student's own year. It never silently
  picks the latest, and when the requested year is missing it lists the years
  that exist.
- **Assumptions** travel with every result: `hypothetical`,
  `admission_not_modeled`, `different_school`, `catalog_year_differs` and
  `requirements_pending_review`. Evaluating requirements is never presented as
  eligibility to transfer.

## Deterministic comparison

`compare_audits(actual, scenario)` is pure: it reads courses and requirements
off the two audits. Two requirements count as "the same" only when both the
code **and** the source prose match. Both real definitions contain a
`MATH_151`, so the code alone isn't enough. There's no ranking, no
recommendation and no time-to-degree estimate, and a test forbids fields
named like any of those.

**Real dev record, CS vs Mathematics:**

| | Computer Science (actual) | Mathematics (scenario) |
|---|---|---|
| status | incomplete | incomplete |
| leaf requirements satisfied | 9 + 1 provisional of 18 | 5 of 15 |

- **01:198:111** fills `CS_111` under CS and `MATH_COMPUTING` under Mathematics.
- **01:640:151, 152 and 250** apply under both programs.
- **Seven CS courses** apply only under CS.
- **All 13 SAS Core nodes** are shared.

## API

```
GET  /api/v1/programs
GET  /api/v1/programs/{program_key}           sas-640-ba
POST /api/v1/student/scenarios/audit          {"program_key", "catalog_year"?}
POST /api/v1/student/scenarios/compare
```

- **No UUIDs:** responses use natural keys only.
- **Ownership:** the student is resolved from the principal to the account to
  the owned student.
- **Extra fields are rejected:** `student_id`, `student_ref`, `external_ref`,
  `account_id`, `subject`, `user_id` and `principal` each return 422. That is
  tested for all seven.
- **A and B get their own records:** two students asking the same question each
  get their own record back.
- **Errors:** an unlinked account gets 409. The error taxonomy is unchanged.

## Cache correctness

- **Why scenarios bypass it:** `student_audit_cache` has one row per student.
  Its key includes the enrolled version, and `rules_token` is a database-wide
  counter, so a scenario would get **exactly** the actual audit's key.
- **What's tested:** the key has no target dimension (pinned), and the cache
  isn't contaminated after scenarios for two other programs. A mutation that
  writes a scenario under the actual key fails four tests.

## Defects found and fixed

1. **Loaders didn't refresh on reload.** A recuration that changed a count or
   the prose loaded "successfully" and changed nothing. The comparison exposed
   it: the dev DB's CS copy of `CORE_AH` held stale prose, so 12 of 13 Core
   nodes matched instead of 13. It's fixed in both loaders, and the dev Core
   was refreshed (0 inserts; only `CORE_AH`'s notes and prose changed).
2. **`satisfied_credits` was the int `0`** for an empty credit requirement.
   On the real record this happened in **every** audit (`CORE_NS`), with a
   Pydantic warning on each serialization. It's fixed, and
   `AUDIT_ENGINE_VERSION` is now 5.7.0 → 6.0.0.

## RAG and search

- **Corpus:** every course, with no program coupling. Adding Mathematics
  descriptions was a data operation and the index rebuilt once.
- **Finding, pre-existing and not fixed here:** code-shaped queries are
  tokenized as plain text, so `01:640:351` ranks fourth for its own code.
- **Explanations are unaffected:** the target's facts come from an exact key
  lookup.
- **Recommended fix:** use `tokenize_course_key` for code-shaped queries. It's
  lexical, so it doesn't justify adding embeddings.

## Performance (dev DB, service level, p50 / p95 ms)

| operation | p50 | p95 |
|---|---|---|
| actual audit, cold | 26.6 | 39.2 |
| actual audit, warm | 4.7 | 7.9 |
| scenario: Mathematics | 25.0 | 30.6 |
| scenario: own program | 25.5 | 31.3 |
| comparison | 34.3 | 45.1 |
| list programs | 6.7 | 14.7 |
| get program | 4.9 | 9.1 |

A scenario costs the same as a cold audit because it runs the same engine, so
no scenario cache was built.

## Tests

```
root (backend + ingestion, PostgreSQL)  1,142 collected: 1,137 passed, 5 skipped, 0 warnings  (x2)
                                        baseline was 1,094: 1,089 passed, 5 skipped
backend alone                           502 passed, 4 skipped    (was 470 / 4;  +32)
ingestion alone, PostgreSQL             635 passed, 1 skipped    (was 619 / 1;  +16)
ingestion, SQLite                       570 passed, 66 skipped   (was 554 / 66)
retrieval                               46 passed                (unchanged)
alembic check                           no new operations, head ce2b9afd3fa7
```

The 5 skips are unchanged: live SSO, live Anthropic, live AI opt-in, live
OpenAI (no key), and one CS archive with no AH courses.

Mutation checks: making the engine ignore `program_version` fails 9 tests.
Writing a scenario under the actual cache key fails 4. The nested-baseline
mutation used to pass everything, and now has its own test.

## Migrations

None. `alembic check` is clean and head is unchanged (`ce2b9afd3fa7`).

## Limitations

- **Mathematics is `pending_review`.**
- **Scope:** two programs in one school; different-school behaviour is tested
  on synthetic data only.
- **Not supported yet:** minors and double majors.
- **Not modeled:** admission and transfer rules, which catalog year applies
  after a change of major, and per-course minimum grades.
- **Eligibility is additive on reload:** a removed course isn't deleted.
- **Search:** ranking for code-shaped queries (above).
- **Location:** curated definitions live under `ingestion/tests/fixtures/`, as CS's did.

**CoursePilot doesn't support all Rutgers majors.** It supports one program
and has a second pending review.

## Next phase

**Phase 6.1: the deterministic Planning Engine.** Its inputs now exist for any
program: remaining requirements, `eligible_not_allocated`, and a scenario
target. Before it or alongside it, a person should verify the Mathematics
definition. The code-query tokenization fix is small and independent.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
