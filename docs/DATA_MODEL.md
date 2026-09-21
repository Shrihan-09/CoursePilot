# CoursePilot Data Model

Status: **partly implemented.** Two migrations are applied and verified on
PostgreSQL 16.

| Area | State |
|---|---|
| `data_source`, `subject`, `course`, `course_offering` | **implemented** (`27348b5d362d`) |
| `course_section`, `section_meeting`, `section_instructor`, `section_cross_listing` | **implemented** (`ef89e1066a73`) |
| Section identity `(term_code, index_number)` | **verified across 5 terms, 2 loaded together** (Phase 2.5 / 2.75) |
| `school`, `program`, `program_version`, `requirement`, `requirement_course_option`, `catalog_course_entry` | **implemented** (`437d81e0faef`) |
| `program_rule` (grade / exclusion / residency) | **implemented** (`62b929a5bd2e`) |
| `requirement.requirement_system`, `program_version.sharing_policy` | **implemented** (`be0ed2042e56`) |
| SAS Core: 13 goals, 13 nodes, 616 eligibility rows | **implemented, NO migration required** |
| `catalog_course_entry` populated, `course_id` nullable | **implemented** (`62b929a5bd2e`) |
| `student`, `student_course` (minimal, no auth) | **implemented** (`437d81e0faef`) |
| personal events, equivalencies, prerequisite structure | **design only** |

Column types for unimplemented tables are indicative. Anything marked
**TODO(rutgers-source)** must be confirmed against authoritative Rutgers
documentation before implementation.

---

## 1. Design constraints

Four properties drive the entire schema:

1. **Everything Rutgers-derived is term-scoped.** A prerequisite, a
   requirement, a credit count — all can differ by catalog year. A schema
   without a time dimension will produce confidently wrong audits.

2. **Everything Rutgers-derived carries provenance.** Non-negotiable, and
   effectively impossible to retrofit.

3. **Requirement structures vary by program.** Rutgers has many schools. Their
   degree requirements are not uniformly shaped. A schema hard-coding one
   program's structure will break on the second program. This is why
   requirements are modeled as a **recursive tree with typed rules** rather
   than as fixed columns.

4. **Student-reported data is not authoritative.** A student typing "I took
   Calc 1" is evidence, not fact. It is stored with a different source kind
   and a different verification status than a registrar record.

---

## 2. Entity map

```
                  ┌──────────────┐
                  │ data_source  │◄──── referenced by nearly every table
                  └──────────────┘

  school ──< program ──< program_version ──< requirement (recursive tree)
                                                  │
                                                  ├──< requirement_rule
                                                  └──< requirement_course_option
                                                              │
  subject ──< course ────────────────────────────────────────┘
     │           │
     │           ├──< course_prerequisite (expression tree)
     │           ├──< course_equivalency
     │           └──< course_offering ──< course_section ──< section_meeting
     │
  student ──< student_program
     │
     ├──< student_completed_course ──► course
     ├──< student_planned_course ────► course
     ├──< requirement_satisfaction ──► requirement
     └──< personal_event ──< personal_event_occurrence
```

---

## 3. Provenance

### `data_source`

One row per distinct source document or feed.

| Column | Type | Notes |
|---|---|---|
| `id` | uuid PK | |
| `kind` | enum | `SourceKind` in `app/domain/provenance.py` |
| `url` | text | canonical source URL |
| `title` | text | |
| `retrieved_at` | timestamptz | when CoursePilot fetched it |
| `source_last_modified` | timestamptz | as reported by the source |
| `content_hash` | text | detects change without re-parsing |
| `raw_payload_ref` | text | pointer to the archived raw response |
| `academic_year` | text | e.g. `"2026-2027"` |
| `term_code` | text | **TODO(rutgers-source)**: official format |
| `version` | int | monotonic per logical source |
| `superseded_by` | uuid FK → data_source | set when a newer version lands |

### Attaching provenance

Every table holding Rutgers facts carries:

```
source_id        uuid FK -> data_source   NOT NULL
verification     enum                     NOT NULL DEFAULT 'unverified'
confidence       numeric(3,2)             NULL   -- parsed/derived records only
valid_from       date
valid_to         date                     NULL   -- NULL = currently in effect
```

**Why a FK rather than a join table:** one record derives from one source.
When two sources disagree, that is not a many-to-many relationship — it is a
conflict, and it gets its own row with `verification = 'conflicted'` plus a
`source_conflict` record naming both. Modeling conflict as multi-source
membership hides it; modeling it as a distinct state surfaces it.

### Source precedence

When sources disagree: official API > official catalog > department page >
PDF > manual curation. Student self-reported data never overrides any of them;
it is stored separately as claims about a student, not as facts about Rutgers.

---

## 4. Academic structure

### `school`
`id`, `code`, `name`, `campus`, provenance.

**TODO(rutgers-source):** the authoritative list of Rutgers schools and their
codes.

### `program`
A degree program (major, minor, certificate).

`id`, `school_id` FK, `code`, `name`, `degree_type` (BA/BS/minor/certificate —
**TODO(rutgers-source)**), `is_active`, provenance.

### `program_version`
**The key to correctness over time.** A student is bound to the requirements
in effect when they matriculated, not today's.

`id`, `program_id` FK, `catalog_year`, `effective_from`, `effective_to`,
`total_credits_required`, provenance.

Unique on `(program_id, catalog_year)`.

Without this table, a returning student's audit silently uses the wrong rules.

---

## 5. Requirements — the hard part

Rutgers programs do not share a requirement shape. Some are "take these 10
courses." Some are "take 4 from group A, 2 from group B, and 12 credits at
300+ level." Some allow one course to satisfy two categories; some forbid it.

Modeling this as columns fails immediately. It is modeled as a **recursive
tree of typed rule nodes**.

### `requirement`

| Column | Type | Notes |
|---|---|---|
| `id` | uuid PK | |
| `program_version_id` | uuid FK | |
| `parent_id` | uuid FK → requirement | NULL at root; makes the tree |
| `code` | text | stable, human-usable label |
| `name` | text | as published |
| `description` | text | |
| `rule_type` | enum | see below |
| `sort_order` | int | preserve published ordering |
| provenance | | |

### `rule_type`

| Value | Meaning |
|---|---|
| `all_of` | every child requirement must be satisfied |
| `any_of` | at least `min_count` children must be satisfied |
| `n_of` | exactly/at least N children |
| `credits` | accumulate ≥ N credits from the option set |
| `course_list` | satisfied by specific listed courses |
| `min_gpa` | GPA threshold |
| `residency` | credits that must be taken at Rutgers |
| `custom` | escape hatch — carries prose + `requires_human_review` |

`custom` exists because real catalogs contain rules that resist formalization
("with departmental approval"). The honest response is to represent it, flag
it, and have the validator return `INDETERMINATE` rather than pretend to
check it.

### `requirement_rule`

Parameters for the node, kept separate so rule types can have different
parameters without a wide, mostly-null `requirement` table.

`requirement_id` FK, `min_count`, `max_count`, `min_credits`, `min_grade`,
`min_level`, `subject_filter`, `params` (jsonb for rule-type-specific extras).

### `requirement_course_option`

Which courses can satisfy a leaf requirement.

