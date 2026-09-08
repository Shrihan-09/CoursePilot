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
| 1 | Frontend (Next.js, React, TypeScript, Tailwind, shadcn/ui, React Query) | partially covered in Lesson 1 |
| 2 | Backend (Python, FastAPI, Pydantic, services, DI) | partially covered in Lesson 1 |
| 3 | Database (PostgreSQL, keys, indexes, normalization, SQLAlchemy, migrations) | *not yet encountered* |
| 4 | Rutgers data ingestion (HTTP, HTML, scraping, parsing, provenance) | *not yet encountered* |
| 5 | Degree requirement engine (graphs, prerequisites, traversal) | *not yet encountered* |
| 6 | Search (BM25, embeddings, pgvector, hybrid, reranking) | *not yet encountered* |
| 7 | RAG pipeline | *not yet encountered* |
| 8 | Skills | *not yet encountered* |
| 9 | Agent (LLM, tools, agent loop, structured outputs) | *not yet encountered* |
| 10 | Deterministic validator | concept introduced in Lesson 1 |
| 11 | Schedule optimization | *not yet encountered* |
| 12 | Calendar | *not yet encountered* |
| 13 | Registration prediction | *not yet encountered* |
| 14 | Testing | partially covered in Lesson 1 |
| 15 | DevOps (Git, Docker, env vars, deployment) | partially covered in Lesson 1 |
| — | [Engineering Lessons](#engineering-lessons) (bugs) | empty so far |

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

# Engineering Lessons

*Bugs we hit, why they happened, and how to avoid them. Empty so far — we
haven't broken anything yet.*
