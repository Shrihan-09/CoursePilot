# Phase 6.2 — Prerequisite Graph, Retake Correctness & Multi-Term SOC Foundation

## Summary

This builds the deterministic prerequisite foundation the future Planning
Engine needs, fixes the retake bug found in Phase 6.1, and loads real
multi-term SOC history with honest coverage labels. There's no Planning
Engine, no new API, and no language model anywhere.

**Base:** `main` at `5ccda00` (PR #1, which brought in Phases 6.0 and 6.1;
both verified reachable from `origin/main`).

## Retake fix

**The bug:** two passing attempts of `01:198:314` filled two `CS_ELECTIVES`
slots and counted 8.0 credits. A permanent test reproduced it **before**
the fix (5 of 11 retake tests failing).

**The policy:** among a course's *usable* attempts, one representative is
chosen. Completed beats in-progress, then the most recent term wins. It is
deliberately not "highest grade wins", because Rutgers grade replacement
isn't modeled. Only the representative is allocated and credited. Other
attempts stay on the record, program rules still see them, and they produce
a `repeated_course` finding.

`AUDIT_ENGINE_VERSION` is now **6.2.0**. A test proves that a double-counted
audit cached under 6.0.0 is never served.

## Prerequisites

**The grammar is measured from all 1,354 distinct strings** in five archived
terms:

- `<em> OR/AND </em>` between groups and lower-case `or`/`and` inside them;
- titles are upper case and contain AND/OR and their own parentheses, so
  operator recognition is **case-sensitive**;
- the parser must read the **raw** text, because the existing normalizer
  strips the `<em>` tags that distinguish operators.

**Coverage:**

| | distinct strings | rows |
|---|---|---|
| parsed | **1,342 (99.1%)** | 3,992 (96.4%) |
| "Any Course EQUAL or GREATER Than" | 11 | 129 |
| unknown | 1 | 12 |
| malformed | 0 | 0 |

- **Round trip:** all 1,342 parsed strings parse back to the same tree from
  their canonical text.
- **Phase 6.1's 75.3%** was a regex over the tag-stripped text, and was an
  undercount.

**IR:** the node types are `CourseReq`, `AllOf`, `AnyOf`, `AtLeast` and
`Unsupported`, with a canonical form and JSON serialization.

**Evaluation is three-valued (Kleene logic).**

- A published `courseNotes` condition, such as "C or better in all
  prerequisites", is stored verbatim and caps SATISFIED at UNKNOWN. It can
  never raise a result.
- `check_many` uses 3 queries whatever the candidate count.

**Persistence:** `course_prerequisite` holds one row per `course_offering`,
so rows are term-scoped. Each row keeps the raw text and its sha256
alongside the interpretation, with a `data_source` link for provenance.

- `prerequisite_reference` keeps every referenced identity, with `course_id`
  set only when the course is known. No metadata is ever invented.
- **Real change:** 76 courses changed their prerequisites between the
  archived terms.

## Multi-term SOC

| term | coverage | courses | offerings | sections | prerequisites |
|---|---|---|---|---|---|
| Fall 2025 | complete | 4,410 | 4,421 | 12,100 | 1,174 |
| Spring 2026 | complete | 4,562 | 4,572 | 11,777 | 1,328 |
| Summer 2026 | complete | 1,046 | 1,046 | 1,698 | 373 |
| Fall 2026 | complete (as of fetch) | 4,391 | 4,400 | 11,992 | 1,236 |
| Winter 2027 | **partial**: pre-publication snapshot | 116 | 116 | 138 | 31 |

- **Coverage is recorded, not inferred:** `data_source.coverage` and
  `coverage_note` state it, and a limited or subject-filtered load can't
  claim `complete`.
- **Dev DB totals:** courses 4,415 → 7,484, offerings 4,516 → 14,555,
  sections 12,130 → 37,705, prerequisites 0 → 4,142.
- **Nothing student-related changed:** students, records, requirements and
  eligibility hash identically before and after.

## Defects found and fixed during this phase

1. **`CourseLoader` overwrote a course's current fields when an older term
   was loaded.** Now the newest term wins (6,756 courses were kept at newer
   values during the load).
2. **Reference resolution depended on load order.** Loading the archives
   oldest-first left 1,184 edges unresolved. A set-based late resolution
   fixed it (335 resolved).

## Migration

`ce2b9afd3fa7 → d839d664f019`. It's additive only: two tables and two
nullable columns. Upgrade, downgrade and re-upgrade were verified on a copy
of the dev DB with identical row counts, and `alembic check` is clean.

## Tests

| suite | before | after |
|---|---|---|
| root (PostgreSQL), 2 consecutive runs, 0 warnings | 1,137 passed / 5 skipped | **1,208 / 5** |
| backend | 502 / 4 | 550 / 4 |
| ingestion (PostgreSQL) | 635 / 1 | 658 / 1 |
| ingestion (SQLite) | 570 / 66 | 593 / 66 |
| retrieval | 46 | 46 |

**Failure injection:** every mutation was caught and the code restored.

- **AND evaluated as OR:** 7 tests fail.
- **Term scoping broken:** 1 test fails (thin coverage, noted).
- **Duplicate attempts allowed again:** 6 tests fail.

## Performance

- **Parser:** about 20,000 strings/s.
- **Checking one course:** 2.9 ms p50.
- **Checking 200 candidates:** 16.4 ms p50.
- **Checking all 4,400 Fall 2026 courses:** 196 ms p50, in 3 queries.

## Deferred

- **Minimum grades:** most CS prerequisites carry a grade note and evaluate
  to UNKNOWN until Phase 6.4.
- **"Any Course EQUAL or GREATER Than":** not interpreted.
- **Co-requisites:** available only as notes.
- **284 referenced identities:** they appear in no archived term.
- **Eligibility queries:** resolved when requirements load.
- **Repeat-for-credit courses:** not modeled.
- **`course.prereq_notes_raw`:** stripped and not parsed.

Docs: `DATA_MODEL.md` §37 and `LEARNING.md` Lesson 29.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