`requirement_id` FK, `course_id` FK, `credits_applied` (may differ from the
course's own credits), `notes`, provenance.

### Double-counting

**Implemented in Phase 3.75 — see §14.** The original sketch here was a
pairwise `requirement_sharing_policy(requirement_a, requirement_b)` table. It
was not built, because the real rule turned out not to be pairwise: SAS
permits sharing between *systems* (core vs major), not between named
requirement pairs. A pairwise table would have needed O(n²) rows to express a
one-line policy.

What was built instead: a `requirement_system` on each requirement, and a
single `sharing_policy` on the program version.

The conservative default survives unchanged — **no stated policy means no
sharing**.

**TODO(rutgers-source):** per-program sharing policies beyond SAS core.

---

## 6. Courses

### `subject`
`id`, `code`, `name`, `school_id` FK, provenance.

**TODO(rutgers-source):** Rutgers course codes have internal structure
(unit/subject/course). The exact segmentation must be confirmed, not guessed —
it determines primary key design here.

### `course`

`id`, `subject_id` FK, `course_number`, `full_code` (display), `title`,
`description`, `credits` (numeric — variable-credit courses need
`credits_min`/`credits_max`), `level`, `is_active`, `first_seen_term`,
`last_seen_term`, provenance.

A course is a **catalog** entity; it exists independently of whether it is
offered in any term.

### `course_prerequisite`

Prerequisites are boolean expressions ("A and (B or C), or permission of
instructor"), not lists. Stored as an expression tree:

| Column | Notes |
|---|---|
| `id` | |
| `course_id` FK | the course being gated |
| `parent_id` FK → self | tree structure |
| `node_type` | `and` / `or` / `not` / `course` / `min_grade` / `credits` / `standing` / `permission` / `unparsed` |
| `required_course_id` FK | for `course` nodes |
| `min_grade` | |
| `relation` | `prerequisite` / `corequisite` / `pre_or_coreq` |
| `raw_text` | the original published text, always kept |
| provenance | |

Two deliberate choices:

- **`raw_text` is always retained.** Prerequisite prose is hard to parse. When
  the parse is wrong, we need the original to compare against — and the UI can
  show it to the student.
- **`unparsed` is a first-class node type.** When parsing fails, we record
  that we could not parse it. The validator then returns `INDETERMINATE` for
  that course. It does *not* return "prerequisites satisfied," which is what an
  empty prerequisite list would wrongly imply.

### `course_equivalency`

`course_id`, `equivalent_course_id`, `direction` (bidirectional or one-way),
`context` (transfer credit, renumbering, cross-listing), provenance.

Directionality matters: a renumbered course substitutes for its predecessor,
but not always in reverse.

---

## 7. Offerings and scheduling

### `course_offering`
A course actually offered in a term. `course_id` FK, `term_code`, `campus`,
provenance. Unique on `(course_id, term_code, campus)`.

### `course_section`
`offering_id` FK, `section_number`, `index_number` (**TODO(rutgers-source)**:
Rutgers section identifier semantics), `instructor`, `capacity`,
`enrolled_count`, `status`, `instruction_mode`, `restrictions`, provenance.

`capacity`/`enrolled_count` are **observations at a point in time**, not live
state. They will be stale. Any registration-likelihood feature must treat them
as historical samples with timestamps, never as current availability.

### `section_meeting`
One meeting pattern. A section may have several (lecture + recitation).

`section_id` FK, `day_of_week`, `start_time`, `end_time`, `building`, `room`,
`campus`, `meeting_type`, `start_date`, `end_date`, provenance.

Modeled as separate rows rather than a "MWF 10:20-11:40" string because
conflict detection needs comparable intervals. Parsing that string at
validation time would put string handling in the correctness path.

**Campus is part of conflict detection.** Two Rutgers classes twenty minutes
apart on different campuses may not both be attendable even without a time
overlap. A travel-time model is needed — **TODO(rutgers-source)**: official
inter-campus travel times.

---

## 8. Student data

### `student`
`id`, `external_ref` (nullable), `expected_graduation_term`, `created_at`.

Deliberately thin. Student PII is a liability; store the minimum. Auth is not
designed yet and must be before this table holds real people.

### `student_program`
`student_id`, `program_version_id` FK, `role` (primary major / second major /
minor), `declared_on`.

FK targets `program_version`, not `program` — that is what binds a student to
the catalog year whose rules govern them.

### `student_completed_course`
`student_id`, `course_id` FK, `term_code`, `grade`, `credits_earned`,
`source_kind` (self-reported vs. imported), `verification`.

Self-reported entries carry `verification = 'unverified'` and the UI must say
so. A degree audit built on unverified input is a projection, not a record.

### `student_planned_course`
`student_id`, `course_id`, `intended_term`, `section_id` (nullable),
`plan_id`, `status`.

### `requirement_satisfaction`
A **derived, cached** audit result: `student_id`, `requirement_id`,
`status` (satisfied / partial / unsatisfied / indeterminate),
`credits_applied`, `satisfying_course_ids`, `computed_at`, `computed_from_version`.

It is a cache, and must be recomputable from scratch. `computed_at` and
`computed_from_version` exist so a stale audit is detectable rather than
silently trusted.

### `personal_event` / `personal_event_occurrence`
Student commitments that constrain scheduling.

`personal_event`: `student_id`, `title`, `recurrence_rule` (RFC 5545 RRULE),
`start_time`, `end_time`, `timezone`, `is_hard_constraint`.

`personal_event_occurrence`: expanded concrete instances, so conflict
detection compares plain intervals instead of re-expanding recurrence rules
inside the validator.

`is_hard_constraint` distinguishes "I work Tuesday mornings, immovable" from
"I'd rather not have 8am classes" — hard vs. soft constraints for the
optimizer.

---

## 9. Retrieval support

### `document_chunk`
Chunked text for retrieval. `id`, `entity_type`, `entity_id`, `chunk_text`,
`chunk_index`, `token_count`, `embedding vector(N)`, `tsv tsvector`, `source_id`.

One table serves both retrieval paths: a pgvector column for semantic search
and a `tsvector` column with a GIN index for keyword search. Keeping them
together guarantees both retrievers see the identical corpus, which is a
precondition for comparing them fairly.

`entity_type`/`entity_id` are the point: retrieval returns **pointers to
authoritative rows**, not free-floating text. The planner then works with real
database entities.

**Open:** embedding dimension depends on the (not yet chosen) embedding model.
`EMBEDDING_DIM` defaults to 1024 as a placeholder. Changing it later requires
re-embedding the corpus.

---

## 10. Indexing notes

- `course.full_code` — unique + `pg_trgm` GIN for fuzzy code matching
- `course_offering (term_code, course_id)` — the hot planning lookup
- `section_meeting (section_id, day_of_week)` — conflict detection
- `document_chunk.embedding` — HNSW; build **after** bulk load, not before
- `document_chunk.tsv` — GIN
- `requirement.parent_id` — recursive CTE traversal
- Partial index on `course` where `is_active` — most queries only want active

---

## 11. Open questions

Updated after the ingestion investigation (2026-09-08). See
`docs/DATA_SOURCES.md` for the measurements.

### Resolved by observation

| # | Question | Answer |
|---|---|---|
| 1 | Term code format | `<year><term>`, e.g. `20269` = Fall 2026. Adopted from SOC's own `effective` field. Term digits 9/1/7/0; **only 9 verified**. |
| 2 | Course code segmentation | `unit:subject:course`, e.g. `01:013:120`. 0 malformed in 4,400 records. |
| 4 | Are prerequisites structured? | **No.** Prose with embedded HTML, present on 28% of courses. Stored verbatim as `prereq_notes_raw`; parsing deferred. |
| 5 | Is there an official API? | Yes: `classes.rutgers.edu/soc/api/courses.json`. Undocumented, unversioned, no auth. **Terms of use still unknown.** |

### Still open — `TODO(rutgers-source)`

| # | Question | Blocks |
|---|---|---|
| 3 | Where are degree requirements authoritatively published, per school? | The entire requirement engine |
| 5b | Terms of use / acceptable-use policy for the SOC endpoint | Scaling ingestion beyond a prototype |
| 6 | Actual double-counting policies per program | Requirement satisfaction |
| 7 | Inter-campus travel times | Schedule feasibility |
| 9 | Authoritative source for course **descriptions** | Semantic search (SOC has none) |
| 10 | Full set of valid term and campus codes | Multi-term ingestion |

Item 8 (embedding model, which fixes the vector dimension) is ours to decide,
and should be settled before the first embedding is written.

---

## 12. Sections — IMPLEMENTED (Phase 2)

Four tables, each justified by a measured one-to-many in the source. See
`docs/DATA_SOURCES.md` §1b for the numbers and
`backend/app/models/sections.py` for the code.

```
course_offering  (course + term + campus)
      |
      | 1..n
      v
course_section ──┬── 1..5 ──> section_meeting
                 ├── 0..2 ──> section_instructor
                 └── 0..n ──> section_cross_listing
```

### Why sections hang off `course_offering`, not `course`

A section only exists within a specific term at a specific campus. Measured:
section `campusCode` equals the parent course's on 11,992 of 11,992 records,
and the NB/OB duplicate course `16:400:513` carries a distinct section index
per campus (19370 / 19371). Attaching sections to `course` would make those
two indistinguishable.

### `course_section`

**Natural key: `(term_code, index_number)`** — UNIQUE.

`index_number` is the Rutgers registration index, measured unique across all
11,992 sections in the term.

`term_code` is part of the key, and this is now **VERIFIED, not defensive**
(Phase 2.5, 2026-09-12, five real terms measured).

Rutgers reuses registration indexes aggressively, and a reused index usually
denotes a *different course*:

| Pair | Shared indexes | → different course |
|---|---|---|
| Spring 2026 vs Fall 2026 | 9,829 (83.5%) | 9,330 (94.9%) |
| Fall 2025 vs Fall 2026 | 8,664 (71.6%) | 8,664 (**100%**) |

Concrete: index `10193` is `01:070:111` sec 01 in Fall 2026 and `01:013:130`
sec 01 in Spring 2026.

Within any one term the index is perfectly unique (verified on all five
terms). So the index is a **term-scoped identifier with no cross-term
meaning**. Keying on `index_number` alone would have silently collided ~83% of
sections on the second term ingested and attached them to the wrong courses.

Pinned by `tests/test_cross_term_identity.py` (5 tests), three PostgreSQL
tests in `tests/test_postgres_sections.py`, and `tests/test_multi_term_ingestion.py`
(7 tests covering two terms through the full pipeline).

**Confirmed in the live database (Phase 2.75):** Fall 2026 and Winter 2027
loaded together produced 21 shared registration indexes, each yielding 2
sections in 2 terms pointing at 2 different courses - zero collisions. A
deliberate duplicate `(term_code, index_number)` is still rejected by
`uq_section_term_index`.

#### Known limitation: course attributes drift between terms

Measured on 2,159 courses present in both Fall 2026 and Spring 2026:

| Field | Differs across terms |
|---|---|
| `preReqNotes` | 94 (4.4%) |
| `title` | 23 (1.1%) |
| `credits` | 3 (0.1%) |
| `level` | 0 |

e.g. `16:640:591` is 3 credits in Fall 2026 and 4 in Spring 2026.

`credits`, `title`, and `prereq_notes_raw` live on `course`, which is
term-independent, so the most recently ingested term wins. For a degree audit
that is wrong in principle: a student who took the 3-credit Fall offering
would be credited with whatever the latest term says.

**Not changed now**, deliberately. At 0.1% for credits this does not meet the
bar for redesigning the course/offering split, and moving credits to
`course_offering` would complicate every query that needs "the credits for
this course" without a term in hand. The honest position is that this is a
recorded limitation with a known trigger: **if a degree audit needs
historically accurate credits, credits must move to (or be shadowed on)
`course_offering`.** Revisit in Phase 4.

`term_code` is denormalized from `course_offering` so this can be a
single-table UNIQUE constraint; enforcing it across tables would need a
trigger for the same guarantee.

A second UNIQUE on `(offering_id, section_number)` is also measured-true and
catches a renumbering that reuses an index.

CHECK: `index_number ~ '^[0-9]+$'` — **PostgreSQL-only**, declared with
`ddl_if(dialect="postgresql")` because `~` is a Postgres operator and the fast
unit tests build a throwaway SQLite schema. Full strength where it matters,
rather than deleting the constraint to make tests run.

### `section_meeting`

**Natural key: `(section_id, meeting_index)`** — the ordinal position.

Not the attribute tuple, for two measured reasons:

* 36.8% of meetings have NULL day and time. In SQL `NULL != NULL`, so a UNIQUE
  constraint containing them would silently permit unlimited duplicates — the
  same trap `supplement_code` hit in Phase 1.
* An ordinal is always present and always comparable.

Tradeoff: if Rutgers reorders a section's meetings, rows are rewritten in place
rather than duplicated. Row counts stay correct, which is what idempotency
requires here.

Nullable: `meeting_day`, `start_time_military`, `end_time_military`,
`building_code`, `room_number`, `campus_*`. Not nullable:
`meeting_mode_code` (100% populated).

CHECKs: `meeting_day IN ('M','T','W','H','F','S','U') OR NULL`; 4-digit time
format (Postgres-only); `meeting_index >= 0`; and
`(start IS NULL) = (end IS NULL)` — measured true on all 17,457 rows.

**There is deliberately NO `end_time > start_time` CHECK.** Three real Rutgers
meetings violate it. Enforcing it would reject authentic data; the validator
warns instead.

### `section_instructor`

**Natural key: `(section_id, instructor_index)`** — ordinal, because 53
sections legitimately list the same name twice, so `(section_id, name)` is not
unique.

**No shared `instructor` entity.** The source provides only a display name —
no id, no email, no netid — and `WANG, HAO` appears 92 times across 4,004
distinct names. Merging on name would assert that every `WANG, HAO` is the
same person, which the data cannot support.

### `section_cross_listing`

**Natural key: `(section_id, registration_index)`** — measured unique, and
`registrationIndex` is populated on all 966 references.

Stores identifier **components, not a foreign key**: 36 of 966 references
point at a section outside this campus/term payload. An FK would force us
either to drop real data or to fabricate the missing section.

### Not modeled, and why

| Field | Reason |
|---|---|
| capacity / enrolled / waitlist | **SOC provides none of these.** Columns would invite fabricated values. |
| `sessionDates`, `subtopic`, `legendKey` | Empty on 100% of 11,992 sections |
| `printed` | Constant `'Y'`; carries no information |
| `majors` / `minors` / `unitMajors` / `honorPrograms` | Real one-to-many restriction lists; deferred. Human-readable forms preserved in `open_to_text` / `section_eligibility` |

---

### Implementation status

Implemented in Phase 1: `data_source`, `subject`, `course`, `course_offering`.

Implemented in Phase 2: `course_section`, `section_meeting`,
`section_instructor`, `section_cross_listing`.

The rest of this document remains a design. Note that the implemented `course`
table differs from §6 above in one respect: the natural key includes
`supplement_code`, and campus was moved to `course_offering`. Both changes were
forced by measured duplicates in the real payload - see the `Course` docstring
in `backend/app/models/academic.py`.

---

## 12. Requirements as implemented (Phase 3)

The design in §5 above survived contact with real data, with three changes
forced by the measured Rutgers CS prose. Implemented shape:

```
School
  └── Program                     (code + degree_type; BA and BS are DIFFERENT programs)
        └── ProgramVersion        (catalog_year - the time dimension)
              └── Requirement     (recursive tree)
                    └── RequirementCourseOption   (ELIGIBILITY, not satisfaction)

Course (Phase 1, reused)
  └── CatalogCourseEntry          (per-catalog-year description/title/credits)

Student
  └── StudentCourse               (completed | in_progress | planned)
```

### Requirement node types

Each exists because a real clause needed it - none is speculative:

| Type | Real clause |
|---|---|
| `all_of` | "six required courses ... 111, 112, 205, 206, 211, and 344" |
| `any_of` | "four physics courses ... or chemistry ..." |
| `choose_n` | "five electives from a designated list" |
| `credits` | "requires 51-55 credits" |
| `course` | a single required course (leaf) |

### Three changes from the original design

1. **`degree_type` is part of program identity.** The CS page defines a B.A.
   and a B.S. with different requirements. They are two programs a student can
   be in, not one program with a flag.

2. **Credit totals are ranges.** The prose says "51-55 credits", so
   `total_credits_min`/`max`, guarded by a CHECK that max >= min.

3. **Group constraints are columns, not separate rules.** The elective clause
   carries two constraints inseparable from the group itself:
   `max_outside_subject` + `constraint_subject_code`, and `min_at_level` +
   `min_at_level_count`. Modeling them as sibling requirements would let a
   student satisfy the constraint without satisfying the group.

### Curation, not extraction

Rutgers publishes requirements as prose, so every requirement row carries
`source_prose`, `source_url`, and `curation_status`. The enum has
`curated_from_prose`, `synthetic`, and `unverified` — and deliberately **no
`extracted`**, because nothing in the catalog is machine-extractable as
structure. A curated requirement must never be presentable as though Rutgers
published it in this shape.

### Eligibility vs satisfaction

`requirement_course_option` says only *"course X CAN satisfy requirement Y."*
It contains nothing about any student. Satisfaction is **computed**, never
stored, from (eligibility + student record + rules) — so a rule change
re-audits every student correctly without a data migration.

A course may be eligible for many requirements; it is **allocated** to at most
one. That gap is why allocation is a real problem rather than a lookup.

### Known limitation: course attribute drift (carried from Phase 2.5)

`credits`, `title`, and `prereq_notes_raw` live on `course` (term-independent),
so the most recently ingested term wins. `catalog_course_entry` now fixes this
for *catalog* data by versioning per catalog year, but the SOC-derived `course`
columns remain last-write-wins. Trigger for revisiting: a degree audit that
needs historically accurate credits.

### Not yet modeled

Grade rules ("no more than one D"), course exclusions ("no credit for 105,
107, 110, 142, 170, 405"), and residency ("seven courses in the Rutgers-NB
department") are recorded in the curated fixture under `rules_not_yet_modeled`
and are **not evaluated**. They are program-wide rules that do not fit a
per-node tree, and each needs its own representation.

---

## 13. Program-level rules (Phase 3.5)

Three real Rutgers CS clauses cannot be expressed by the requirement tree,
because the tree evaluates nodes independently while these span the whole
degree:

| Rule | Prose | Type |
|---|---|---|
| At most one D | "No more than one grade of D can be accepted in the courses required for the major." | `max_grade_count` |
| Excluded courses | "Declared computer science majors (198) will not receive credit ... for ... 105, 107, 110, 142, 170, or 405." | `course_exclusion` |
| Residency | "A minimum of seven courses must be taken in the Rutgers University-New Brunswick Department of Computer Science." | `residency` |

### Why `program_rule` is not a `Requirement`

A requirement is *satisfied by courses*; a rule *constrains how courses count*.
Modeling "no more than one D" as a requirement node would mean inventing a node
that no course can satisfy.

### `is_evaluable`: authoritative but uncheckable

The residency rule is real and published, but evaluating it needs to know which
courses were taken **at Rutgers-New Brunswick** rather than transferred in.
`student_course` records no transfer provenance, and a cross-listed course
carries another department's code. Rutgers publishes no transfer-equivalence
rule we could apply.

So the rule is stored with `is_evaluable = False` plus a required
`not_evaluable_reason` (enforced by a CHECK), and the audit reports
`NOT_EVALUABLE`. Guessing would be worse than admitting the gap.

### Exclusion is program-scoped, never global

An excluded course is **not** deleted, flagged, or altered. `01:198:405`
remains a valid `Course`, remains *eligible* for the elective requirement, and
may count fully toward a different program. The exclusion lives on the
`ProgramVersion` and is applied at audit time - to allocation and to credit
counting only.

### Completed credits vs degree-applicable credits

```
credits_completed              everything passed, including excluded courses
credits_applicable_to_degree   what actually counts toward THIS degree
credits_excluded               the difference, itemised
```

`credits_remaining` is computed from **applicable** credits. Counting excluded
courses toward the total would tell a student they are closer to graduating
than they are.

### `catalog_course_entry.course_id` is nullable

Measured: 13 of 44 CS catalog courses have no `course` row - the catalog is a
superset of what SOC offers in any term. The natural key is therefore
`(course_string, catalog_year)`, with `course_id` a nullable link backfilled
if SOC later offers the course. See `DATA_SOURCES.md` §3 for the per-course
classification.

---

## 14. Requirement sharing (Phase 3.75)

### The invariant that existed before

The allocator ran **one** bipartite matching over every slot in the program.
A matching assigns each left vertex at most one right vertex, so the invariant
came free:

> one student course -> at most ONE requirement slot, anywhere in the program

Correct while the major was the only modeled system, and it is what prevents a
single course from satisfying two major requirements.

### Why it became insufficient

Rutgers SAS states that **"a course used to meet core goals may also be used
to fulfill a major or minor requirement."** So the invariant is too strict in
one direction and exactly right in the other:

| Case | Allowed? |
|---|---|
| One course -> major requirement + core requirement | **yes** (SAS says so) |
| One course -> two major requirements | no |
| One course -> two core goals | no |

### The model

Two columns, no new table:

```
requirement.requirement_system      "major" | "core" | ...   (open set)
program_version.sharing_policy      exclusive | share_across_systems  (closed set)
```

`requirement_system` is deliberately **unconstrained** at the database level:
Rutgers has more systems than we have modeled (school requirements, general
education, college requirements), and adding one must not need a migration.

`sharing_policy` **is** constrained by a CHECK, because it is a closed set of
two and an unknown value would make the allocator fall back to some default
silently.

### Why not the pairwise table from §5

The sketch keyed policy on *requirement pairs*. The real rule is not pairwise -
it is about which body of requirements a node belongs to. Encoding "core may
share with major" pairwise means a row for every (core requirement, major
requirement) combination, all saying the same thing, all needing maintenance.

Systems express the same policy in two columns.

### Why only two policy values

A third - sharing *within* a single tree - was considered and left out. No
observed Rutgers rule needs it, and an unused policy value is an invitation to
apply it wrongly. It can be added when a real requirement demands it.

### How allocation changed

Matching now runs **once per system** rather than once per program:

* `EXCLUSIVE` - every slot goes into one partition, so the result is
  byte-for-byte the previous single matching.
* `SHARE_ACROSS_SYSTEMS` - slots are partitioned by system and each partition
  is matched independently.

Because each partition is still a matching, a course can never fill two slots
in the same system. Sharing is across systems only, by construction rather
than by a check someone could forget.

Most-constrained-first ordering and determinism are unchanged; they now apply
within each partition.

### Satisfaction, allocation, and credit are three different things

The distinction this phase exists to make:

| Concept | Question | Can a shared course inflate it? |
|---|---|---|
| Requirement satisfaction | is this requirement met? | yes, legitimately |
| Course allocation | which slot did this course fill? | one per system |
| Credit applicability | how many credits count? | **no** |

A 4-credit course satisfying a major requirement AND a core goal contributes
**4 credits, not 8**. This holds structurally, not by a guard: applicable
credits are summed per *student-course row* in `engine.py`, before allocation
runs, so no allocation decision can reach the credit total.

Worked example:

```
student completes 01:198:111 (4 credits)
  eligible for MAJOR_A (system: major)
  eligible for CORE_B  (system: core)
  program policy: share_across_systems

allocation:      MAJOR_A <- 01:198:111   (system major)
                 CORE_B  <- 01:198:111   (system core)
satisfaction:    MAJOR_A satisfied, CORE_B satisfied
credits:         completed 4, applicable 4      <- not 8
```

### Ambiguity fails safe

A curated definition that says nothing about sharing gets `EXCLUSIVE`. The
system never grants extra requirement satisfaction merely because a course is
eligible in two places - permission must be stated.

---

## 15. SAS Core through the existing requirement tree (Phase 4)

### No new tables, and no migration

Core added **zero** schema objects. `alembic check` reports "No new upgrade
operations detected" after ingestion, which is the strongest available
evidence that the Phase 3 model already expressed it.

```
Program (CS 198 BA)
  -> ProgramVersion(catalog_year='2026-2027', sharing_policy='share_across_systems')
       -> Requirement(requirement_system='major')   CS_BA tree      13 nodes
       -> Requirement(requirement_system='core')    SAS_CORE tree   13 nodes
            -> RequirementCourseOption              616 eligibility rows
```

Two roots in one tree, distinguished only by `requirement_system`.

### Why Core is NOT a separate program

Core was first modeled as its own Program/ProgramVersion, and that was wrong:
**the audit engine evaluates ONE ProgramVersion**, so core requirements stored
elsewhere would never appear in a student's audit, and sharing could never
fire. Core requirements therefore attach to the student's own program version.

The cost is duplication - every program carrying SAS Core gets its own 13
nodes. The alternative (a shared core program plus multi-program audits) is a
larger change to the engine and is not justified by one program.

### What an OPEN requirement_system bought

`requirement_system` has no CHECK constraint. Adding `'core'` therefore needed
no migration. Had it been a closed enum, Core would have cost a schema change
before a single row could load - and `minor`, `school`, `honors` would each
cost another.

`sharing_policy` stays CLOSED, because the allocator branches on it and an
unknown value would silently pick a default.

### Eligibility vs satisfaction, on real data

`RequirementCourseOption` says a course **may** count. Nothing about a student
lives there.

`01:070:111` is certified CCD, CCO and NS - three different core requirements.
It produces three eligibility rows, and the audit satisfies **exactly one** of
them, because each system's allocation is still a matching. Eligibility
creates possibilities; allocation decides.

Measured: **4 courses** in the loaded data count toward both systems.

### Credits still count once

Unchanged from Phase 3.75 and verified again on real data: a course satisfying
a major requirement and a core goal contributes its credits **once**, because
applicable credits are summed per student-course before allocation runs.

### Requirement types used, and why

| Area | Type | Reason |
|---|---|---|
| SAS_CORE, CORE_CC, CORE_AOI, CORE_HSA, CORE_CSP | `all_of` | structural groupings |
| CORE_CCD, CORE_CCO, CORE_HST, CORE_SCL | `choose_n` (1) | "1 from each category" |
| CORE_AH | `choose_n` (2) | "2 courses" |
| CORE_WC | `choose_n` (3) | "3 courses" |
| CORE_QFR | `choose_n` (2) | "2 courses" |
| CORE_NS | `credits` (6) | the source states **credits only**, no course count |

No new requirement type was needed, and no Core-specific branch was added to
the allocator or the engine.

### One engine gap Core exposed

`credits` requirements were given **zero** allocation slots - the code
promised "allocated greedily after matching" and no such pass existed, so a
credits requirement could never be satisfied. CORE_NS is the first real one.

Fixed generically: a `credits` node gets one slot per course the STUDENT holds
that is eligible for it. Data-derived, deterministic, and it never assumes a
course size the source does not state.

---

## 16. Distinct categories and credit allocation (Phase 4.1)

Two defects that only real Core data could expose. Both were fixed
generically, because both would otherwise have needed a named exception for
every curriculum that shares the shape.

### 16.1 Why a requirement needs to know WHY a course is eligible

SAS states, for Arts and Humanities:

> Students must take two degree credit-bearing courses and meet at least two
> of these goals.

That is a **conjunction of two independent conditions**: a course count AND a
goal count. Phase 4 modeled only the first, because AHo/AHp/AHq/AHr were
normalized onto one `CORE_AH` node and the certifying goal was discarded. Two
AHp courses satisfied the area.

The fix is two columns, not an AH branch:

| Column | Meaning |
|---|---|
| `Requirement.min_distinct_categories` | how many distinct categories the allocated courses must cover |
| `RequirementCourseOption.category` | the source's own certification identifier for THIS eligibility |

`category` is `''`, never NULL, and it joined the unique constraint:

```
uq_requirement_course  (requirement_id, course_id, category)
```

Empty string rather than NULL is deliberate. NULL != NULL in SQL, so a
nullable column in a unique constraint stops constraining exactly the rows
that need it most.

Three levels stay distinct, and none is derivable from another:

```
official Core goal        AHp                  what SAS publishes
certification category    category='AHp'       why this course is eligible
requirement node          CORE_AH              what the student must satisfy
```

Measured effect on the real 2026-27 load: eligibility rows went from **678 to
776**, and `CORE_AH` now holds 166 options - AHo 53, AHp 77, AHq 23, AHr 13 -
where before it held one categoryless row per course.

### 16.2 Distinctness is a matching, not a set union

`max_distinct_categories()` answers "how many distinct categories can these
courses cover **at once**", and the difference from a union is the whole
point:

| Held | Union says | Matching says | Right answer |
|---|---|---|---|
| two AHp courses | 1 | 1 | 1 |
| AHp + AHo | 2 | 2 | 2 |
| **one course certified AHp AND AHq** | **2** | **1** | **1** |
| course(AHp,AHq) + course(AHp) | 2 | 2 | 2 |

Row 3 is what the Rutgers sentence settles: two courses are required, so one
course occupies one goal slot no matter how many goals certify it.

Row 4 is why it must be a matching rather than a greedy scan. In sorted order
the dual-certified course would take AHp and the AHp-only course would have
nowhere to go, answering 1. The correct answer needs the dual-certified course
to **move** to AHq - an augmenting path, the same Kuhn search used for slot
allocation.

### 16.3 A credit minimum is not a number of slots

Allocation is a bipartite matching over *slots*. A `choose_n` requirement has
a natural slot count. A `credits` requirement does not, and Phase 4 invented
one: a slot per eligible course the student held.

Measured consequence: a 6-credit `CORE_NS` with five eligible 3-credit courses
claimed **all five**, and took `01:070:111` - the only course certified CCD -
leaving CCD unsatisfiable. The requirement asked for 6 credits and was given
15, at the cost of another requirement.

Phase 4.1 removes credit requirements from the matching entirely:

```
_slots_needed(CREDITS) == 0        # deliberately zero
```

and settles them **after** it, in `(sort_order, code)` order:

1. take courses eligible for this requirement and not already claimed in its
   system,
2. highest credits first, stopping as soon as the minimum is reached,
3. tie-break on the **course string**, not the surrogate id.

Step 3 was a bug found by the round-trip: the course key is built from
`course.id`, so ordering ties by it is deterministic within one database and
arbitrary across two. Re-ingesting the catalog would silently change which
course the audit says filled the requirement. A natural key keeps the
explanation stable.

Overshooting is allowed, because Rutgers states a **minimum**: 4 + 3 = 7
satisfies 6. Nothing is rounded, no course is split, and a course whose
credits are unknown contributes zero and is never assumed to be worth 3.

Real-data verification, same student record, before and after:

```
holds 01:070:111 (CCD/CCO/NS, 4cr), 01:070:201 (NS, 3cr), 01:070:212 (NS, 3cr)

before   CORE_NS claims all three          CCD and CCO unsatisfiable
after    CORE_CCO <- 01:070:111
         CORE_NS  <- 01:070:201, 01:070:212   6.0 / 6.0 satisfied
```

### 16.4 Why the ordering is count-first and not the reverse

Count requirements are genuinely more constrained. "Exactly one CCD course"
has a small eligible set; "6 credits from any of 59 NS courses" has a large
one. Letting the narrow requirement choose first and the wide one take what
remains is the same most-constrained-first principle that put `01:198:344`
into `CS_344` rather than the elective pool in Phase 3.

### 16.5 Major and Core interact through systems, not through types

Nothing above changes sharing. Allocation is still partitioned per
`requirement_system`, a course still fills at most one slot **per system**,
and credits are still summed per student-course before allocation, so a
shared course contributes its credits once. The credit pass obeys the same
invariant: it skips any course already claimed in its own system.

### 16.6 What is still not modeled

The matching maximizes **filled slots**, not **satisfied requirements**. Where
two arrangements fill the same number of slots, the allocator is indifferent
and most-constrained-first decides.

Observed on real data: a student holding `01:013:311` (AHp and CCD) and
`01:013:203` (AHo and AHq) gets `CORE_CCD` satisfied and `CORE_AH` at 1 of 2.
Putting both courses into `CORE_AH` would satisfy AH and leave CCD
unsatisfied - the same two filled slots, one satisfied requirement either way.
Neither answer is wrong, but the audit does not currently prefer the
arrangement that completes more requirements.

Changing that means optimizing a different objective, which is a larger
decision than Phase 4.1 should make. It is recorded here rather than fixed
quietly.

---

## 17. Allocation Objective (Phase 4.2 investigation)

Investigation only. **No production behaviour changed in this phase.** The
counterexamples below are pinned by `ingestion/tests/test_allocation_objective.py`,
which asserts what the allocator does today - including in the two cases this
section concludes are wrong.

### 17.1 Why matching was chosen

A course is often eligible for several requirements, and a greedy scan in
input order can strand a requirement that had a valid assignment. That is not
hypothetical: it is the normal case in the CS major, where one course appears
both in the elective pool and against a specific required node.

Modeling allocation as maximum bipartite matching gave three properties that
still hold and are still wanted:

| Property | Why it mattered |
|---|---|
| Never under-reports for a fixable reason | if some assignment fills a slot, the matching finds one that does |
| Polynomial and fast | O(V*E) per system, microseconds at this size |
| Explainable without an LLM | every allocation traces to one edge, and edges are eligibility rows |

Most-constrained-first ordering was added on top, because maximum cardinality
alone is indifferent between equally large matchings and put `01:198:344`
into the elective pool instead of the required `CS_344`.

### 17.2 What matching guarantees, exactly

> The number of **filled slots** is the maximum achievable, per system.

That is the whole guarantee. It is a statement about a **sum**.

### 17.3 What it does NOT guarantee

Requirement satisfaction is not a sum. It is a **threshold**:

```
choose_n(2)  with 1 slot filled  ->  PARTIALLY_SATISFIED   contributes nothing
choose_n(2)  with 2 slots filled ->  SATISFIED
credits(6)   with 3 credits      ->  PARTIALLY_SATISFIED
all_of       -> satisfied only when EVERY child is satisfied
any_of(n)    -> satisfied only when n children are satisfied
```

Maximizing a sum of filled slots and maximizing how many thresholds are
crossed are different objectives. A slot filled in a requirement that will
not reach its threshold contributes to the first and nothing to the second.

Because `all_of` propagates upward, a single unsatisfied leaf keeps its whole
group unsatisfied, so the difference is amplified rather than averaged out.

### 17.4 The satisfaction model, level by level

```
leaf (course)        SATISFIED when its 1 slot is filled by a passing course
leaf (choose_n N)    SATISFIED when N slots filled AND every constraint holds
                     (max_outside_subject, min_at_level, min_distinct_categories)
leaf (credits C)     SATISFIED when allocated credits >= C
                     (settled after the matching, not by slots)
group (all_of)       SATISFIED when ALL children SATISFIED
                     INDETERMINATE if any child is INDETERMINATE
group (any_of N)     SATISFIED when >= N children SATISFIED
system (major/core)  the set of roots carrying that requirement_system;
                     each system is matched independently
audit                COMPLETE only when every root is satisfied AND no
                     authoritative rule is unevaluable
```

Two things follow. First, **allocation happens per system, evaluation happens
per tree** - so the objective question is asked once per system and never
across systems. Second, constraints such as `min_distinct_categories` are
checked at EVALUATION time, after allocation has already committed. The
allocator cannot see them.

### 17.5 Four quantities that are easy to conflate

| Quantity | Question it answers | Where it lives |
|---|---|---|
| **Eligibility** | may this course EVER count here? | `RequirementCourseOption` - a stored fact about the catalog |
| **Allocation** | which requirement does it count toward THIS time? | `AllocationPlan` - a chosen interpretation, not a fact |
| **Satisfaction** | is this requirement finished? | `RequirementResult.status` - derived from the allocation |
| **Objective** | which allocation do we choose when several are valid? | the allocator's sort order - currently implicit |

The fourth is the one this phase names. It was never written down, so
"maximize filled slots" had been operating as a decision nobody made
deliberately.

A course eligible for A and B does not satisfy both. Eligibility creates
possibilities; allocation picks one; evaluation judges the pick. Changing the
objective changes the pick - never the eligibility, and never the rules.

### 17.6 Known counterexamples

Measured, not predicted. Each is a test.

**C2 - a slot spent on a requirement that cannot be completed**

```
R_BIG  choose_n(2), only ONE eligible course held  -> unsatisfiable, always
R_ONE  choose_n(1), same single course eligible

allocation chosen today   01:198:111 -> R_BIG
filled slots              1                      (maximum: no better exists)
requirements satisfied    0
better allocation         01:198:111 -> R_ONE
                          filled slots 1, requirements satisfied 1
```

Both allocations fill one slot, so the matching is indifferent, and
most-constrained-first cannot separate them either - both requirements have
exactly one eligible course, so the tie falls to sort order. The student is
told they have completed nothing, while their transcript completes R_ONE.

**D - the matching is blind to distinct categories** — **FIXED in Phase 4.2,
see section 18.** Retained here as the record of the defect.

```
R_AH   choose_n(2) AND min_distinct_categories(2)
       01:013:120 -> Xp     01:070:102 -> Xp     01:070:201 -> Xo

allocation chosen today   01:013:120 + 01:070:102     (both Xp)
filled slots              2                            (maximum)
distinct categories       1
status                    PARTIALLY_SATISFIED
better allocation         01:013:120 + 01:070:201      (Xp + Xo)
                          filled slots 2, distinct 2, SATISFIED
```

This one mattered most, because it undermined Phase 4.1 from below: Phase 4.1
taught the requirement to DETECT a missing second category without teaching
the allocator to avoid causing one, so the audit correctly explained a failure
it had created itself. Section 18 puts the categories into the graph.

**C - equal slots, different satisfaction, survived by luck**

```
R1 choose_n(2) eligible {111, 112} | R2 choose_n(1) eligible {111}
                                   | R3 choose_n(1) eligible {112}

111->R1, 112->R1   2 slots, 1 requirement satisfied
111->R2, 112->R3   2 slots, 2 requirements satisfied   <- reached today
```

The current code reaches the better answer here - but through
most-constrained-first, not through its stated objective. Correct answer,
wrong reason, and the reason is what generalizes.

**Cases A, B, E, F, G** - documented in the test file - are cases where the
two objectives agree: single-slot ties, augmenting paths, credit-vs-count
(resolved in Phase 4.1), cross-system sharing, and unmodeled systems. Sharing
in particular **removes** the conflict, because each system is matched
separately.

### 17.7 What Rutgers actually says

SAS Academic Advising, answering a student whose course was applied to HST
when they wanted it on SCL:

> "When students take a class that can apply to more than one goal, Degree
> Navigator (DN) will apply the course to the goal that the system thinks is
> the most likely choice of the student. However, DN will adjust itself
> automatically as soon as another course is taken - **DN will always adjust
> the audit so that the maximum number of requirements are complete**."

That is a published statement of the objective, and it is **maximize
satisfied requirements** - not maximize filled slots. It is the only
statement found that addresses allocation precedence at all.

Its authority needs stating precisely. It describes the behaviour of Degree
Navigator, which this project does not treat as authoritative for
**requirement content**. But it is published by the school's advising office
and tells students what the official audit is supposed to do, which makes it
good evidence of **institutional intent about allocation**. Those are
different claims, and only the second is being relied on.

Corroborating facts, all from official SAS pages:

| Statement | Bearing |
|---|---|
| "Courses may be counted as meeting multiple learning goals" | ELIGIBILITY, not simultaneous satisfaction - see the next row |
| "even if you take a class that is on both the HST and SCL lists, you still need at least one course that meets HST and one that meets SCL for a total of TWO courses (6 credits)" | confirms one course fills one slot WITHIN Core |
| "students generally will complete the core in 10 to 14 courses" | 13 goals in 10-14 courses corroborates roughly one course per goal |
| "A course used to meet core goals may also be used to fulfill a major or minor requirement" | sharing ACROSS systems, already modeled |
| "any credits satisfying the Core Curriculum may count toward a major or minor **unless prohibited by the major or minor**" | sharing is per-program permission - `sharing_policy` is the right granularity |

No Rutgers source found states a precedence between major and core, or a rule
for choosing among equally complete allocations.

### 17.8 Why the exact objective cannot simply be swapped in

"Maximize the number of satisfied requirements" is **NP-hard**, by reduction
from Set Packing:

```
given sets S_1..S_m over a universe U
build  one course per element of U
       one requirement R_i = choose_n(|S_i|), eligible exactly S_i, one system

R_i is SATISFIED  <=>  all |S_i| of its courses are allocated to it
allocation is a matching, so two satisfied requirements use disjoint sets
therefore  max satisfied requirements  =  maximum set packing
```

Maximum-cardinality matching is polynomial; this is not. So the objective
Rutgers describes is genuinely harder than the one implemented, and the
change is not a drop-in replacement.

That is an argument about the general case, not about CoursePilot's
instances, which are small: roughly 26 requirement nodes and a few dozen
courses per audit, already partitioned by system. Exhaustive or
branch-and-bound search over that is entirely tractable. The point is that
**a bigger objective needs a search strategy and a stated bound**, not that it
is impossible.

### 17.9 Candidate objective (NOT implemented)

Recorded as a target, in lexicographic order:

```
1. maximize the number of SATISFIED requirement nodes
2. maximize filled slots                       (today's objective, demoted)
3. prefer more-constrained requirements        (today's tie-break, kept)
4. maximize distinct-category coverage
5. deterministic tie-break on natural keys     (kept, non-negotiable)
```

Level 5 is not a preference. Determinism and reproducibility are already
verified properties and no objective change may cost them.

Two open questions this ordering does not settle, both listed in 17.10.

### 17.10 Remaining ambiguity

1. **Partial progress versus completion.** In case C2 today's answer shows
   the student "1 of 2" on an unsatisfiable requirement. The proposed
   objective would show "0 of 2" there and complete R_ONE instead. Rutgers'
   statement favours completion; whether students find partial progress more
   useful is a product question, not a requirements question.
2. **No stated major/core precedence.** Where sharing is EXCLUSIVE and both
   systems want one course, nothing official says which wins. Today the
   systems are processed in sorted order. Any rule here would be
   CoursePilot's invention and must be labelled as such.
3. **Weighting.** Nothing says a major requirement is worth more than a core
   goal, so level 1 counts nodes equally. That is a choice, not a finding.
4. **DN's actual algorithm is unpublished.** The quoted sentence states an
   objective, not a method, and cannot be verified without authenticated
   access, which this project does not use.

---

## 18. Category-aware allocation (Phase 4.2)

Section 17 found two defects. This section implements the fix for **one** of
them - the category-blind matching - and deliberately leaves the other (the
global objective) alone.

### 18.1 What was wrong

Phase 4.1 gave a requirement two conditions and taught the EVALUATOR to check
both. The ALLOCATOR still knew nothing about categories, so for

```
R_AH   choose_n(2), min_distinct_categories(2)
       01:013:120 -> Xp     01:070:102 -> Xp     01:070:201 -> Xo
```

the matching filled two slots with the first two courses in sort order - both
Xp - and the evaluator correctly reported the requirement unsatisfied. **The
audit explained a failure it had caused itself**, while the student held a
satisfying pair.

### 18.2 The invariant that had to survive

```
one StudentCourse  ->  at most ONE allocation  WITHIN a requirement
```

A course certified Xp AND Xq is eligible twice and satisfies once. This is
not an implementation convenience; it is what Rutgers states:

| Source | Wording |
|---|---|
| SAS, Arts and Humanities | "Students must take two degree credit-bearing courses and meet at least two of these goals." |
| SAS Core FAQ | a course on both the HST and SCL lists still leaves a student needing "at least one course that meets HST and one that meets SCL for a total of TWO courses (6 credits)" |

Cross-system sharing is untouched: a course may still fill one slot in
`major` and one in `core`. This section only ever looks **inside** one
requirement.

### 18.3 Two strategies, one interface

Both implement `CategoryAllocationStrategy.select(CategoryRequest) ->
CategoryAllocation`. The request carries no ORM objects and no session, so
the two are pure functions of the same input - which is what makes comparing
them meaningful rather than circular.

**Strategy A - `CategorySlotStrategy` (category-slot matching)**

One vertex per AVAILABLE CATEGORY; courses on the left; an edge where the
course is certified under that category. Maximum bipartite matching, Kuhn's
augmenting-path search - the same algorithm as slot allocation, with
categories playing the part of slots.

A subtlety worth recording, because the obvious reading of
"min_distinct_categories = 2" is wrong: the vertices must be **the actual
categories**, not two generic "category slots". Two generic slots would both
be filled by two Xp courses and report two categories covered. A category has
to be occupiable only once, so the category IS the vertex.

Complexity **O(V*E)**, polynomial.

**Strategy B - `CategoryCoverageStrategy` (category-coverage search)**

Enumerates every course subset of the required size, scores each
lexicographically by (distinct categories covered, slots filled), then by a
canonical ordering. Coverage of a subset is itself a matching, because a
course inside the subset still cannot occupy two categories - so this
strategy does not avoid matching, it wraps a search around it.

Complexity **C(n, k)**, exponential. Bounded by
`MAX_COVERAGE_COMBINATIONS = 20_000`, beyond which it raises rather than
quietly returning a worse answer.

### 18.4 Why they are equivalent, and the proof

They agree on every tested case, and that is not a coincidence:

> The maximum number of distinct categories coverable by **any** subset of at
> most k courses equals `min(k, M)`, where M is the size of the maximum
> course-to-category matching over all candidates.
>
> A matching of size M uses M distinct courses and M distinct categories. If
> M <= k, that subset is itself a witness. If M > k, any k of the matched
> pairs give k courses covering k categories. No subset of size k can do
> better, since its own coverage is a matching of size at most k.

So Strategy B's optimum is computable in polynomial time, and Strategy A
computes exactly it. **The exponential search buys nothing**, which is the
useful finding: a polynomial equivalent exists for this constraint structure,
so no solver and no enumeration is needed in production.

Verified empirically as well as argued: `test_category_allocation.py` runs
both over ten candidate shapes and four (count, category) shapes each, plus
four real-data scenarios through the engine, and compares
`(filled_slots, distinct_categories)` under the single-use rule.

Equivalence is defined **semantically**, not as identical course choices -
several optima can exist, and picking a different one of them is not a
disagreement.

### 18.5 Production strategy

**`CategorySlotStrategy`.** Polynomial rather than exponential, no
enumeration bound to breach, and the smaller of the two to read. Strategy B
stays in the codebase as an independent oracle: a second copy of the same
algorithm would be no evidence, whereas a genuinely different method that
agrees is.

The seam is internal (`DegreeAuditEngine(session, category_strategy=...)`)
and is not exposed as a setting. Production always uses `DEFAULT_STRATEGY`.

### 18.6 Where the pass runs, and why it is local

```
matching (all requirements, per system)
   -> category pass    <- HERE, only for category-constrained requirements
   -> credit pass
   -> evaluation
```

For each requirement declaring `min_distinct_categories`, the candidate pool
is deliberately narrow:

```
courses the requirement ALREADY holds
  +  courses eligible for it that NO requirement in the same system has claimed
```

Nothing is taken from another requirement. The pass can therefore only
improve one node's category coverage and can never unsatisfy a neighbour.

That locality has a measured cost. Real data, `01:013:311` (AHp and CCD) plus
`01:013:203` (AHo and AHq):

```
CORE_CCD <- 01:013:311      (its only option; most-constrained-first)
CORE_AH  <- 01:013:203      1 of 2 courses, 1 of 2 goals
```

`CORE_AH` could be satisfied by taking `01:013:311` back from `CORE_CCD`, but
that would unsatisfy `CORE_CCD` - one requirement either way. Choosing
between those is the **global objective** question from section 17, which
remains unimplemented. The category pass correctly declines to make that
trade.

### 18.7 The trigger is data, never a code

```python
def _is_category_constrained(req) -> bool:
    return bool(req.min_distinct_categories)
```

No requirement code appears anywhere in this path. `CORE_AH` gets the
behaviour because of what its row says, and so will any future Rutgers
requirement shaped the same way. Requirements without the column take the
ordinary path unchanged - pinned by a test.

### 18.8 The reported category is the selected edge

`AllocationPlan.slot_category` records which category each slot was filled
under, and `_eval_choose_n` reports `len(set(selected))` rather than
re-deriving a best case from everything the courses could have counted as. A
course certified Xp and Xq appears once, under one of them, and the audit can
say which.

### 18.9 Problem size, measured

Real 2026-27 CS BA program version:

| Quantity | Value |
|---|---|
| Requirement nodes | 26 |
| Category-constrained requirements | **1** (`CORE_AH`, 2 courses / 2 goals) |
| Category edges on CORE_AH | 166 |
| Distinct categories | 4 (AHo, AHp, AHq, AHr) |
| Distinct courses certified | 135 |
| Total eligibility rows | 776 |
| Candidate pool per audit | the student's eligible courses - single digits |
| Measured audit time | 8-15 ms end to end |

The matching runs over the student's pool, not the catalog, so the practical
input is tiny. No optimization library is warranted, and none was added.

### 18.10 Credits combined with categories

`min_distinct_categories` sits on `Requirement` with no CHECK tying it to a
`requirement_type`, so a `credits` requirement **can** store one. Inspected
rather than assumed, here is what happens today:

- `_slots_needed(CREDITS)` is 0 and `min_count` is None, so the category pass
  computes an empty selection and changes nothing;
- `_eval_credits` never looks at categories, so the minimum is **not
  enforced**.

No Rutgers requirement found so far has this shape - SAS states Natural
Sciences purely in credits. So nothing was invented. The combination is
pinned by a test as UNMODELLED, which is a better state than undiscovered.

### 18.11 What this did NOT change

Byte-for-byte unchanged for everything without `min_distinct_categories`:
ordinary bipartite matching, most-constrained-first ordering, the credit
pass, Major/Core sharing, EXCLUSIVE, SHARE_ACROSS_SYSTEMS, determinism and
reproducible tie-breaking. No migration - `alembic check` reports "No new
upgrade operations detected", and the Phase 4.1 schema was already
sufficient.

Still not implemented, and still recorded in section 17: the global
"maximize satisfied requirements" objective, and any major-vs-core
precedence.
