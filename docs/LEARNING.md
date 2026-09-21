# CoursePilot Learning Guide

Your engineering textbook for this project. It grows as CoursePilot grows.

**How this document works**

- A new **Lesson** is added when we hit a real milestone, not after every edit.
- Lessons cover only what we have actually built. Sections below marked
  *(not yet encountered)* are placeholders — they get written when we build
  the thing.
- Existing lessons are not rewritten. New material is appended.
- Nothing here claims you understand something. It says what you *encountered*.
  Understanding is your job; this is the material.

---

## Table of contents

| # | Topic | Status |
|---|---|---|
| — | [Lesson 1: Project Architecture](#lesson-1-project-architecture) | **written** |
| — | [Lesson 2: Rutgers Data Ingestion](#lesson-2-rutgers-data-ingestion) | **written** |
| — | [Lesson 3: Sections and One-to-Many Data](#lesson-3-sections-and-one-to-many-data) | **written** |
| 1 | Frontend (Next.js, React, TypeScript, Tailwind, shadcn/ui, React Query) | partially covered in Lesson 1 |
| 2 | Backend (Python, FastAPI, Pydantic, services, DI) | partially covered in Lesson 1, Pydantic in Lesson 2 |
| 3 | Database (PostgreSQL, keys, indexes, normalization, SQLAlchemy, migrations) | covered in Lesson 2 |
| 4 | Rutgers data ingestion (HTTP, HTML, scraping, parsing, provenance) | Lesson 2; catalog parsing in **Lesson 5** |
| 5 | Degree requirement engine (graphs, prerequisites, traversal) | **Lesson 4**; rules in **Lesson 5**; sharing in **Lesson 6**; Core in **Lesson 7** |
| 6 | Search (BM25, embeddings, pgvector, hybrid, reranking) | *not yet encountered* |
| 7 | RAG pipeline | *not yet encountered* |
| 8 | Skills | *not yet encountered* |
| 9 | Agent (LLM, tools, agent loop, structured outputs) | *not yet encountered* |
| 10 | Deterministic validator | concept introduced in Lesson 1 |
| 11 | Schedule optimization | *not yet encountered* |
| 12 | Calendar | *not yet encountered* |
| 13 | Registration prediction | *not yet encountered* |
| 14 | Testing | Lesson 1; ingestion testing in Lesson 2 |
| 15 | DevOps (Git, Docker, env vars, deployment) | partially covered in Lesson 1 |
| — | [Engineering Lessons](#engineering-lessons) (bugs & surprises) | **6 entries** |

---

# Lesson 1: Project Architecture

## What We Built

The skeleton. No features — a **shape** that features will be poured into:

- a Next.js frontend that talks to a FastAPI backend over typed JSON
- a FastAPI backend that boots, reports health, and reports what it can't do yet
- PostgreSQL with pgvector running in Docker
- typed contracts for provenance, validation, and planner output
- an LLM provider abstraction with a fake default
- documentation for the systems we have *not* built

## Why We Built It This Way

Because of one problem that shapes everything else:

**An LLM is fluent whether or not it is correct.**

If a student asks "can I take Data Structures next semester?" and the model
says yes, the answer *sounds* identical whether the model checked something or
invented it. The student registers, or doesn't graduate on time, based on a
sentence that had no verification behind it.

So the architecture is built to make that failure structurally impossible
rather than merely unlikely.

---

## Concepts

### Separation of concerns

**General idea.** Split a system so each part has one job and one reason to
change. The classic phrasing: a module should have a single responsibility.

Consider a simple counter-example — one function that does everything:

```
def answer_question(question):
    return llm(f"Answer this about Rutgers: {question}")
```

This has no seams. You cannot test it, cannot check it, cannot fix half of it.
When it's wrong you have one lever: change the prompt and hope.

**How CoursePilot uses it.** Four layers, each with one job:

```
DATA        stores facts          (PostgreSQL)
RETRIEVAL   finds relevant facts  (search)
REASONING   proposes an answer    (LLM)
VALIDATION  decides if it's valid (plain Python)
```

**Why CoursePilot needs it.** Now there *are* levers. Bad recommendations?
That's reasoning. Missing courses in results? Retrieval. Wrong prerequisite?
Data. And crucially, validation can be tested exhaustively without any model
involved — it's just functions over inputs.

### The deterministic core

**General idea.** *Deterministic* means: same input, same output, every time.
`2 + 2` is deterministic. An LLM is not — same prompt, different answers.

Not a flaw; it's what makes models useful for open-ended work. But it means
you cannot build a *guarantee* on top of one.

**How CoursePilot uses it.** The LLM proposes. Python decides.

```
LLM says:        "Take CS 112 in Spring 2027."
                          │
                          ▼
Python checks:   Does CS 112 exist in our database?          ← lookup
                 Is it offered in Spring 2027?               ← lookup
                 Does the student have the prerequisites?    ← tree walk
                 Does it satisfy a requirement they need?    ← tree walk
                 Does it fit the schedule?                   ← interval math
                          │
                          ▼
Python decides:  valid / invalid / could-not-determine
```

Every one of those checks is ordinary code. Boring, testable, repeatable.

**Why CoursePilot needs it.** The student acts on the answer. "Probably right"
isn't good enough when the cost of wrong is a delayed graduation.

### The third state: `INDETERMINATE`

This is the most important design idea in the codebase so far, and it's easy
to miss.

**General idea.** Most validation is modeled as a boolean: valid or invalid.
But there are genuinely three states:

1. I checked it. It's fine. → `PASSED`
2. I checked it. It's wrong. → `FAILED`
3. **I could not check it.** → `INDETERMINATE`

Systems that only model two states have to squeeze the third into one of them.
Both choices are bad:

- collapse into `PASSED` → unverified things get green checkmarks
- collapse into `FAILED` → valid plans get blocked for no reason

**How CoursePilot uses it.** `CheckStatus` in
[validation.py](backend/app/domain/validation.py) has four values, including
`INDETERMINATE`. When a prerequisite couldn't be parsed from the Rutgers
catalog, the validator says "I don't know," and the UI must render that
differently from a confirmed pass.

**Why CoursePilot needs it.** Rutgers data will be incomplete, ambiguous, and
sometimes unparseable. That's not an edge case here — it's Tuesday. A system
that can't say "I don't know" will lie instead.

### Provenance

**General idea.** Provenance = where a piece of information came from. Not the
value, but its *receipt*: source, URL, when we fetched it, which version.

**How CoursePilot uses it.** Look at
[provenance.py](backend/app/domain/provenance.py). Every Rutgers fact carries a
`SourceRef`: source kind, URL, `retrieved_at`, `academic_year`, `version`,
`verification`.

Note `Sourced[T]` — a value welded to its sources, with `min_length=1`. The
type system itself refuses an unsourced Rutgers fact.

**Why CoursePilot needs it.** Three reasons, and the third is the one people
learn the hard way:

1. Students can check our work.
2. We can detect staleness — a 2024 requirement shown to a 2027 student is wrong.
3. **You cannot add it later.** Once a million rows exist without provenance,
   the information is simply gone. This is why it's in the schema on day one,
   before there's any data at all.

### Abstraction (the LLM provider)

**General idea.** An *abstraction* is an interface that hides which concrete
thing is behind it. Callers depend on the shape, not the implementation.

**How CoursePilot uses it.** [base.py](backend/app/llm/base.py) defines an
`LLMProvider` Protocol — `complete()` in, `Completion` out. Two implementations
exist: `EchoProvider` (a fake) and `AnthropicProvider` (a stub). Config picks
one.

A Python `Protocol` is *structural* typing: any class with a matching
`complete()` method satisfies it. No inheritance required.

**Why CoursePilot needs it.** Model providers change. Prices change. But the
deeper reason is testing: `echo` is the default, so the whole test suite runs
with no API key, no network, and no randomness. A test that calls a real LLM
fails intermittently, and intermittent tests get muted.

### Contracts before implementation

**General idea.** Define the shape of a thing before writing its guts. Here,
the shapes are Pydantic models and Protocols.

**How CoursePilot uses it.** `PlanResponse`, `ValidationReport`, `Retriever`,
`Validator` all exist. None of the logic behind them does.

**Why CoursePilot needs it.** The contract is where the architecture actually
lives. Once `ValidationReport` has no field an LLM can write to, no future
implementation can let the LLM declare a plan valid. **The design is enforced
by the types, not by remembering to be careful.**

---

## How CoursePilot Uses It

```
Student in a browser
        │  HTTP
        ▼
Next.js  (frontend/)          renders structured responses
        │  typed JSON
        ▼
FastAPI  (backend/app/api/)   thin — validates input, delegates
        │
        ├──► Reasoning     (services/planning)   ← the only LLM
        ├──► Validation    (services/validation) ← plain Python, the decider
        ├──► Retrieval     (services/retrieval)  ← finds candidates
        │
        ▼
PostgreSQL + pgvector          the source of truth
        ▲
        │
Ingestion (ingestion/)         Rutgers sources → normalized rows
```

Data flows up; **authority flows down**. No layer may claim something the layer
below it doesn't support.

---

## Important Code

| File | What it does | Why it matters |
|---|---|---|
| [validation.py](backend/app/domain/validation.py) | `CheckStatus`, `Finding`, `ValidationReport` | The verdict contract. `is_valid` is a computed property — nothing can *set* it. |
| [provenance.py](backend/app/domain/provenance.py) | `SourceRef`, `Sourced[T]` | Makes unsourced facts a type error. |
| [plan.py](backend/app/domain/plan.py) | `PlanResponse` | Planner output. `rationale` and `summary` are the only free-text fields, and nothing depends on them. |
| [base.py](backend/app/llm/base.py) | `LLMProvider` Protocol | Vendor independence. |
| [config.py](backend/app/core/config.py) | `Settings` | One place reads the environment. |
| [health.py](backend/app/api/v1/routes/health.py) | `/health`, `/ready` | Liveness vs. readiness — see below. |
| [meta.py](backend/app/api/v1/routes/meta.py) | capability flags | The backend states what it can't do, so the UI can't lie. |

### One detail worth studying: `is_valid`

```python
@property
def is_valid(self) -> bool:
    return not any(f.severity is Severity.BLOCKING for f in self.findings)
```

It's a **property**, not a field. There is no way to assign to it. Validity is
*derived from the evidence* — and evidence is what validators produce. If you
wanted the LLM to declare a plan valid, you'd have to change this class, and
that change would be visible in review.

That's the whole architecture, compressed into three lines.

---

## What Could Go Wrong?

- **Liveness/readiness confusion.** If `/health` touched the database, a
  30-second Postgres blip would make the orchestrator kill and restart a
  perfectly healthy app — repeatedly. That's why there are two endpoints:
  `/health` never touches the DB; `/ready` does.
- **Missing pgvector.** Plain Postgres accepts connections happily, then fails
  on the first vector query with a confusing error. `/ready` checks
  `pg_extension` explicitly to fail loudly and early instead.
- **Schema drift.** `frontend/src/types/api.ts` is hand-written today. If the
  backend changes and the frontend doesn't, TypeScript stays happy while the
  runtime data is wrong. Fix (already noted in ARCHITECTURE.md): generate
  those types from the OpenAPI schema.
- **`lru_cache` on settings in tests.** `get_settings()` is cached, so tests
  that change env vars see stale config. Hence `get_settings.cache_clear()` in
  the fixture.
- **Python version mismatch.** Local Python is 3.14; some dependencies have no
  3.14 wheels yet. Docker pins 3.12. If a local `pip install` fails, that's why.

---

## What I Should Be Able To Explain

1. Why can't the LLM be the source of truth for whether a plan is valid?
2. What's the difference between `FAILED` and `INDETERMINATE`, and why does
   collapsing them into a boolean cause harm in *both* directions?
3. Why is `is_valid` a property instead of a field?
4. Why must provenance be designed in on day one instead of added later?
5. What's the difference between `/health` and `/ready`, and what breaks if
   you merge them?
6. Why is the default LLM provider a fake that just echoes its input?
7. What is a Python `Protocol`, and how does it differ from a base class?
8. Why is ingestion a separate package instead of a folder inside the backend?

## Try It Yourself

Two exercises. Don't look for solutions below — there aren't any.

**A.** Open [validation.py](backend/app/domain/validation.py). Add a new
`CheckKind` for a rule we haven't modeled — say, a "course is not repeatable
for credit" check. Now trace: what else has to change for that check to appear
in an API response? (Hint: compare this to what would have been required under
the fixed-boolean design described in `docs/ARCHITECTURE.md` §5.2. That
difference is the point of the design.)

**B.** Start the stack and break it on purpose:

```
docker compose up -d db
cd backend && uvicorn app.main:app --reload
```

Hit `/api/v1/health` and `/api/v1/ready`. Now stop the database
(`docker compose stop db`) and hit both again. Predict each status code
*before* you look. Explain why they differ.

## Further Learning

Before the next milestone (the database), get familiar with:

- relational modeling basics — tables, primary keys, foreign keys
- what an ORM is and what problem it solves
- why schema migrations exist at all
- recursive data in relational databases (the requirement tree needs this)

---

# Lesson 2: Rutgers Data Ingestion

## What We Built

A pipeline that turns a live Rutgers source into validated, provenance-tagged
database rows:

```
Rutgers SOC API → Fetcher → Parser → Normalizer → Validator → Loader → PostgreSQL
```

Plus four database tables, a migration, and 47 tests.

## Why We Built It

CoursePilot's architecture says the database is the source of truth. That is
an empty promise until something actually puts trustworthy data in it. This
phase is that something.

---

## Concepts

### APIs vs. scraping — and why we didn't scrape

**General idea.** Getting data off the web has a hierarchy, best to worst:

1. **A structured API** — the server hands you JSON. Stable, cheap, explicit.
2. **Server-rendered HTML** — the data is in the page source. Parse it.
3. **JavaScript-rendered pages** — the HTML arrives nearly empty and JS fills
   it in. You need a real browser (Playwright) to see anything.

**How CoursePilot uses it.** `classes.rutgers.edu/soc/` is a category-3 page:
a JavaScript app. The obvious move is to reach for Playwright.

That would have been a mistake. The SPA loads its data from a JSON endpoint,
and we can call that endpoint directly:

```
https://classes.rutgers.edu/soc/api/courses.json?year=2026&term=9&campus=NB
→ 200 OK, application/json, 21 MB, 4,400 courses
```

**Why it matters.** Driving a browser to read a page that got its data from
JSON is strictly worse: slower, far more fragile, needs a browser engine, and
yields the same data. **Always look for the endpoint behind the page before
you automate the page.** This is why `ingestion/pyproject.toml` has no
Playwright and no BeautifulSoup — we never needed them.

### Measure the data before you design the schema

This is the most important lesson of the phase, and it is a habit, not a fact.

**The trap.** `courseString` looks exactly like a primary key. It is a course
code — `01:013:120` — and course codes are unique. Obviously.

**What measurement showed.** 4,400 records, **4,389 distinct** course strings.
Eleven collisions, from two different causes:

```
01:750:193  supplement='  '  campus=NB  credits=4  "PHYSICS FOR SCIENCES"
01:750:193  supplement='LB'  campus=NB  credits=0  "PHYSICS FOR SCI LAB"
            ^^^^ lecture and lab: DIFFERENT courses

16:400:513  supplement='  '  campus=NB  credits=3  "FOOD CHEMISTRY FUND"
16:400:513  supplement='  '  campus=OB  credits=3  "FOOD CHEMISTRY FUND"
            ^^^^ same course, two campuses: ONE course, TWO offerings
```

Had we shipped `courseString` as the key, the lab would have overwritten the
lecture (or vice versa) and 9 more courses would have collided — silently.
Nothing would have crashed. The data would just have been wrong.

**Why CoursePilot needs the habit.** Ten minutes of counting prevented a bug
that would have surfaced months later as "why does Physics say 0 credits?"

Also measured, with the same payoff:

| Assumption | Reality |
|---|---|
| credits is an integer | 11.1% null, 91 fractional (0.5, 1.5, ...) |
| courses have descriptions | **0 of 4,400** have one |
| title is the title | truncated to ~20 chars; real one is `expandedTitle`, present 60.8% |

### Natural keys vs. surrogate keys

**General idea.** Two ways to identify a row:

- **Surrogate key** — a meaningless ID the database invents (a UUID). Stable
  forever, because it means nothing.
- **Natural key** — real-world attributes that identify the thing
  (unit + subject + number + supplement).

**How CoursePilot uses both.** Every table has a UUID primary key *and* a
UNIQUE constraint on the natural key:

```python
id = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)   # surrogate
UniqueConstraint("offering_unit_code", "subject_code",
                 "course_number", "supplement_code")             # natural
```

**Why both.** The surrogate is what foreign keys point at, so a course can be
renamed or renumbered without rewriting every row that references it. The
natural key is what makes re-ingestion recognize "I have seen this course
before." Neither alone is enough.

### Entity granularity: course vs. offering

The NB/OB duplicate forced a real modeling decision.

```
Course  (catalog: what the course IS)
  └── has many ──> CourseOffering  (a term + campus where it RUNS)
```

`16:400:513` is **one** course that **runs in two places**. Credits and title
are properties of the course; campus and section counts are properties of the
offering. Put campus on the course and you get duplicate courses; put credits
on the offering and you duplicate the same number endlessly.

**The general skill:** when two records differ, ask *which entity does the
differing field actually belong to?* That answer is your table boundary.

### Normalization (the database kind)

**General idea.** Store each fact once. If it changes, you change it in one
place.

**Concretely.** The SOC payload repeats `subjectDescription` on every course:

```
01:013:120  "African, Middle Eastern, and South Asian Languages and Literatures"
01:013:130  "African, Middle Eastern, and South Asian Languages and Literatures"
...x70 more
```

Storing that per course would repeat a 60-character string thousands of times,
and renaming the subject would mean rewriting every course. So it moves to a
`subject` table, and courses point at it with a foreign key.

```
subject (id, code, description)
   ▲
   │ foreign key
course (id, ..., subject_id)
```

### Constraints: making bad data impossible

**General idea.** A constraint is a rule the *database* enforces. Application
code can have bugs; a constraint cannot be bypassed by one.

CoursePilot uses four kinds:

| Constraint | Here | Prevents |
|---|---|---|
| PRIMARY KEY | `id` | rows with no identity |
| FOREIGN KEY | `course.subject_id` | a course pointing at a subject that doesn't exist |
| UNIQUE | course natural key | duplicate courses |
| CHECK | `credits >= 0` | negative credits |

**Why this matters more than validation code.** We validate in Python *and*
constrain in SQL. That is not redundancy — the Python layer decides what
*should* happen and gives good error messages; the database guarantees what
*can* happen. `test_database_rejects_duplicate_natural_key` deliberately
bypasses the loader to prove the constraint holds on its own.

### Idempotency

**General idea.** An operation is *idempotent* if running it twice has the
same effect as running it once. Pressing an elevator button is idempotent.

**Why ingestion must be.** We will re-run this constantly — new terms, fixed
parsers, scheduled refreshes. If each run appended, the database would fill
with duplicates.

**How.** Look up by natural key, then insert or update:

```python
existing = session.scalar(select(Course).where(...natural key...))
if existing:  update the mutable fields
else:         insert
```

Two subtleties worth internalizing:

- **Never overwrite a real value with `None`.** If a later payload omits a
  field, that must not erase what an earlier one supplied. A partial upstream
  response would otherwise silently destroy data.
- **Never update the natural-key columns.** If those changed, it is a
  different course.

### Provenance and content hashing

**General idea.** A *hash* turns any input into a short fixed-size
fingerprint. Same bytes → same hash; any change → a completely different one.

**How CoursePilot uses it.** Every fetch computes `sha256(payload)`. On
re-ingest:

- hash unchanged → reuse the existing `data_source` row
- hash changed → create a new one

**Why.** Without it, every re-run would create a near-identical provenance
row, and "when did this course's credits change?" would become unanswerable
noise. With it, a new source row means something genuinely changed.

### The two-model Pydantic pattern

**General idea.** Pydantic validates data at runtime against a typed model.

**How CoursePilot uses it** — two models, opposite strictness:

```python
class RawSocCourse(BaseModel):
    model_config = ConfigDict(extra="allow")    # lenient
class NormalizedCourse(BaseModel):
    model_config = ConfigDict(extra="forbid")   # strict
```

**Why the asymmetry.** We do not control the SOC API — it is undocumented and
unversioned, so a rigid model would reject real data the first time Rutgers
adds a field. We *do* control `NormalizedCourse`, so there a typo like
`titel=` should be a loud error, not a silently ignored value.

The boundary between them is the normalizer. An upstream rename changes one
mapping function, not the whole codebase.

### Separating fetch from parse

The fetcher archives raw bytes to `data/raw/` **before** anything parses them.

**Why.** Parsers have bugs. When you find one six weeks later, you want to
reprocess the original payload — and by then the source has moved on, so
re-fetching is impossible. The archive also means development runs hit the
cache instead of re-downloading 21 MB from Rutgers, which is both faster and
more courteous.

### Testing ingestion pipelines

Three rules this suite follows:

1. **No test touches the network.** The fixture is 10 real courses extracted
   from the archived payload. Tests are fast, offline, and deterministic.
2. **Fixtures come from real data, not imagination.** Hand-written test data
   encodes your assumptions about the source — which is exactly what the
   tests are supposed to be checking. `scripts/make_fixture.py` selects real
   records covering every edge case found: both duplicate types, null
   credits, fractional credits, HTML-laced prerequisites.
3. **Test the failures.** Roughly half these tests assert that bad input is
   *rejected*. A validator only ever run on good data has not been tested.

---

## How CoursePilot Uses It

```
Rutgers SOC API (JSON, 21 MB, 4,400 courses)
        │
        ▼
  Fetcher      HTTP + retry; archives raw bytes; sha256
        │      ingestion/fetchers/soc.py
        ▼
  Parser       JSON -> RawSocCourse; reports bad records, never repairs them
        │      ingestion/parsers/soc.py
        ▼
  Normalizer   their vocabulary -> ours; strips HTML; picks the best title
        │      ingestion/normalizers/soc.py
        ▼
  Validator    cross-field + cross-record checks; loud, specific rejections
        │      ingestion/validators/course.py
        ▼
  Loader       look-up-then-upsert, keyed on the natural key
        │      ingestion/loaders/postgres.py
        ▼
  PostgreSQL   data_source · subject · course · course_offering
```

## Important Code

| File | Why it matters |
|---|---|
| [academic.py](backend/app/models/academic.py) | The schema. Its docstrings record *why* each choice was made, with the measurements. |
| [schemas.py](ingestion/coursepilot_ingestion/schemas.py) | The two-model pattern; where field rules live. |
| [postgres.py](ingestion/coursepilot_ingestion/loaders/postgres.py) | Idempotency. |
| [course.py](ingestion/coursepilot_ingestion/validators/course.py) | Cross-record checks; the duplicate-disagreement guard. |
| [DATA_SOURCES.md](docs/DATA_SOURCES.md) | Everything measured about the source, including what does *not* exist. |

## What Could Go Wrong?

- **Assuming an obvious key is unique.** Covered above. Count first.
- **`NULL != NULL` in SQL.** A nullable column in a UNIQUE constraint disables
  it — Postgres treats every NULL as distinct, so unlimited duplicates get
  through. This is why `supplement_code` normalizes `'  '` to `''`, never NULL.
- **Timezone-naive timestamps.** The first autogenerated migration produced
  `DateTime()` without `timezone=True`. For "when did we retrieve this?" that
  is ambiguous the moment a server's timezone changes. Fixed before it shipped.
- **Dialect leakage in migrations.** Autogenerating against SQLite emitted
  `server_default=sa.text('(CURRENT_TIMESTAMP)')` — SQLite's spelling. Replaced
  with `sa.func.now()`.
- **One bad record killing the batch.** An unguarded exception in a 4,400-item
  loop loses 4,399 good records. Each stage collects failures and continues.
- **Silently "fixing" malformed data.** A repaired record is indistinguishable
  from a real one later. Reject loudly instead.

## What I Should Be Able To Explain

1. Why did we call the JSON endpoint instead of scraping the page with Playwright?
2. Why is `courseString` not a valid primary key for Rutgers courses?
3. What is the difference between a `Course` and a `CourseOffering`, and how did the data force that split?
4. Why does every table have both a UUID primary key and a UNIQUE natural key?
5. What does "idempotent" mean, and what specifically makes our loader idempotent?
6. Why must `supplement_code` be `''` rather than `NULL`?
7. Why is `credits` `Numeric` and nullable instead of `Integer`?
8. Why does `RawSocCourse` allow extra fields while `NormalizedCourse` forbids them?
9. What does the content hash accomplish that a timestamp alone would not?
10. Why do we archive the raw payload before parsing it?

## Try It Yourself

**A.** Run the pipeline twice and predict the second run's output *before* you
run it:

```
python -m coursepilot_ingestion.cli --limit 12
python -m coursepilot_ingestion.cli --limit 12
```

How many courses are inserted the second time? Why? Which table gets a new row
and which do not?

**B.** Break idempotency deliberately. In `loaders/postgres.py`, comment out
the `if existing is not None:` branch in `_upsert_course` so it always
inserts. Run `pytest -k idempotent`. Two things should happen — the test
fails, *and* the database raises an `IntegrityError`. Explain why the second
one occurs even though you only changed Python code. That distinction is the
whole point of having constraints.

**C.** The SOC payload has a `sections` array we do not yet ingest. Look at
one in `data/raw/`. Sketch (don't build) the tables you would need for
sections and meeting times. Which existing table would they attach to? What
would the natural key for a section be — and what evidence in the data
supports your answer?

## Further Learning

Before the next phase:

- SQL `JOIN`s — you will need them to query courses with their offerings
- Recursive queries (`WITH RECURSIVE`) — the requirement tree needs them
- Database transactions and what "atomic" really guarantees
- Boolean expression parsing — for the prerequisite prose we deliberately left unparsed

---

# Lesson 3: Sections and One-to-Many Data

## What We Built

Four tables and a second pipeline, turning "what courses exist?" into "which
sections are offered, when, where, and by whom?":

```
course_offering ──1..n──> course_section ──┬──1..5──> section_meeting
                                            ├──0..2──> section_instructor
                                            └──0..n──> section_cross_listing
```

11,992 sections, 17,457 meetings, 12,067 instructor entries on PostgreSQL.

## Why We Built It

A course catalog cannot answer "can I take these together?" Only sections have
times, and only times produce conflicts. This is the data a scheduler needs.

---

## Concepts

### One-to-many, and the columns you must not create

**General idea.** When one thing owns several of another, the several go in
their own table with a foreign key pointing back.

The tempting shortcut is repeated columns:

```
meeting_day_1, meeting_time_1, meeting_day_2, meeting_time_2, ...
```

**Why it fails here, measured.** Sections have 1, 2, 3, 4, or **5** meeting
patterns. Two columns would have overflowed at three; four would have lost
data on the eight sections with five. And every section with one meeting would
carry eight NULLs.

The deeper problem is that queries become impossible. "Which sections meet on
Monday?" against repeated columns means
`WHERE day_1='M' OR day_2='M' OR day_3='M' ...` — and it silently breaks the
day someone adds `day_6`.

**How CoursePilot uses it.** `section_meeting` is its own table. The Monday
query is `WHERE meeting_day = 'M'`, and it keeps working at any cardinality.

**The signal to watch for:** if you are about to number a column, you want a
table.

### Choosing a natural key when the source gives you none

Phase 1's keys came straight from Rutgers identifiers. Child rows here have
no such luxury, and the two candidates fail differently.

**Option A — the attribute tuple.** Key a meeting on
`(section, day, start, end, mode, building, room)`. Measured: 0 duplicates, so
it *is* unique.

It is still the wrong choice. **36.8% of meetings are TBA — NULL day and NULL
time.** And in SQL, `NULL != NULL`. A UNIQUE constraint containing a NULL
column doesn't just weaken; it *silently stops applying* to those rows, so
Postgres would accept unlimited duplicate TBA meetings without complaint.

This is the same trap `supplement_code` hit in Phase 1, arriving from a
different direction. Worth internalizing: **a nullable column in a UNIQUE
constraint is usually a bug.**

**Option B — the ordinal.** Key on `(section_id, meeting_index)`: position 0,
1, 2… in the source array. Always present, always comparable.

Tradeoff, stated honestly: if Rutgers reorders a section's meetings, the rows
are rewritten rather than duplicated. Row counts stay correct — which is what
idempotency actually requires.

The instructor case settles it. **53 sections list the same instructor name
twice** (`01:447:380` lists `GLODOWSKI TROTT` twice). So `(section, name)` is
not unique in real data, and an ordinal is the only key that preserves the
source faithfully.

### Identity you cannot infer: why there is no `instructor` table

The instinct is to normalize instructors into their own table and point
sections at them.

**Don't — not with this data.** The source gives exactly one field: `name`. No
id, no email, no netid. There are 4,004 distinct name strings, and
`WANG, HAO` appears 92 times.

Merging on name asserts that every `WANG, HAO` is the same person. The data
cannot support that, and getting it wrong would attach one professor's
teaching history to another's.

**The rule:** normalize on a *stable identifier*, never on a display string. A
real instructor entity needs an identifier this source does not provide.

### Absent data is a finding, not a gap to fill

Before designing, I probed the payload for every key containing `capacity`,
`enroll`, `seat`, `wait`, `avail`, `max`, `limit`, `count`.

**Zero matches.** SOC provides no seat counts at all — only a boolean
`openStatus` (8,000 OPEN / 3,992 CLOSED).

The temptation is to add `capacity` and `enrolled` columns anyway, "for
later." Resist it. An empty column invites someone to fill it — with a guess,
a scraped number, or a default — and once a value is in the database nothing
distinguishes it from a real one.

**CoursePilot's rule:** if the source does not provide it, there is no column.
The absence is documented in `DATA_SOURCES.md` so the next person does not
re-investigate.

### Constraints must match reality, not tidiness

`CHECK (end_time > start_time)` is an obviously correct rule for meeting times.

It is also **wrong for this data.** Three real Rutgers meetings violate it:

```
07:966:123  S  1100 -> 1100   (zero length)
07:966:333  T  2330 -> 1250   (wraps past midnight)
07:966:333  F  2330 -> 1250
```

Ship that constraint and ingestion crashes on authentic records. The fix is
never to delete the data — it is to decide what the anomaly *means*:

| Response | Consequence |
|---|---|
| CHECK constraint | Real sections rejected. Data lost. |
| Silently accept | Anomaly invisible; conflict detection later produces nonsense. |
| **Accept + warn** | Data preserved, anomaly surfaced, someone can decide. |

CoursePilot warns. The three warnings appear on every run.

**The general lesson:** validate against *measured* source behavior, not
against what a well-behaved source would do. A rule that sounds obviously
right is exactly the kind that silently destroys real records.

### Dialect-specific constraints (`ddl_if`)

Some constraints only one database can express. `index_number ~ '^[0-9]+$'`
uses PostgreSQL's regex operator; SQLite cannot even parse it — and the fast
unit tests build their schema on SQLite.

Three bad options and one good one:

| Option | Problem |
|---|---|
| Delete the constraint | Weakens production to suit tests |
| Drop the SQLite tests | Loses the fast offline suite |
| Define it only in the migration | Schema defined in two places; they drift |
| **`ddl_if(dialect="postgresql")`** | One definition, emitted where supported |

```python
CheckConstraint("index_number ~ '^[0-9]+$'", name="index_number_numeric")
    .ddl_if(dialect="postgresql")
```

The honest consequence: that constraint is **not** tested by the SQLite suite,
so it gets a dedicated PostgreSQL integration test. A constraint that only
exists on one dialect must be verified on that dialect.

### Replace-vs-upsert for child rows

Parent rows upsert on their natural key. Children can go either way, and the
right answer depends on a question worth asking explicitly: **can the child
set shrink?**

A section's meeting list can. If it drops from 5 patterns to 2, upserting the
2 leaves 3 stale rows behind — rows that no longer exist in the source but
still look authoritative.

So children are **replaced**: delete all, insert the current set.

| | Upsert children | Replace children |
|---|---|---|
| Shrinking list | Leaves orphans | Correct |
| Unchanged data | No writes | Rewrites rows |
| Stored state | Can drift from source | Always matches source |

The cost is rewriting unchanged rows. The benefit is that stored state cannot
drift. For derived data with no independent identity, that is the right trade —
and `test_shrinking_meeting_list_leaves_no_orphans` pins it.

### Structural parentage

Sections are **nested inside** course objects in the payload. A section has no
field naming its own course.

```json
{ "courseString": "01:013:120",
  "sections": [ { "index": "10052", ... } ] }
```

That parentage exists only as a position in the JSON. The moment you iterate
sections out of their courses, it is gone.

So the parser carries it explicitly, in a `ParentCourseRef`. Matching is by
the course's **natural key**, never by title — titles are abbreviated,
duplicated across courses, and change between terms.

### Not silently discarding what you cannot place

11,992 sections were ingested with 0 unmatched. But when courses are loaded
with `--limit`, sections arrive for courses that were never stored.

Three possible responses, and only one is acceptable:

1. Drop them silently — data vanishes with no record. **No.**
2. Create the missing course — fabricates a Rutgers record. **Absolutely not.**
3. Count them, name them, insert nothing. **Yes.**

`SectionIngestionStats.unmatched_offering` plus `unmatched_details` does (3),
and the CLI always prints the count even when it is zero.

### Attribute granularity: which level owns a field?

Once you have a `Course → Offering → Section → Meeting` chain, every field
needs an owner. Putting one at the wrong level is a quiet, expensive bug.

The way to decide is not intuition — it is asking *at which level does this
value vary?* Measured answers for this source:

| Field | Varies at | Evidence |
|---|---|---|
| credits | **course** | No section key contains `credit`, `unit`, or `hour` |
| prerequisites | **course** | 0 sections carry `preReqNotes` |
| eligibility (`SENIORS ONLY`) | **section** | 10.8% of sections, varies within a course |
| open/closed | **section** | 8,000 open / 3,992 closed |
| campus | **all three** | see below |

Two traps this avoids:

* **Pushing a course attribute down.** A `credits` column on `section` would
  be duplicated across every section and could drift out of sync. Worse, it
  would imply sections *can* differ in credits — which this source says they
  cannot.
* **Pulling a section attribute up.** `SENIORS ONLY` on the course would
  apply a restriction to sections that don't have it.

**The campus case is the interesting one.** Campus appears at three levels,
and they are genuinely different facts:

```
course.campusCode     which campus OFFERS it     (NB / OB)
section.campusCode    always equals the course's (measured 11,992/11,992)
meeting.campusName    where this MEETING happens (BUSCH, ONLINE, ...)
```

**830 sections have meetings on more than one campus** — usually
`COLLEGE AVENUE + ONLINE` (hybrid), sometimes two physical campuses.

Had campus been stored only on the section, every one of those would look
like it never moves. Rutgers campuses are a bus ride apart, so a future
conflict checker must know that back-to-back meetings on BUSCH and COLLEGE
AVENUE may be infeasible even with no time overlap. Recording campus per
meeting is what makes that question answerable at all.

**The general skill:** for each field, ask "if I stored this one level up,
what would I be claiming?" If the claim is false for any real record, the
field belongs where it is.

---

## How CoursePilot Uses It

```
Rutgers SOC courses.json  (sections nested inside courses)
        │
        ▼
  SocSectionParser       walks courses, carries ParentCourseRef down
        │                parsers/sections.py
        ▼
  SocSectionNormalizer   their vocabulary -> ours; TBA becomes NULL
        │                normalizers/sections.py
        ▼
  SectionValidator       warns on real anomalies, rejects ambiguity
        │                validators/section.py
        ▼
  SectionLoader          resolves offering by natural key; replaces children
        │                loaders/sections.py
        ▼
  PostgreSQL             course_section · section_meeting ·
                         section_instructor · section_cross_listing
```

## Important Code

| File | Why it matters |
|---|---|
| [sections.py](backend/app/models/sections.py) | The schema, with the measurement behind each decision |
| [sections.py](ingestion/coursepilot_ingestion/loaders/sections.py) | Offering resolution, replace-vs-upsert, unmatched reporting |
| [section.py](ingestion/coursepilot_ingestion/validators/section.py) | Which rules exist, and which deliberately do not |
| [test_section_loader.py](ingestion/tests/test_section_loader.py) | Idempotency and constraint proofs |

## What Could Go Wrong?

- **Numbered columns** (`day_1`, `day_2`). If you're numbering, you want a table.
- **Nullable columns in a UNIQUE constraint.** The constraint silently stops applying.
- **Normalizing on a display name.** Merges people who share a name.
- **Creating columns for data the source lacks.** They get filled with guesses.
- **Constraints that reject real data.** Measure before you enforce.
- **Upserting shrinkable child lists.** Leaves orphans that look authoritative.
- **Losing structural parentage** when flattening nested JSON.
- **Reporting only the first member of a collision.** Every ambiguous record must be rejected — this was a real bug in the validator, caught by a test.

## What I Should Be Able To Explain

1. Why is `section_meeting` a separate table instead of numbered columns?
2. Why is the meeting natural key an ordinal rather than `(day, time, room)`?
3. What does `NULL != NULL` do to a UNIQUE constraint, and why does it matter here?
4. Why is there no `instructor` table?
5. Why is there no `capacity` column?
6. Why is there no `CHECK (end_time > start_time)`?
7. What does `ddl_if` solve, and what does it cost?
8. When should child rows be replaced instead of upserted?
9. Why do sections attach to `course_offering` rather than `course`?
10. What happens to a section whose course was never ingested — and what must never happen?

## Try It Yourself

**A.** Predict, then check. A section has 3 meetings; the source drops to 1.
What do `section_meeting` row counts look like after re-ingestion under
(i) replace, (ii) upsert-only? Then read
`test_shrinking_meeting_list_leaves_no_orphans`.

**B.** Break a constraint deliberately. In `sections.py`, add
`CheckConstraint("end_time_military > start_time_military")` to
`SectionMeeting`, then run the full ingestion. Which courses fail, and why is
that the *correct* behaviour of a *wrong* constraint? Remove it afterwards.

**C.** Write a SQL query listing every section meeting on Monday before noon,
with its course code, building, and instructor. You will need three JOINs.
Then ask yourself how you would have written it against `meeting_day_1..5`.

**D.** (Harder.) `section_cross_listing` stores identifiers, not foreign keys,
because 36 of 966 references point outside the payload. Sketch how you would
resolve them into real links once more campuses are ingested — and what should
happen to a reference that is still unresolvable.

## Further Learning

- SQL `JOIN` types — `INNER` vs `LEFT`, and which one finds orphans
- Interval overlap logic, for conflict detection
- `GROUP BY` / `HAVING`, the tool that found every duplicate in this phase
- Composite indexes and column order

---

# Engineering Lessons

*Bugs and surprises we hit, why they happened, and how to avoid them.*

## The key that wasn't unique

**What broke.** Nothing — this was caught before any code shipped, which is
the point of the story.

**What happened.** `courseString` (`01:013:120`) was the obvious primary key.
A probe script counting distinct values found 4,389 of 4,400.

**How we diagnosed it.** Counting, then printing the colliding rows and
diffing their fields to find what actually differed (`scripts/probe_dupes.py`).
The two causes needed opposite fixes: `supplementCode` had to be *added* to
the key, `campusCode` had to be *moved off* it entirely.

**How to prevent it.** Before designing a schema around a field, run
`SELECT COUNT(*), COUNT(DISTINCT field)`. If they differ, find out why before
you write the migration.

## Mojibake from a PowerShell round-trip

**What broke.** Em-dashes in a source file turned into `â€"`.

**Why.** A PowerShell `Get-Content -Raw` / `Set-Content` round-trip read UTF-8
bytes as the system ANSI codepage, then wrote them back as UTF-8 — the classic
double-encoding corruption.

**How we fixed it.** Rewrote the file through a UTF-8-aware tool and switched
the affected comments to ASCII punctuation.

**How to prevent it.** Don't use shell text-substitution to edit source files.
Use an editor/tool that is explicit about encoding.

## A version cap that blocked its own project

**What broke.** `pip install -e ./backend` failed with
`requires a different Python: 3.14.3 not in '<3.14,>=3.12'`.

**Why.** In the previous phase we capped Python at `<3.14` as a precaution,
because `psycopg` wheels lagged. The cap was speculative — and the application
code ran fine on 3.14. The precaution blocked real work.

**How we fixed it.** Removed the cap and moved the database drivers into an
optional `postgres` extra, so the risky dependency is isolated instead of
gating the whole package. (`psycopg` 3.3.5 turned out to install fine on 3.14.)

**How to prevent it.** Constrain the dependency that actually has the problem,
not the whole package. And re-test speculative constraints instead of
inheriting them.

## The config file that had never been used

**What broke.** The very first `alembic upgrade head` against PostgreSQL died
before touching the database:

```
pydantic_settings.exceptions.SettingsError: error parsing value for
field "cors_origins" from source "DotEnvSettingsSource"
```

**Why.** `pydantic-settings` **JSON-decodes complex types** (`list`, `dict`)
read from a `.env` file *before* field validators run. So
`CORS_ORIGINS=http://localhost:3000` was handed to `json.loads`, which failed.
The `_split_origins` validator written to handle exactly that comma-separated
form never got the chance to run.

The bug had existed since Phase 1 and was invisible, because **no `.env` file
existed until now**. Defaults came from the class, and the dotenv source was
never exercised. Creating `.env` — the documented first setup step — was what
surfaced it.

**How we fixed it.** Annotate the field so the JSON pre-decode is skipped and
the existing validator receives the raw string:

```python
cors_origins: Annotated[list[str], NoDecode] = Field(...)
```

**How to prevent it.** A config path that is never executed is not tested.
`.env.example` documented a format the code could not actually parse. Exercise
every configuration source at least once — a smoke test that loads settings
from a real dotenv file would have caught this on day one.

## Integration tests that eat your development data

**What broke.** Nearly: `pytest -m db` TRUNCATEs every table, and its database
URL fell back to `DATABASE_URL_SYNC` — the developer's working database. The
first run wiped 118 ingested courses and left test fixture rows behind, mixing
fake data into real data.

**How we diagnosed it.** Row counts stopped adding up. Joining
`data_source` against its children showed a `https://example.invalid` source
row owning 2 courses and 5 subjects — fixture residue that had no business
being in a database of Rutgers records.

**How we fixed it.** Two changes, neither of which weakens a test:

1. A dedicated `coursepilot_test` database, selected via `TEST_DATABASE_URL`.
2. A guard that **refuses to run** unless the database name ends in `_test`,
   with an explicit skip reason.

**How to prevent it.** When a test suite performs destructive operations, make
the destructive target impossible to confuse with a real one. A convention
("remember to set the env var") is not protection; a check is. Note the
difference between the two failure modes: silently truncating the wrong
database is catastrophic and quiet, while a skipped test with a loud reason is
merely annoying. Design so the loud one is what happens.

## The defensive key that turned out to be mandatory

**What happened.** Phase 2 keyed `course_section` on `(term_code, index_number)`
rather than `index_number` alone. Only Fall 2026 had been observed, where the
index was perfectly unique — 11,992 of 11,992 — so the extra column looked like
pure caution. It was documented as "costs nothing and fails safe."

Phase 2.5 measured five real terms. The caution was not caution:

```
Spring 2026 vs Fall 2026   9,829 shared indexes (83.5%)
                           9,330 of them point at a DIFFERENT course

index 10193  ->  01:070:111 sec 01   in Fall 2026
             ->  01:013:130 sec 01   in Spring 2026
```

Keying on the index alone would have collided **~83% of sections** the first
time a second term was ingested — attaching sections to the wrong courses,
with no error raised.

**The concept: scoped identifiers.** An identifier can be unique *within a
scope* while carrying no meaning *across* scopes. A registration index is
unique per term and recycled every term. Other everyday examples: a row id in
a partitioned table, a line number in a file, a jersey number on a team.

The trap is that a single sample cannot distinguish "globally unique" from
"unique within this scope" — they look identical. One term of data was always
going to show a perfectly unique index, no matter which answer was true.

**How to avoid it.** When a source hands you an identifier, ask what scope it
is unique in, and treat one sample as unable to answer that question. If the
scope is unclear, include the scope in the key: an unnecessary scope column is
a rounding error, while a missing one is silent data corruption that surfaces
only after you have a second dataset to collide with.

**What made it cheap.** The cost of being wrong was asymmetric and knowable in
advance — one extra column versus silent cross-term corruption. That asymmetry,
not confidence about Rutgers, is what justified the decision at the time. The
measurement merely confirmed it.

**A second finding from the same measurement.** The live Fall 2026 payload
changed between 2026-09-08 (4,400 courses / 11,992 sections) and 2026-09-12
(4,396 / 12,004) — *after* the term began. A fetched payload is a point-in-time
observation, never a durable fact. This is what the `content_hash` and
`retrieved_at` on `data_source` are for.

---

# Lesson 4: Degree Requirements and Allocation

## What We Built

A deterministic degree audit: real Rutgers catalog data → a curated
requirement tree → allocation → a typed audit result. No LLM anywhere in it.

## Why

Everything CoursePilot eventually wants to say — "take this next", "can you
graduate on time" — reduces to *which requirements are done*. If that answer
is wrong, every answer built on it is wrong, confidently.

---

## Concepts

### Prose is not data

The most consequential finding of this phase.

Rutgers' catalog runs on Coursedog, a platform whose schema has real
requirement primitives (`requirementType`, `courseCount`, `requirementSelect`).
We found those field names in the page payload and briefly thought the
structure was there for the taking.

It was not. Those were **schema definitions**, not populated data. The actual
CS major requirement is one 1,661-character English paragraph:

> "The basic major ... consists of: 1) six required courses in computer
> science, 01:198:111, 112, 205, 206, 211, and 344; 2) three courses in
> mathematics ... 3) five electives from a designated list ..."

**General idea.** There is a difference between data that is *structured* and
data that merely *contains* structure. Prose containing course codes is not
machine-readable requirements — the codes are extractable, the *logic* is not.

**How CoursePilot handles it.** The structure is **curated**: a human reads the
prose and encodes it, and every row stores the sentence it came from plus a
`curation_status`. The enum has `curated_from_prose`, `synthetic`, and
`unverified` — and deliberately **no `extracted`**, because that value would be
a lie the schema invites someone to tell.

**Why it matters.** A curated requirement is a *derivation*. Presenting it as
though Rutgers published it in that shape would be exactly the
unsupported-claim failure this project exists to avoid.

### Eligibility is not satisfaction

**General idea.** Two questions that look alike and are not:

- *Eligibility*: "Course X **can** satisfy Requirement Y." A property of the
  curriculum. True for every student.
- *Satisfaction*: "This student's Course X **does** satisfy Requirement Y." A
  property of one student at one moment.

**How CoursePilot uses it.** `requirement_course_option` stores only
eligibility and contains no student data at all. Satisfaction is **computed**
and never stored.

**Why.** If satisfaction were stored, a corrected requirement would need a data
migration across every student, and stale rows would silently tell students
they had finished things they had not. Computing it means a rule change
re-audits everyone correctly for free.

### Allocation: when counting is not enough

A course can be eligible for several requirements but should count for **one**.
Deciding which is a genuine combinatorial problem, not a lookup.

**The naive bug.** Assign greedily in order. Course A fits R1 and R2; course B
fits only R1. Greedy puts A in R1, B has nowhere to go, and R2 reports
unsatisfied — even though A→R2, B→R1 satisfies both.

**The fix: maximum bipartite matching.**

```
left  = the student's courses
right = requirement SLOTS   (a "choose 5" contributes 5 slots)
edge  = this course is eligible for that requirement
goal  = fill as many slots as possible
```

Kuhn's augmenting-path algorithm. When a course wants an occupied slot, it asks
the occupant to move; if the occupant can, both fit.

**Then the real bug, found against real data.** `01:198:344` is eligible for
the required `CS_344` node *and* for the 53-option elective pool. Both are one
slot, so maximum-cardinality matching is **indifferent** — and it filled an
elective slot, reporting a *required course* as unsatisfied while electives
were over-served. Same cardinality. Worse answer.

The objective was incomplete: it counted slots without caring *which*.

**Fix: most-constrained-first.** Order slots by how many courses could fill
them. A `course` requirement (exactly one possible course) outranks a pool of
53. This is the classic most-constrained-variable heuristic, and it never
shrinks the matching — it only breaks ties among equally large matchings.

**The general lesson:** when an optimiser gives a defensible-but-wrong answer,
suspect the objective before the algorithm. "Maximise filled slots" was a
faithful implementation of the wrong goal.

### Determinism is a feature, not a side effect

Maximum matchings are not unique. Without a fixed exploration order, the same
student could get different allocations on different runs.

Both sides are sorted before matching, so the result is reproducible. An audit
that changed between page loads would be untrustworthy *even if every
individual answer were correct* — a student cannot plan against an answer that
moves.

This is also what makes the engine testable: `test_allocation_is_deterministic`
runs the same audit five times and asserts identical output.

### Versioning what does not change yet

We measured the real CS requirements in catalog 2025-26 and 2026-27. They were
**byte-identical** — requirements, all 43 course descriptions, titles, credits.

It is tempting to conclude versioning was unnecessary. The opposite is true,
and the reasoning generalises:

> A property that holds in all current data but is not *guaranteed* by the
> source is exactly the property that breaks silently later.

An implementation that overwrote one year's requirements with another's would
pass every test built from today's real data, and corrupt an audit the first
time Rutgers changes a rule.

So the isolation property is proved with fixtures **explicitly labelled
synthetic**, and no synthetic requirement ever enters the development database.
Same asymmetry as the Phase 2 section key: cheap to carry, catastrophic to omit.

### Saying "I could not check that"

The audit has five requirement statuses, not two:

| Status | Meaning |
|---|---|
| `satisfied` | completed and verified |
| `provisionally_satisfied` | relies on **in-progress** work |
| `partially_satisfied` | some progress |
| `unsatisfied` | no progress |
| `indeterminate` | **we could not determine this** |

`provisionally_satisfied` exists because a student can still fail an
in-progress course — reporting it as satisfied is a promise the data does not
support. `indeterminate` exists because curated data is incomplete by nature.

Three rules the engine never breaks, all of the same shape: a planned course
satisfies nothing; an unknown requirement type is `indeterminate`, never a
pass; and a rule we have not modeled is reported as not evaluated rather than
silently ignored.

---

## Important Code

| File | Why |
|---|---|
| [allocation.py](backend/app/services/audit/allocation.py) | The matching, and why cardinality alone was wrong |
| [engine.py](backend/app/services/audit/engine.py) | Evaluation; allocation runs *before* evaluation because it is a global decision |
| [requirements.py](backend/app/models/requirements.py) | The schema, with the measurement behind each choice |
| [audit.py](backend/app/domain/audit.py) | The typed result the LLM will one day read but never write |
| [cs_ba_requirements_26_27.json](ingestion/tests/fixtures/cs_ba_requirements_26_27.json) | The curated tree, with prose and a documented gap |

## What Could Go Wrong?

- **Trusting a field name.** `requirementType` existed in the payload but was
  never populated. Check for *values*, not keys.
- **Inventing the missing part.** The catalog defers the elective list to the
  department. Encoding a guessed list would produce an audit that looks
  authoritative and is not. The gap is recorded instead.
- **Optimising the wrong objective.** See allocation above.
- **Storing computed state.** Satisfaction in the database would rot.
- **Overwriting history.** Descriptions are per catalog year for the same
  reason requirements are.

## What I Should Be Able To Explain

1. Why can't CoursePilot *extract* Rutgers requirements? What does it do instead?
2. What is the difference between eligibility and satisfaction, and why is only one stored?
3. Why does greedy allocation fail, and what does maximum matching fix?
4. Maximum matching found a valid maximum assignment that was still wrong. Why — and what fixed it?
5. Why must allocation be deterministic, beyond being correct?
6. Requirements were identical across both catalog years. Why version them anyway?
7. What does `provisionally_satisfied` mean, and why isn't it `satisfied`?
8. Why is there no `extracted` value in `CurationStatus`?

## Try It Yourself

**A.** In `allocation.py`, make `priority` ignore `option_count`. Run
`pytest -k scarce`. Explain, from the algorithm, why the total number of
allocated courses is unchanged while the audit gets worse.

**B.** Add a `credits`-type requirement ("at least 12 credits of CS electives")
to the curated fixture. It needs no new code. Why not — and what does that tell
you about data-driven rules versus hardcoded logic?

**C.** The prose says *"No more than one grade of D can be accepted in the
courses required for the major."* Sketch (don't build) where this belongs. It
is not a requirement node — why not? What would it need instead?

## Further Learning

- Bipartite matching and Hopcroft-Karp (if slot counts ever grow)
- Constraint satisfaction: most-constrained-variable, least-constraining-value
- Recursive SQL (`WITH RECURSIVE`) for requirement trees
- Why storing derived state is a common and expensive mistake

---

# Lesson 5: Rules, Exclusions, and Admitting What You Cannot Check

## What We Built

Two things: the catalog description parser became a real pipeline, and the
audit learned to evaluate degree-level *rules* — including one it must refuse
to evaluate.

## Why

Phase 3 could say "every requirement is satisfied." That is not the same as
"you can graduate," and the gap between those two sentences is where a degree
audit either earns trust or loses it.

---

## Concepts

### Requirements vs rules

**General idea.** Two different shapes of constraint:

- A **requirement** is *satisfied by* things. "Take six CS courses."
- A **rule** *constrains how* things count. "No more than one grade of D."

**How CoursePilot uses it.** The requirement tree evaluates each node from the
courses allocated to it. But "at most one D" is not satisfied by any course —
it is a property of the whole transcript. Modeling it as a requirement node
would mean inventing a node nothing can satisfy.

So rules live in their own table, attached to the `ProgramVersion`, and are
evaluated after the tree.

**The test for which you have:** ask *"what would satisfy this?"* If the answer
is "a course," it is a requirement. If the answer is "nothing — it is a
condition on other things," it is a rule.

### Exclusion is a relationship, not a property

**The tempting shortcut.** Rutgers says CS majors get no credit for CS 105,
107, 110, 142, 170, or 405. The quick fix is a flag: `course.excluded = true`,
or just delete the rows.

**Why that is wrong.** Those are real courses. Someone teaches them, students
take them, and they count fully toward *other* programs. The exclusion is not a
fact about the course — it is a fact about the relationship between that course
and *one program version*.

**How CoursePilot models it.** The exclusion lives on the `ProgramVersion` and
is applied at audit time. `01:198:405` stays a normal `Course`, stays
*eligible* for the elective requirement, and is only skipped during allocation
and credit counting for this specific degree.

**The general principle:** before adding a flag to an entity, ask *"is this
true of the thing, or true of a relationship the thing is in?"* Flags on
entities are how program-specific rules leak into global state.

### Completed credits are not degree credits

**General idea.** A student can pass a course that earns them nothing toward
the degree they are pursuing.

```
credits_completed             = 7   (everything passed)
credits_applicable_to_degree  = 4   (what counts here)
credits_excluded              = 3   (the difference, itemised)
```

**Why keep all three.** Reporting only the total would tell a student they are
closer to graduating than they are. Reporting only the applicable figure would
make credits vanish with no explanation. Keeping the difference *and naming the
courses* is what lets the audit explain itself.

`credits_remaining` is computed from the **applicable** figure. That is the
line where a shortcut becomes a false promise.

### Admitting what you cannot check

The most important idea in this phase.

Rutgers requires: *"A minimum of seven courses must be taken in the Rutgers
University-New Brunswick Department of Computer Science."*

Real, published, authoritative. And **we cannot evaluate it.** Checking it
requires knowing which courses were taken *at* Rutgers-NB versus transferred
in. `student_course` has no transfer provenance, and cross-listed courses carry
another department's code.

There were three options:

1. **Ignore the rule.** The audit silently omits a real requirement.
2. **Guess** — count CS-coded courses and call it residency. Looks right,
   quietly wrong for any transfer student.
3. **Record the rule, mark it unevaluable, say why.**

CoursePilot does (3). `is_evaluable = False` plus a `not_evaluable_reason`, and
a database CHECK enforces that an unevaluable rule *must* carry a reason —
because a rule marked unevaluable with no explanation is indistinguishable from
one someone forgot to finish.

The consequence is deliberate and uncomfortable: **a student who satisfies every
modeled requirement gets `INDETERMINATE`, not `COMPLETE`.** That is the honest
answer. Option 2 would have produced a cheerful, confident, occasionally wrong
one.

This is the same `INDETERMINATE` idea from Lesson 1, now load-bearing: a system
that cannot say "I don't know" will say something false instead.

### When a test's expectation is superseded

Adding the residency rule broke `test_complete_program_reports_complete`. It
asserted `COMPLETE`; the audit now returns `INDETERMINATE`.

**That test was not wrong when written** — it was correct for a system that
evaluated only the requirement tree. The behaviour it asserted was
deliberately superseded.

So it was rewritten, renamed to say what it now proves, and given a docstring
explaining *why* the expectation changed. That is different from deleting a
failing test or loosening an assertion to get green: the new version asserts
something **stronger** (satisfied tree + unevaluable rule → not complete).

**The distinction worth internalising:** a failing test after a deliberate
behaviour change is information, not an obstacle. Ask "is the test wrong, or is
the code wrong?" — and write down the answer where the next reader will find it.

### Parsers fail silently; counts do not prove correctness

The catalog parser was promoted from a probe script. It reported **43 courses,
zero failures** — and was wrong.

`01:198:110`'s title contains an internal `</em></strong><strong><em>` split.
The single whole-entry regex let the title group run past the end of its own
entry and **swallow `01:198:111` entirely** — a course the CS major *requires*.
No exception. No failure count. Just 43 instead of 44.

Two lessons:

1. **A clean parse is not a correct parse.** "43 parsed, 0 failed" was
   reassuring and meaningless. The bug only surfaced because a test asked for a
   *specific* course by name and it was missing.

2. **Prefer splitting to matching.** One regex per entry means a malformed
   entry can consume its neighbour. Splitting on an unambiguous boundary — here
   the course code in its own tag — means a bad chunk can only lose *itself*.

A second, smaller bug hid behind the first: `01:198:110` publishes no credits
at all, and the regex required the parentheses, so it discarded the title and
description too. Title and credits are now parsed independently.

### Nullable foreign keys as an honest signal

13 of 44 catalog courses have no `course` row. Not errors — **the catalog is a
superset of what SOC offers in any given term.** Four are offered in other
archived terms; nine appear in no term we hold.

A required FK would have made 30% of the authoritative description source
unstorable. Making `course_id` nullable, with `course_string` always present,
says exactly what is true: *"the catalog describes this course; we have no SOC
record for it yet."* The link backfills automatically if SOC later offers it.

**The general shape:** when a foreign key is optional in reality, forcing it in
the schema does not improve integrity — it just deletes the rows that do not fit.

---

## Important Code

| File | Why |
|---|---|
| [rules.py](backend/app/services/audit/rules.py) | Rule evaluation; the evaluability gate runs before any rule-specific logic |
| [requirements.py](backend/app/models/requirements.py) | `ProgramRule`, and the CHECK that unevaluable requires a reason |
| [parsers/catalog.py](ingestion/coursepilot_ingestion/parsers/catalog.py) | Split-on-marker parsing, and why |
| [loaders/catalog.py](ingestion/coursepilot_ingestion/loaders/catalog.py) | Unlinked entries; never overwriting SOC credits |

## What Could Go Wrong?

- **Flagging a course instead of the relationship.** Global state for a
  program-specific rule.
- **Collapsing completed and applicable credits.** Silently over-reports progress.
- **Guessing at an uncheckable rule.** Confident and wrong beats nothing only
  in the short term.
- **Trusting a parser's failure count.** It only counts failures it noticed.
- **Deleting rows that do not fit a required FK.**

## What I Should Be Able To Explain

1. What distinguishes a *requirement* from a *rule*? Give the test.
2. Why is course exclusion modeled on the ProgramVersion rather than the Course?
3. Why does the audit keep three credit figures instead of one?
4. Why is the residency rule `NOT_EVALUABLE` rather than just unimplemented?
5. Why must an unevaluable rule carry a reason, enforced by the database?
6. A student satisfies every modeled requirement. Why might the audit still not say COMPLETE?
7. The catalog parser reported 43 courses and 0 failures. Why was that wrong, and how was it found?
8. Why is `catalog_course_entry.course_id` nullable, and what would a required FK have cost?

## Try It Yourself

**A.** In the curated fixture, flip `CS_RESIDENCY` to `"is_evaluable": true`.
Run `pytest -k not_evaluable`. Several tests fail — before reading them, predict
*which* and *why*. Then explain what the audit now claims about a transfer
student, and why that claim is not supported.

**B.** The prose says at most one D "in the courses required for the major."
The current implementation counts Ds across **all** completed courses. Find
where, and decide whether that is too strict, too lenient, or right. What
evidence from the source would settle it?

**C.** Add a fourth rule type — a minimum GPA — to the fixture only, without
touching Python. What happens, and which layer rejects it? What does that tell
you about where the schema's guarantees actually live?

## Further Learning

- Constraint scoping: entity properties vs relationship properties
- Parser design: tokenise/split before you match
- Why derived values (satisfaction, applicable credits) should be computed, not stored
- Double-counting policies — the next real modeling problem (SAS Core explicitly permits a course to count toward both a core goal and a major)

---

# Lesson 6: Requirement Sharing, and Why Credits Are Not Satisfaction

## What We Built

The allocator learned that one course can legitimately satisfy two
requirements — without letting its credits count twice.

## Why

Phase 3.5's investigation turned up one sentence from Rutgers SAS:

> "A course used to meet core goals may also be used to fulfill a major or
> minor requirement."

That sentence invalidates an invariant the allocator had relied on since
Phase 3. This lesson is about noticing that, and generalising it by the
smallest amount that is actually justified.

---

## Concepts

### An invariant you got for free is still an invariant

The old rule was:

> one student course → at most ONE requirement slot, anywhere

Nobody wrote that rule. It came free from the algorithm: a bipartite *matching*
assigns each left vertex at most one right vertex. Choosing matching as the
method silently chose the policy.

**That is worth noticing in general.** When an algorithm gives you a property
you wanted, you have not necessarily *decided* that property — you may have
inherited it. And inherited constraints are the ones nobody remembers to
revisit when the requirements change.

Here the inherited constraint was *half* right:

| Case | Should it be allowed? |
|---|---|
| One course → major requirement + core goal | **yes** — SAS says so |
| One course → two major requirements | no |
| One course → two core goals | no |

An invariant that is right in one direction and wrong in the other cannot be
fixed by loosening it. It has to be *re-derived* at the correct granularity.

### Partition, don't special-case

The tempting fix: add a check like `if requirement.is_core: allow_reuse()`.

That hard-codes one university's policy into the allocator, and every future
system (school requirements, general education) adds another branch.

**The fix that generalises:** give every slot a `requirement_system`, and run
the matching **once per system**.

```
EXCLUSIVE             all slots in one partition  → one matching → old behaviour
SHARE_ACROSS_SYSTEMS  partition by system         → one matching each
```

Two consequences fall out for free, which is the sign the abstraction is at the
right level:

1. **Sharing across systems works** — a course can occupy one slot in `major`
   and one in `core`.
2. **Sharing within a system remains impossible** — each partition is still a
   matching, so "two major requirements from one course" cannot happen. Not
   because anything checks for it; because it is unrepresentable.

The allocator never mentions "core". It only knows that slots have systems.

### Satisfaction, allocation, and credit are three different quantities

The conceptual core of this phase. It is easy to collapse these, and the
collapse is invisible until it over-reports a student's progress:

| Concept | Question | Can sharing inflate it? |
|---|---|---|
| Requirement satisfaction | Is this requirement met? | **yes, legitimately** |
| Course allocation | Which slot did this course fill? | one per system |
| Credit applicability | How many credits count toward the degree? | **no** |

A 4-credit course satisfying a major requirement *and* a core goal means two
requirements are done and **4 credits earned — not 8**.

**How CoursePilot guarantees it.** Not with a de-duplication pass. Applicable
credits are summed per *student-course row*, before allocation runs at all:

```python
for sc, course in student_courses:      # ← per course on the record
    credits_applicable += credits
...
plan = allocate(slots, candidates)      # ← allocation happens after
```

Allocation decisions physically cannot reach the credit total. A guard could be
removed by a later refactor; an ordering like this has to be actively
dismantled.

**The general principle:** when two quantities must not interact, prefer making
the interaction *impossible* over making it *checked*.

### Open sets and closed sets need different enforcement

Two new columns, deliberately treated differently:

```
requirement_system   "major" | "core" | ...     no CHECK constraint
sharing_policy       exclusive | share_across_systems   CHECK constraint
```

`requirement_system` is **open**. Rutgers has systems we have not modeled —
school requirements, general education. Adding one must not require a
migration, and an unknown system is harmless: it simply becomes another
partition.

`sharing_policy` is **closed**. There are two values and the allocator branches
on them. An unrecognised value would make it fall back to a default *silently*,
which is exactly the failure a CHECK should prevent.

**The test:** does an unknown value cause harmless extension, or silent wrong
behaviour? Constrain the second kind.

### Ambiguity must fail safe

A curated program that says nothing about sharing gets `EXCLUSIVE`.

This matters more than it looks. The permissive default is *also* defensible —
"be generous to the student" — and it is wrong. Over-counting tells a student
they can graduate when they cannot, and they find out at the worst possible
moment. Under-counting is visible immediately and gets corrected.

**When defaults are asymmetric in cost, the default belongs on the recoverable
side.** Same reasoning as the Phase 2.5 section key: cheap to be conservative,
expensive to be wrong.

### Adding an abstraction you cannot yet justify

The obvious enum had three values: `EXCLUSIVE`, `SHARE_ACROSS_SYSTEMS`,
`SHARE_WITHIN_TREE`. Only two were built.

No observed Rutgers rule needs within-tree sharing. Adding it would mean a
value nobody can test against real data, sitting in an enum where a future
reader might reasonably assume it works — and an untested branch that *looks*
supported is worse than an absent one.

The third value can be added the day a real requirement demands it, and it will
arrive with the evidence that shows what it should mean.

---

## Important Code

| File | Why |
|---|---|
| [allocation.py](backend/app/services/audit/allocation.py) | Per-system partitioning; the old invariant and what replaced it |
| [engine.py](backend/app/services/audit/engine.py) | Credits summed per student-course, before allocation |
| [requirements.py](backend/app/models/requirements.py) | `RequirementSystem` (open) vs `SharingPolicy` (closed) |
| [test_requirement_sharing.py](ingestion/tests/test_requirement_sharing.py) | Cases A–F, including the credit-inflation regression |

## What Could Go Wrong?

- **Special-casing a system by name** in the allocator. Every new system then
  adds a branch.
- **De-duplicating credits after allocation** instead of counting before it.
  Works until someone reorders the code.
- **A permissive sharing default.** Silently over-reports progress.
- **Constraining an open set.** Every new requirement system becomes a migration.
- **Reporting an internal partition key as a real system.** I hit this: under
  `EXCLUSIVE` every slot is matched in one pseudo-partition `"*"`, and I
  initially recorded *that* as the slot's system — so every allocation claimed
  to be shared with `"*"`. The partition key is an implementation detail; the
  reported system must always be the slot's own.

## What I Should Be Able To Explain

1. Where did the old one-slot-per-course invariant actually come from? Who decided it?
2. Why can't it be fixed by simply loosening it?
3. How does per-system partitioning allow major+core while forbidding major+major?
4. Why does the allocator never mention "core" by name?
5. A course satisfies two requirements. Why do its credits count once, and what *structurally* guarantees that?
6. Why does `requirement_system` have no CHECK constraint while `sharing_policy` does?
7. Why is the default `EXCLUSIVE` rather than permissive?
8. Why were only two policy values implemented instead of three?

## Try It Yourself

**A.** In `engine.py`, move the `credits_applicable += credits` line so it runs
inside the allocation loop instead. Run `pytest -k credits_are_counted_once`.
Explain what the student is now told, and why the original ordering is a
stronger guarantee than a de-duplication check would be.

**B.** Add a `school_requirements` system to the synthetic fixture in
`test_requirement_sharing.py`, with one requirement eligible for the same
course. Predict how many allocations result under each policy *before* running
it. What does that tell you about how the allocator scales to new systems?

**C.** The CS program is `EXCLUSIVE`. Suppose Rutgers published "at most 6
credits may be shared between core and major." Which of the two new columns
could express that, and which could not? Sketch what would have to change —
and note that this is exactly the `max_shared_credits` field the original
pairwise sketch had.

## Further Learning

- Bipartite matching under side constraints (this is where C leads)
- Why "make it impossible" beats "check for it" in invariant design
- Open/closed set modeling and where enforcement belongs
- Policy-as-data versus policy-as-code

---

# Lesson 7: Modeling Curriculum Requirements from Multiple Sources

## What We Built

The SAS Core Curriculum — 13 learning goals, 616 course-to-goal eligibility
rows, 470 courses — entirely inside the requirement tree that already existed.
No new tables. No migration.

## Why

Core is the first part of CoursePilot where **no single Rutgers source can
answer the whole question**. That turns out to change how you model, not just
where you fetch from.

---

## Concepts

### Authority is per-fact, not per-source

The instinct is to rank sources: "the catalog is authoritative, SOC is
secondary." That instinct is wrong, and Core proves it.

| Fact | Who actually knows it |
|---|---|
| Which core goals exist, their wording, the area structure | SAS Office of Undergraduate Education |
| **Which courses are certified for a goal** | **Rutgers SOC `coreCodes`** |

The SAS pages never list certified courses. SOC never states how many courses
an area requires. Neither is "more authoritative" — they are authoritative for
**different facts**.

So the model records an *authority matrix*, not a source ranking. Forcing
either source to answer the other's question would mean inventing data.

**The general skill:** before choosing a source, decompose the question. "What
is the Core Curriculum?" is at least two questions with two different owners.

### Verify field semantics; names are not evidence

SOC's core entries carry `year`, `term`, and `effective`. `effective` in
particular *looks* like a catalog-year marker — exactly what Core versioning
needs.

Measured across 1,541 entries: every single one reads `year=2026`, `term=9`,
`effective=20269` — which is precisely the term we requested. They echo the
**payload**, not the goal's validity window.

Had that been assumed, every core certification would have been stamped with a
catalog year derived from whichever term happened to be fetched.

`lastUpdated` was similar: epoch **milliseconds** spanning 2016–2024. Real
information (when certification changed), but not a catalog year either.

**Cheap habit, large payoff:** for any field you intend to rely on, print its
distinct values before writing code against it. A field whose values never vary
is describing the request, not the record.

### A structured source is not automatically the right source

`coreCodes` is structured, per-course, and free — everything a hand-maintained
mapping is not. But 19 distinct codes appear in it and only **13** are SAS Core
goals:

```
SOEHS  633   School of Engineering humanities/social science
CE     145   description literally reads "Non-Core: Community Engagement"
ITR     34   not listed as a goal by either official SAS page
GVT      7   SEBS Core
ECN      4   SEBS Core
WC       3   not listed as a goal
```

**826 of 1,541 entries are not SAS Core.** Ingesting the field wholesale would
have created six requirement categories Rutgers does not have — one of which
describes itself as "Non-Core".

Structured data still needs a filter, and the filter comes from the *other*
source.

### Conflicts get recorded, not resolved by preference

`ITR` and `WC` are certified on real courses but appear on neither official SAS
page. Three options:

1. Include them — invents two goals Rutgers does not list.
2. Drop them silently — hides a real discrepancy.
3. **Apply the authority matrix and record the conflict.**

CoursePilot does (3). The SAS pages are authoritative for *which goals exist*,
so no requirement node is created — and the 37 orphaned certifications are
counted and reported, with the conflict written into `DATA_SOURCES.md` and the
fixture itself.

The difference between (2) and (3) is whether the next person can tell that a
decision was made.

### Eligibility and satisfaction, finally on real data

Lesson 4 introduced the distinction. Core makes it concrete.

`01:070:111` is certified for **CCD, CCO and NS** — three different core
requirements. It produces three eligibility rows. It satisfies **one**.

```
eligibility   course MAY count toward a requirement   3 rows
allocation    which requirement it DOES count toward  1 slot
```

Being eligible three times is not being done three times. Each system's
allocation is still a matching, so one course fills one core slot — and the
student needs three courses to finish three goals.

If eligibility were stored as satisfaction, one well-chosen course would appear
to complete a quarter of the Core.

### Requirements as data, not code

Every Core rule is a row. There is no `if requirement.code == "CORE_NS"`
anywhere, and no Core branch in the allocator or the engine. The engine still
sees only `all_of`, `choose_n`, and `credits`.

That is what made Core cheap: the work was writing a JSON definition and a
loader, not extending the evaluator.

The check worth applying to any rules engine: **could a new curriculum ship
without touching Python?** Core almost did — the one exception is below, and it
was a pre-existing gap rather than a Core-specific need.

### Open sets and what they buy, concretely

Phase 3.75 left `requirement_system` deliberately unconstrained. Core is the
payoff: adding `'core'` needed **no migration**. `alembic check` reports "No new
upgrade operations detected" after loading 13 goals and 616 eligibility rows.

Had that column been a closed enum, Core would have cost a schema change before
a single row could load — and `minor`, `school`, `honors` would each cost
another.

Meanwhile `sharing_policy` stayed closed, because the allocator branches on it.

**The rule:** constrain what code branches on. Leave open what code only
partitions by.

### Where the model actually got it wrong

Core was first built as its own `Program` and `ProgramVersion` — which reads
sensibly and was wrong. The audit engine evaluates **one** ProgramVersion, so
core requirements stored in a separate version would never appear in a
student's audit, and sharing could never fire.

The fix: core requirements attach to the *student's* program version, tagged
`requirement_system='core'`. Two roots, one tree.

It has a real cost — every program carrying SAS Core gets its own 13 nodes —
and that cost was accepted rather than hidden, because the alternative (a
shared core program plus multi-program audits) is a much larger change.

**The lesson is the detection, not the mistake:** it surfaced by running the
real audit and seeing zero core allocations. A test against fixtures alone
would not have caught it.

### A gap that only a new curriculum could expose

`credits` requirements were given **zero** allocation slots. The code said
"allocated greedily after matching" and no such pass existed. Every major
requirement is count-based, so nothing had ever exercised it — CORE_NS ("6
credits", no course count) is the first.

Fixed generically: a `credits` node gets one slot per course the *student*
holds that is eligible for it. Data-derived, deterministic, and it never
assumes a course size the source does not state.

**Dormant code paths are not working code paths.** A comment promising
behaviour is not behaviour.

### Honest limits beat tidy ones

Two things Core exposed that are *not* fixed, and are written down instead:

1. **"At least 2 goals" on Arts and Humanities is unenforced.** AHo/AHp/AHq/AHr
   collapse onto one `CORE_AH` node, so two AHp courses would be accepted.
2. **A credits requirement can be starved.** `01:070:111` carries NS credit but
   is the *only* course certified for CCD, so most-constrained-first correctly
   claims it and NS gets nothing. The allocator maximises filled slots; a
   credits requirement is measured in credits.

Both have tests pinning the current behaviour, so neither can change unnoticed.
A limitation with a test is a known quantity; a limitation without one is a
future surprise.

---

## Important Code

| File | Why |
|---|---|
| [pipelines/core.py](ingestion/coursepilot_ingestion/pipelines/core.py) | Two inputs, two sources, one pipeline |
| [loaders/core.py](ingestion/coursepilot_ingestion/loaders/core.py) | Why Core attaches to an existing program version |
| [validators/core.py](ingestion/coursepilot_ingestion/validators/core.py) | Cross-referencing goals against eligibility in both directions |
| [sas_core_26_27.json](ingestion/tests/fixtures/sas_core_26_27.json) | The curated structure, with every exclusion justified |
| [probe_core_codes.py](scripts/probe_core_codes.py) | Field-semantics verification before any code was written |

## What Could Go Wrong?

- **Ranking sources instead of mapping facts.** Leads to asking the wrong
  source and getting a confident wrong answer.
- **Trusting a field because of its name.** `effective` is the trap here.
- **Ingesting a structured field wholesale.** 54% of `coreCodes` is not SAS Core.
- **Silently dropping records that do not fit.** Indistinguishable later from
  records that never existed.
- **Storing eligibility as satisfaction.** Inflates completion.
- **Assuming a dormant code path works.**

## What I Should Be Able To Explain

1. Why is neither SAS OUE nor SOC "the authoritative Core source"?
2. What do `year`, `term`, and `effective` actually mean in `coreCodes`, and how was that established?
3. Why are 826 of 1,541 core-code entries excluded?
4. What happens to `ITR` and `WC`, and why is that better than including or dropping them?
5. `01:070:111` is certified for three goals. How many can it satisfy? Why?
6. Why did Core require no migration — and what earlier decision made that possible?
7. Why can Core not be its own Program? What breaks?
8. Why did `credits` requirements never work before, and how is the slot count decided now?
9. Name two Core rules CoursePilot does **not** enforce, and how you would know.

## Try It Yourself

**A.** Add `ITR` to the `goals` list in the fixture with a requirement node.
Run `pytest tests/test_core_ingestion.py`. Which tests fail, and what would you
have to find in a Rutgers source to justify keeping the change?

**B.** In `normalizers/core.py`, make goal-code matching case-**sensitive**
(compare `code` instead of `match_key`). Predict which requirements break
before running the suite. What does the result say about SOC and the SAS page
disagreeing on spelling?

**C.** The AH "at least 2 goals" rule is unenforced. Sketch two model changes
that could express it — one splitting `CORE_AH` into per-goal nodes, one adding
a distinctness constraint to `choose_n`. Which composes better with the
allocator, and why?

## Further Learning

- Data fusion and provenance when sources overlap partially
- Constraint modeling: distinctness constraints over a chosen set
- Why derived state (satisfaction) should never be stored alongside source state (eligibility)
- Multi-objective matching — the credits-vs-count starvation problem above

---

# Lesson 8: Allocation Is Not the Same as Requirement Satisfaction

## What We Built

Two columns, one helper, one post-matching pass — and two rules that Rutgers
publishes and CoursePilot previously could not express:

- "meet at least two of these goals"
- "6 credits", stated with no course count

## Why

Lesson 7 ended by writing down two limitations rather than hiding them. This
lesson is what happened when they were picked up. Both turned out to be the
same mistake wearing different clothes: **the allocator was being asked to
answer a question it does not measure**.

---

## Concepts

### A sentence can be a conjunction

> "Students must take two degree credit-bearing courses and meet at least two
> of these goals."

Read quickly, that is one rule about Arts and Humanities. Read carefully, it
is two independent conditions joined by *and*:

```
count      >= 2 courses
distinct   >= 2 goals
```

The distinction only becomes visible in the case where the two disagree: one
course certified for both AHp and AHq meets the goal count and not the course
count. The modeling question — is a dual-certified course enough? — is settled
by the words "two courses", not by anything in the code.

**The general skill:** when a requirement has two numbers in it, check whether
they are two views of one condition or two conditions. Implementing a
conjunction as a single condition passes every example where the two agree.

### Normalization that deletes the reason is lossy

Phase 4 mapped AHo, AHp, AHq, AHr onto one `CORE_AH` node. That is a
reasonable normalization — there is one requirement — and it quietly deleted
the fact the second condition needs. By the time the audit ran, the data could
no longer answer "which goal certified this course?".

Three things that look like one thing:

```
official Core goal        AHp                  what SAS publishes
certification category    category='AHp'       why this course is eligible
requirement node          CORE_AH              what the student must satisfy
```

The fix did not restore four nodes. It kept one node and recorded the
certification on the eligibility row, where it belongs: `category` describes
the *relationship* between a course and a requirement, not either one alone.

**A test to apply to any normalization:** can the original be distinguished
from another input that normalizes the same way? AHp+AHp and AHp+AHo both
became "two eligible courses". That collision was the bug.

### Empty string, not NULL, inside a unique constraint

`category` defaults to `''`. The temptation is NULL — "this requirement has no
sub-categories" really is absent information.

But in SQL `NULL != NULL`, so rows with NULL do not collide with each other.
A unique constraint containing a nullable column stops constraining exactly
the rows where the column is unset — which here is every major requirement,
i.e. most of the table. The constraint would have looked present and enforced
nothing.

**The rule:** a column that participates in a unique constraint wants a total
domain. Reserve NULL for facts that are genuinely unknown, not for facts that
are legitimately empty.

### Distinctness is a matching, not a union

"How many distinct goals do these courses cover?" sounds like set union.

```
courses = {A: {AHp, AHq}, B: {AHp}}
union   = {AHp, AHq}      -> 2
```

It is 2, but not for the reason the union gives. Union answers the same 2 for
`{A: {AHp, AHq}}` alone — one course, two goals — which would let a single
course satisfy a two-goal requirement.

The right question is "how many (course -> goal) pairs can be chosen with no
course and no goal used twice", which is maximum bipartite matching: the same
algorithm as slot allocation, with goals playing the part of slots.

And it must be a *matching*, not a greedy scan. Scanning `{A: {AHp, AHq},
B: {AHp}}` in order gives A -> AHp, then B has nowhere to go: answer 1. The
correct answer needs A to **move** to AHq once B arrives. That move is an
augmenting path — the mechanism from Lesson 5, reused rather than reinvented.

**Worth noticing:** the second use of an algorithm is where you find out
whether you understood the first. Nothing about "goals" is special; both
problems are "pair these up without reusing either side".

### Measuring the wrong unit

A bipartite matching allocates **slots**. `choose_n` has an obvious slot count.
`credits` has none, and Phase 4 invented one — a slot per eligible course the
student held.

The consequence, measured before anything was designed:

```
CORE_NS: 6 credits needed, five eligible 3-credit courses held
result:  claims all five (15 credits), including 01:070:111 -
         the only course certified CCD, which is now unsatisfiable
```

The requirement asked for 6 and took 15, at another requirement's expense. No
bug in the matching: it filled as many slots as it could, and it had been told
those slots existed.

**The general failure:** a system that optimizes something will optimize the
thing you actually gave it, not the thing you meant. The defect here was in
the translation from "6 credits" to "5 slots" — everything downstream was
faithful to a bad input.

The fix is to stop translating. Credit requirements contribute **zero** slots
and are settled after the matching, highest-credit-first, stopping at the
minimum. Count requirements — narrow, few options — choose first; credit
requirements — wide, many options — take what is left. That is the same
most-constrained-first principle from Lesson 5, applied one level up.

### Deterministic and reproducible are different properties

The credit pass broke ties on the allocator's course key, which is built from
a surrogate UUID. Every run on one database gave the same answer, and a test
asserting the exact courses passed.

It was still wrong. Re-ingest the catalog, get new UUIDs, and the audit
silently reports a *different* course as the one that filled the requirement.
Same status, different explanation, no way to tell why.

Deterministic means "same input, same output". Reproducible means "the input
that matters is the input you think it is". Ordering on a surrogate key is the
first without the second.

The tie-break is now the course string — a natural key, chosen back in the
early phases precisely because it survives a reload.

### Where the fix was NOT allowed to go

Both rules were tempting to special-case:

```python
if requirement.code == "CORE_AH":   ...   # no
if requirement.code == "CORE_NS":   ...   # no
```

Those would work today and fail the next curriculum. SEBS Core, minors and
honors programs all state area rules in the same shapes. What went in instead
is two columns and one pass that read requirement *data*, with no code
anywhere naming a Rutgers requirement.

The check, again: **could the next curriculum ship without touching Python?**
For these two rules, now yes.

### Tests that pin a limitation must be allowed to change

Phase 4 had a test asserting that two goals feeding one requirement produce
exactly ONE eligibility row. It passed. Phase 4.1 makes it fail, deliberately
— the collapse it protected is the defect.

That is different from deleting a test to make an implementation pass. The
replacement asserts the new behaviour AND the property the old test really
cared about (a dual-certified course still fills only one slot), so nothing
that was guarded became unguarded.

**Before changing a failing test, ask:** is the test wrong, or is the code
wrong? A test written to pin a known limitation is evidence of a decision, and
reversing it is a decision too — which is why it gets written down here.

### Honest limits, again

The matching maximizes **filled slots**, not **satisfied requirements**. Real
data shows the difference: a student holding one course certified AHp+CCD and
one certified AHo+AHq gets CCD satisfied and AH at 1 of 2. Putting both into
AH would satisfy AH and leave CCD unsatisfied — two filled slots either way,
one satisfied requirement either way. The allocator is indifferent, and
most-constrained-first breaks the tie.

Neither answer is wrong. But "which arrangement completes more of the degree"
is a different objective, and choosing it is a bigger decision than this phase
should make. So it is written into `DATA_MODEL.md` 16.6 and left visible.

---

## Important Code

| File | Why |
|---|---|
| [allocation.py](backend/app/services/audit/allocation.py) | `max_distinct_categories` and `allocate_credits` — both deliberately small |
| [engine.py](backend/app/services/audit/engine.py) | `_slots_needed` returning zero, and the post-matching credit pass |
| [requirements.py](backend/app/models/requirements.py) | `min_distinct_categories`, and `category` inside the unique constraint |
| [loaders/core.py](ingestion/coursepilot_ingestion/loaders/core.py) | one eligibility row per certifying goal |
| [test_distinct_and_credits.py](ingestion/tests/test_distinct_and_credits.py) | Cases A–J, including the case a greedy scan gets wrong |

## What Could Go Wrong?

- **Reading a two-number rule as one condition.** Passes every example where
  the two agree.
- **Normalizing away the reason.** The requirement tree stays correct and
  becomes unevaluable.
- **A nullable column in a unique constraint.** Looks enforced; is not.
- **Answering a distinctness question with a union.** Lets one course cover
  two categories.
- **Greedy where an augmenting path is needed.** Under-counts, silently.
- **Inventing a unit.** "6 credits" is not "5 slots".
- **Ordering on a surrogate key.** Deterministic, not reproducible.

## What I Should Be Able To Explain

1. Why is "two courses and at least two goals" two conditions rather than one?
2. A course is certified AHp and AHq. How many goals does it contribute, and
   which sentence settles that?
3. Why is `category` `''` and not NULL?
4. Give the input where union and matching disagree, and say which is right.
5. Why does `{A:{AHp,AHq}, B:{AHp}}` need an augmenting path?
6. How many slots does a `credits` requirement contribute, and why is any
   other number wrong?
7. Why are count requirements settled before credit requirements?
8. What is the difference between deterministic and reproducible here?
9. Why was a passing Phase 4 test changed, and what makes that different from
   deleting an inconvenient test?
10. Name one rule CoursePilot still does not optimize for, and why it was left.

## Try It Yourself

**A.** In `allocate_credits`, change the sort key from `(-credits,
course_string, key)` to `(-credits, key)`. The suite still passes. Now
re-ingest the catalog and re-run the audit twice. What changes, and why did the
tests not catch it?

**B.** Replace `max_distinct_categories` with a set union over the allocated
courses' categories. Predict which of Cases A–E fail before running them.
Which case is the one that proves union is the wrong model?

**C.** Give `CORE_QFR` a `min_distinct_categories` of 2 in the fixture. It will
change results. Now find the Rutgers wording for QFR and decide whether the
change is justified — the source says "6 credits; 2 courses required" and
nothing about distinct goals. What does that tell you about where academic
policy is allowed to come from?

**D.** Build the tie case from 16.6: one course certified AHp+CCD, one
certified AHo+AHq. Write the allocation that satisfies AH and the one that
satisfies CCD. What objective function would prefer one, and what would it
cost elsewhere?

## Further Learning

- Maximum bipartite matching with preferences (rank-maximal, and why it is not
  the same as maximum cardinality)
- Constraint programming for allocation with several objectives at once
- Natural vs surrogate keys in ordering and pagination, not just identity
- Three-valued logic and NULL semantics in SQL constraints

---

# Lesson 9: Optimization Objectives in Academic Auditing

## What We Built

Nothing. This phase changed no production behaviour.

What it produced is a written objective, seven counterexamples, one piece of
published Rutgers evidence, and a proof that the obvious fix is harder than
it looks. That is a legitimate deliverable, and recognising when it is the
right one is the skill this lesson is about.

## Why

Every phase so far asked "is the answer right?". This one asks a question one
level up: **"right according to what?"** The allocator had been optimizing
something since Lesson 4, and nobody had ever written down what.

---

## Concepts

### An implicit objective is still an objective

The allocator maximizes filled slots. That was never a decision — it is what
maximum bipartite matching happens to do, chosen because matching solved the
stranding problem from Lesson 4.

The algorithm came first and the objective came along with it. That is the
normal way objectives get chosen, and it is why they are worth auditing: you
inherit the objective of whatever algorithm you picked, whether or not it is
the one your domain wants.

**Worth asking of any system that "optimizes":** if someone asked what it
maximizes, could you answer in one sentence — and is that sentence the thing
you actually want?

### Sums and thresholds are different objectives

This is the whole lesson in two lines:

```
matching maximizes     sum over slots of (is this slot filled?)
the domain wants       sum over requirements of (did this one reach its threshold?)
```

A requirement needing two courses gives you nothing at one course. So a
filled slot is worth something only if its requirement finishes. Maximizing
the first sum is not maximizing the second, and no amount of tuning the
matching changes that — they are different functions.

The general shape: **optimizing a proxy is only safe while the proxy is
monotone in the thing you care about.** Filled slots are not monotone in
satisfied requirements; a slot can be filled at another requirement's
expense.

And `all_of` makes it worse rather than better. One unsatisfied leaf keeps
its entire group unsatisfied, so the error propagates upward instead of
averaging out.

### Local optimum versus desired domain outcome

Case C2, measured:

```
R_BIG  needs 2 courses, student holds only 1 eligible  -> can NEVER finish
R_ONE  needs 1 course,  same course eligible

chosen:  course -> R_BIG    1 slot filled, 0 requirements satisfied
better:  course -> R_ONE    1 slot filled, 1 requirement satisfied
```

The allocator did nothing wrong by its own definition. One slot is the
maximum; there is no larger matching. It is at a **global** optimum of the
function it was given, and the answer is still bad — the student is told they
have finished nothing when their transcript finishes something.

That is the failure mode worth internalising: not a bug, not a local optimum
a better search would escape, but a correct answer to the wrong question.
Debugging cannot find these, because nothing is broken. Only writing the
objective down and testing against the domain can.

### Deterministic does not mean semantically correct

Lesson 8 separated *deterministic* from *reproducible*. This phase adds a
third thing neither of them implies.

```
deterministic   same input -> same output            YES, verified
reproducible    stable across a re-ingest            YES, verified
correct         the output the domain actually wants NOT established
```

Case D is the sharp version. The allocator deterministically and reproducibly
picks two Xp courses for a requirement demanding two *distinct* categories,
and reports it unsatisfied — while the student holds a pair that satisfies
it. Every process property holds. The answer is wrong.

Worse, it is wrong in a way that makes Phase 4.1 look like the problem.
Phase 4.1 taught the requirement to *detect* a missing category; the
allocator then walked into one. **The audit correctly explains a failure it
created itself** — the most expensive kind of wrong answer, because the
explanation is convincing.

### Constraints checked after the choice cannot influence the choice

Why does case D happen at all? Because of where the check lives:

```
allocation  ->  evaluation  ->  min_distinct_categories checked here
```

By the time anything knows categories exist, the courses are committed. The
allocator's edges carry eligibility and nothing else.

This is a recurring architectural shape, not a Rutgers quirk: a validator
placed downstream of a chooser can only complain, never steer. If a
constraint should influence a decision, it has to be visible at decision
time — as part of the model, not as a later check.

### Evidence beats elegance

"Maximize satisfied requirements" is the mathematically appealing answer, and
appeal is not evidence. The brief was explicit: do not pick an objective
because it sounds cleaner.

The evidence turned out to exist, in a student FAQ:

> "DN will always adjust the audit so that the maximum number of requirements
> are complete."

Two things about how that gets used. It is published by SAS advising, so it
is good evidence of **what the official audit is meant to do**. It describes
Degree Navigator, which this project does not treat as authoritative for
**what the requirements are**. Keeping those apart is the difference between
citing a source and over-claiming one.

**Also worth noticing where it was found.** Not in the catalog, not in the
policy pages — in a student's complaint about a course landing on the wrong
goal. Real allocation semantics surface where people hit them.

### Proving the honest answer is hard

The obvious next move is to swap the objective. Before recommending that, it
is worth knowing what it costs — and here it is provably expensive.

Reduce Set Packing to it:

```
sets S_1..S_m over universe U
  one course per element of U
  one requirement R_i = choose_n(|S_i|), eligible exactly S_i

R_i satisfied  <=>  all of S_i allocated to it
allocation is a matching  =>  satisfied requirements are pairwise disjoint
max satisfied requirements = maximum set packing        NP-hard
```

Maximum matching is polynomial. The objective Rutgers describes is not. That
does not end the discussion — CoursePilot's instances are about 26 nodes and
a few dozen courses, already split by system, which is tiny — but it changes
the recommendation from "swap the objective" to "choose a search strategy and
state its bound".

**The habit:** when you are about to recommend a better objective, check its
complexity first. Whether the answer is "easy" or "NP-hard but n is 26", you
now have a reason instead of a preference.

### Knowing when not to implement

The brief forbade changing behaviour without evidence, and forbade reaching
for an ILP or SAT solver without proof of need. Both restraints held, and
both were right: the evidence arrived late in the investigation, and the
instances turned out small enough that no solver is warranted.

An investigation that ends in "here is what is wrong, here is the evidence,
here is what it would cost, do not change it yet" is a complete piece of
work. Shipping a half-justified objective change would have been worse than
shipping nothing.

---

## Important Code

| File | Why |
|---|---|
| [test_allocation_objective.py](ingestion/tests/test_allocation_objective.py) | Cases A–G; pins today's answers including the two that are wrong |
| [allocation.py](backend/app/services/audit/allocation.py) | the matching, and the docstring that now needs section 17 beside it |
| [engine.py](backend/app/services/audit/engine.py) | where thresholds are evaluated — after allocation has committed |
| [DATA_MODEL.md](docs/DATA_MODEL.md) | section 17, the written objective and the evidence |

## What Could Go Wrong?

- **Inheriting an algorithm's objective without noticing.**
- **Optimizing a proxy that is not monotone in the goal.** Filled slots are
  not monotone in satisfied requirements.
- **Mistaking determinism for correctness.** Both case C2 and case D are
  perfectly deterministic.
- **Checking a constraint downstream of the choice it should have informed.**
- **Citing a tool's behaviour as a statement of requirements.**
- **Swapping in a "better" objective without checking its complexity.**
- **Treating a correct answer to the wrong question as a bug.** There is
  nothing to debug.

## What I Should Be Able To Explain

1. What does the allocator maximize, and who decided that?
2. Why is "maximize filled slots" not the same as "maximize satisfied
   requirements"?
3. In case C2 the matching is at a global optimum. Why is the answer still
   wrong?
4. Why does `all_of` amplify rather than dampen the difference?
5. Why can the allocator not see `min_distinct_categories`?
6. Case D produces a correct explanation of a failure. Who caused the
   failure?
7. What exactly does the Rutgers FAQ sentence authorize you to conclude — and
   what does it not?
8. Sketch the Set Packing reduction. Why does it not settle the question for
   CoursePilot?
9. Give a system that is deterministic, reproducible, and wrong.
10. Why was nothing implemented this phase?

## Try It Yourself

**A.** In case C2, swap the `sort_order` of R_BIG and R_ONE and re-run. Does
the answer improve? What does that tell you about whether sort order is a
safe place to encode an objective?

**B.** Make the allocator category-aware for case D: give a requirement with
`min_distinct_categories` one slot per required category instead of per
course, and let eligibility edges carry the category. Which of cases A–G
change? Which regression tests break, and are any of them right to break?

**C.** Write the brute-force allocator: enumerate every assignment of courses
to slots, score each by satisfied requirements, and take the best with a
deterministic tie-break. Run it against the real CS + Core audit. How many
assignments are there, how long does it take, and does it disagree with the
current allocator on real data?

**D.** Construct a case where maximizing satisfied requirements fills FEWER
slots than the matching does. Is that a problem? Answer in terms of what a
student sees.

## Further Learning

- Set packing, maximum coverage, and their approximation bounds
- Lexicographic and hierarchical optimization
- Proxy metrics and Goodhart's law in system design
- Constraint propagation: making constraints visible at decision time
- Rank-maximal and popular matchings — matchings with preferences

---

# Lesson 10: Category-Constrained Matching

## What We Built

One requirement-local pass that makes the allocator aware of categories, two
independent implementations of it, and a proof that the expensive one buys
nothing.

Lesson 9 ended by recommending exactly this and nothing more. This is what
following that recommendation looks like.

---

## The worked example

A requirement: **2 courses, at least 2 distinct goals.**

```
A -> Xp        B -> Xp        C -> Xo
```

### Ordinary matching

The graph the allocator actually builds:

```
courses            slots
  A  ------------>  R_AH slot 0
  B  ------------>  R_AH slot 1
  C  ------------>  (either)
```

Every edge is identical, because an edge means "eligible for this
requirement" and nothing else. The slots are interchangeable. Sorted order
fills them with A and B, both Xp:

```
filled slots         2  (maximum - no larger matching exists)
distinct categories  1
status               UNSATISFIED
```

Nothing is broken. The matching answered its question perfectly. It was
never asked about categories, because **categories are not in the graph**.

### Category-aware matching

Change what the right-hand vertices ARE:

```
courses            categories
  A  ------------>  Xp
  B  ------------>  Xp
  C  ------------>  Xo
```

Now a matching means something different: no course reused (left side) AND
**no category reused** (right side). A and B compete for the single Xp
vertex; one of them loses; C takes Xo:

```
A -> Xp,  C -> Xo    filled slots 2, distinct categories 2, SATISFIED
```

Same algorithm, same complexity. The fix was **what the vertices represent**,
not how they are searched.

---

## Concepts

### Put the constraint in the graph, not after it

Lesson 9 noted that a constraint checked downstream of a decision can only
complain, never steer. This is the repair: `min_distinct_categories` used to
be checked at evaluation time, after the courses were committed. Now the
categories ARE the vertices, so the constraint is enforced by the structure
of the problem rather than audited afterwards.

**The general move:** when a validator keeps rejecting a chooser's output,
the fix is usually to change what the chooser is choosing over.

### The obvious encoding is wrong

"min_distinct_categories = 2" reads like *two category slots*. It is worth
seeing why that fails:

```
slot 1, slot 2  (generic)        A -> slot 1, B -> slot 2
                                 two slots filled, "two categories covered"
                                 ...but both are Xp
```

Generic slots are interchangeable, and interchangeable is exactly what
categories are not. The vertex has to BE the category, so that Xp can be
occupied once and only once.

**Worth generalising:** when modeling "N distinct X", the thing that must be
unique is the vertex. If your vertices are counters, distinctness is not
being enforced - it is being hoped for.

### One-to-one versus many-to-many eligibility

A course may be certified under several categories:

```
A -> {Xp, Xq}
```

That is many-to-many **eligibility**. It is not many-to-many
**satisfaction**. A matching is precisely the structure that accepts the
first and enforces the second: A has two edges and may use one.

This is the same eligibility-vs-satisfaction line from Lesson 4, appearing a
third time in a new place. It keeps reappearing because it is the one
distinction the whole audit rests on.

### Multi-category edges need augmenting paths

```
A -> {Xp, Xq}        B -> {Xp}
```

Scanned greedily in order: A takes Xp, B is stranded, answer 1.

The right answer needs A to **move** to Xq when B arrives - an augmenting
path. This is the third time the same Kuhn search has been the answer
(requirement slots, distinct counting, now category allocation), and the
third time a greedy scan was the tempting wrong thing.

### Preserving a single-use resource

The rule that must never bend:

```
one StudentCourse -> at most one allocation WITHIN a requirement
```

It would be very easy to "fix" case D by letting A count as Xp and Xq at
once. Two categories covered, requirement satisfied, student delighted,
answer wrong - and wrong against explicit Rutgers text, which says a course
on both the HST and SCL lists still leaves you needing two courses.

A matching gives this invariant for free, which is a good reason to reach for
one. **An invariant enforced by the data structure cannot be forgotten by the
next person editing the code.**

### Polynomial versus exponential, and checking before assuming

Two strategies were built deliberately:

```
A  category-slot     one vertex per category, matched        O(V*E)
B  category-coverage enumerate subsets, score by coverage    C(n,k)
```

B is the obvious "just search it" approach. Before shipping either, the
question worth asking is whether the expensive one actually finds anything
the cheap one misses. Here it provably does not:

> max categories coverable by any k courses = min(k, M), where M is the
> maximum course-to-category matching.
>
> A matching of size M uses M courses and M categories. If M <= k it is
> itself the witness; if M > k, any k matched pairs give k categories. No
> k-subset beats that, since its own coverage is a matching of size <= k.

So B's optimum equals A's output, and the exponential search buys nothing.
That is the whole justification for the production choice - not taste, and
not "A looked simpler".

**The habit:** two implementations that agree are evidence only if they are
genuinely different methods. A second copy of the same algorithm proves
nothing. B was kept precisely because it works differently.

### Bound the exponential thing, or do not ship it

Strategy B raises past 20,000 combinations rather than silently returning a
worse answer. That is the same principle as `INDETERMINATE` from Lesson 5:
**never present a guess as a result.** A search that quietly degrades under
load is worse than one that refuses, because the degradation is invisible in
exactly the cases where it matters.

### Keeping a fix local, on purpose

The category pass only ever re-chooses from courses its own requirement
already holds plus courses nobody in that system has claimed. It cannot take
a course from a neighbour.

That is a real limitation, and it is chosen rather than overlooked. Real data:

```
01:013:311 (AHp, CCD)    01:013:203 (AHo, AHq)

CORE_CCD <- 311     (its only option)
CORE_AH  <- 203     1 of 2 goals
```

AH could be satisfied by taking 311 back - and that would unsatisfy CCD. One
requirement either way. Deciding between them is the global objective from
Lesson 9, which is a bigger question with its own open product decision.

**A local fix that is honest about its edges is worth more than a global one
that was not justified.** The scope was the evidence's scope.

---

## Important Code

| File | Why |
|---|---|
| [categories.py](backend/app/services/audit/categories.py) | the shared interface and both strategies |
| [engine.py](backend/app/services/audit/engine.py) | where the pass runs, and why its pool is narrow |
| [allocation.py](backend/app/services/audit/allocation.py) | `release`, `slot_category`, and the untouched ordinary matching |
| [test_category_allocation.py](ingestion/tests/test_category_allocation.py) | cases A–L, and both strategies compared on identical input |

## What Could Go Wrong?

- **Checking a constraint after the choice it should have informed.**
- **Encoding "N distinct X" as N interchangeable counters.**
- **Letting multi-category eligibility become multi-category satisfaction.**
- **Greedy where an augmenting path is needed.** Third occurrence.
- **Comparing two implementations of the same algorithm** and calling the
  agreement evidence.
- **Shipping an exponential search with no bound.**
- **Widening a fix past its evidence** because the wider version feels
  complete.

## What I Should Be Able To Explain

1. Draw the ordinary graph and the category-aware graph for A, B, C. What
   changed?
2. Why are two generic "category slots" the wrong model?
3. Why can a course certified Xp and Xq not cover both?
4. Which Rutgers sentences settle that, and why is one course on both the HST
   and SCL lists still not enough?
5. Where does `A -> {Xp, Xq}`, `B -> {Xp}` need an augmenting path?
6. State the equivalence proof between the two strategies.
7. Why keep Strategy B at all, given it is strictly slower?
8. Why does Strategy B raise instead of returning its best-so-far?
9. Why does the category pass refuse to take a course from another
   requirement, and what does that cost?
10. What triggers category-aware allocation, and why is it not a requirement
    code?

## Try It Yourself

**A.** Change `CategorySlotStrategy` to use `needed_categories` generic
vertices instead of one per real category. Which tests fail? Predict before
running.

**B.** Widen the candidate pool in `_apply_category_strategy` to include
courses held by OTHER requirements in the same system. Does the real Case H
now satisfy CORE_AH? What breaks, and which requirement pays for it?

**C.** Raise `MAX_COVERAGE_COMBINATIONS` and time Strategy B against Strategy
A as candidates grow from 5 to 20. Plot it. At what size does the difference
stop being academic?

**D.** Construct a case where the two strategies pick DIFFERENT courses but
are still semantically equivalent. Why is `signature()` the right comparison
and exact course equality the wrong one?

## Further Learning

- Matching with side constraints; degree-constrained subgraphs
- Matroid intersection, and why "max distinct coverage" is tractable here
- Rainbow matchings and colour-constrained assignment
- Hopcroft-Karp, if a requirement ever needs thousands of candidates
- Differential testing: two implementations as a correctness oracle
