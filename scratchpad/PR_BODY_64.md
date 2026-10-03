# Phase 6.4: Academic Rule Semantics & Course Eligibility

## Summary

This phase adds deterministic academic-rule primitives that a Phase 6.5 Planning Engine can call. There are two separate domains, and they share one grade module:

| Domain | Question | Code |
|---|---|---|
| Degree completion | Does a completed course count toward a requirement? | `app.services.audit` (engine 6.4.0) |
| Course eligibility | May the student take course X in term T alongside courses P? | `app.services.prerequisites`, `app.services.course_eligibility` |
| Shared grade semantics | — | `app.domain.grades`, `attempts`, `gpa` |

Neither domain imports the other, and a test asserts this. No language model decides any rule. Every policy is cited to an archived public Rutgers page from the SAS 2026-27 catalog: Grades and Records, Academic Credit, and Repeating Courses.

### What's new

**Grades.** The published Rutgers scale is A=4.0, B+=3.5, B=3.0, C+=2.5, C=2.0, D=1.0, F=0.0.
- P counts as A–C, so it meets "C or better" but leaves "B or better" UNKNOWN.
- NC counts as D or F.
- T grades, NG, H, S/U and XF are handled as the policy page defines them.
- Transfer and exam credit complete a course but can't prove a grade, and never enter the GPA.
- Unrecognized symbols are UNKNOWN. That includes `D-`, which the old failing list contained but Rutgers doesn't use.

**Retakes.** Each question has its own policy:
- **Degree credit:** the SAS rule applies. Repeating a course already passed with C or better gives E credit.
- **Prerequisites:** any attempt that earned the grade counts.
- **GPA:** UNKNOWN wherever the answer depends on a credit prefix CoursePilot doesn't store.

**Degree completion.**
- **Minimum grades:** applied before allocation, with evidence for every decision.
- **Grade quotas:** for example "All but one … C or better", enforced by a deterministic repair after allocation.
- **Course sequences:** "411-412" means both courses, by the page's own "151-152 … three semesters". No order is enforced, because none is stated.
- **D-count rules:** the existing rule now counts courses applied to the major, once each, instead of every attempt on the record.
- **GPA rules:** "GPA in the major" is encoded but refused (`NOT_EVALUABLE`), because Rutgers doesn't define which courses it covers.

**Options.** A key without a variant, for a program that exists only as variants, returns 409 listing the variants. CoursePilot never picks an option.

**Course eligibility.**
- Minimum-grade prerequisite notes are read with three scopes: all, named or unspecified.
- "OR PLACEMENT / PERMISSION / EQUIVALENT" alternatives are recognized.
- Section notes are read when every section publishes the same text.
- Co-requisites come in three forms: "PRE OR COREQ", "COREQ" and "MUST BE TAKEN CONCURRENTLY".
- `check_proposal(student, courses, term, proposed)` evaluates one proposed term, with a fixed query count.

**Schema.** Migration `6ba645ee0f9c` is additive, and its CHECK constraints are only widened. The downgrade refuses, rather than deletes, rows the old constraints can't hold. The round trip on a copy of the dev database was lossless.

## Measured effects

**Prerequisites in Fall 2026, synthetic students:**

| History | Before | After |
|---|---|---|
| B in every referenced course | 1,158 SAT / 75 UNK (1,233 offerings) | 1,113 SAT / 194 UNK (1,307) |
| D in every referenced course | 1,158 SAT / 75 UNK | 1,081 SAT / **31 UNSAT** / 195 UNK |

- A student with a D no longer satisfies a "C or better" prerequisite.
- UNKNOWNs caused by `courseNotes` fell from about 42 to 5.
- 156 new UNKNOWNs come from uniform section notes that were previously ignored, such as "PREREQ: SENIOR STATUS", ROTC requirements, committee approval and "OR EQUIVALENT".

**Condition reading coverage (5 terms):**
- Minimum grade interpreted for 75 offerings; alternatives for 80.
- 503 offerings keep a published condition that isn't interpreted, so it stays UNKNOWN.
- Co-requisite clauses: 116 parsed, 111 unsupported.

**Curated programs:**
- Mathematics, Economics, Statistics, Sociology and Linguistics changed their definitions. They moved validated → parsed on load, and back to validated by the deterministic checks only.
- CS, Philosophy and Psychology are unchanged.
- No program was reviewed or published.

## For human review

- **Mathematics:** confirm the sequence encoding of 411-412 and 451-452, and the "all but one" quota scope.
- **Economics:** the "GPA in the major" rule is refused by design.
- **Engine semantic change:** CS and Philosophy D-count rules now count courses applied to the major, not every attempt on the record. Their definitions are unchanged.
- **Co-requisites:** 67 offerings with section-specific co-requisites aren't loaded; that belongs to the Schedule Engine.

## Tests

| Suite | Before | After |
|---|---|---|
| Root (Postgres) | 1265 passed / 5 skipped | 1412 / 5 |
| Backend | 553 / 4 | 659 / 4 |
| Ingestion (Postgres) | 712 / 1 | 753 / 1 |
| Ingestion (SQLite) | 647 / 66 | 688 / 66 |
| Retrieval | 46 | 46 |

`alembic check` is clean. There are no warnings.

**Failure injection: all 9 mutations caught.**

| Mutation | Result |
|---|---|
| A: C-or-better accepts D | caught (15 tests) |
| B: prerequisite minimum grade ignored | caught (4) |
| C: an UNKNOWN grade scope is treated as SATISFIED | caught (2) |
| C2: an UNKNOWN degree grade is treated as SATISFIED | caught (19) |
| D: GPA boundary | caught (1) |
| E: duplicate attempts counted in a grade quota | caught (2) |
| E2: node quota limit ignored | caught (1) |
| F: co-requisite ignored | caught (3) |
| G: options collapsed into one | caught (2) |

## Out of scope

Planning Engine, scheduling, recommendations, transcript import, admission evaluation, publication of validated programs.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
