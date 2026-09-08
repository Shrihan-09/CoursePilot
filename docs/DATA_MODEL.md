# CoursePilot Data Model

Status: **design, not implemented.** `backend/app/models/` is empty and there
are zero migrations. This document is the proposal to review before any of it
becomes real.

Column types are indicative. Anything marked **TODO(rutgers-source)** must be
confirmed against authoritative Rutgers documentation before implementation.

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

Whether one course may satisfy two requirements is **program policy**, not a
universal rule. Modeled explicitly:

```
requirement_sharing_policy
  program_version_id  FK
  requirement_a_id    FK
  requirement_b_id    FK
  sharing_allowed     bool
  max_shared_credits  int NULL
  provenance
```

Default when no row exists: **not allowed**. Defaulting to permissive would
over-credit students and produce audits claiming they can graduate when they
cannot. The conservative default fails safe.

**TODO(rutgers-source):** actual per-program double-counting policies.

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

## 11. Open questions before implementation

1. Official Rutgers term code format?
2. Course code segmentation (unit/subject/course)?
3. Where are degree requirements authoritatively published, per school?
4. Are prerequisites published structured, or only as prose?
5. Is there an official API, and what are its access terms?
6. Actual double-counting policies per program?
7. Inter-campus travel times for schedule feasibility?
8. Which embedding model (fixes the vector dimension)?

Items 1–7 are **TODO(rutgers-source)** and must come from Rutgers. Item 8 is
ours to decide, but should be decided before the first embedding is written.
