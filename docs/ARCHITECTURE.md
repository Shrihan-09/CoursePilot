# CoursePilot Architecture

Status: **foundation only.** Most systems described here are designed, not
built. Each section marks what exists.

---

## 1. The governing principle

> The LLM proposes. Deterministic code decides.

Every architectural choice below follows from this. A student asking "can I
graduate on time?" is making a decision with real consequences — tuition,
time, graduation date. A language model that is fluent and wrong is worse than
no answer at all, because it is trusted.

So CoursePilot is structured to make it *structurally impossible* for a model
to certify academic correctness. The model never sets a validity flag. It
proposes; `app/services/validation` returns a verdict; the API reports the
verdict.

---

## 2. Layers

```
┌─────────────────────────────────────────────────────────────┐
│  FRONTEND        Next.js / React / Tailwind / shadcn        │
│                  Renders structured responses. Never        │
│                  interprets academic rules itself.          │
└───────────────────────────┬─────────────────────────────────┘
                            │  typed JSON over HTTP
┌───────────────────────────▼─────────────────────────────────┐
│  API             FastAPI + Pydantic                         │
│                  Thin. Validates input, delegates, returns. │
├─────────────────────────────────────────────────────────────┤
│  REASONING       Router → Skills → Planner                  │
│                  The only place an LLM runs. Output is      │
│                  a *proposal*, always typed.                │
├─────────────────────────────────────────────────────────────┤
│  VALIDATION      Deterministic validators                   │
│                  Pure functions. No LLM. No network.        │
│                  Produces the verdict of record.            │
├─────────────────────────────────────────────────────────────┤
│  RETRIEVAL       BM25 + vector + hybrid + rerank            │
│                  Finds candidates. Returns pointers into    │
│                  the database, never free-floating text.    │
├─────────────────────────────────────────────────────────────┤
│  DATA            PostgreSQL + pgvector                      │
│                  Normalized, provenance-tagged, term-scoped.│
│                  The source of truth.                       │
└───────────────────────────▲─────────────────────────────────┘
                            │
┌───────────────────────────┴─────────────────────────────────┐
│  INGESTION       Separate batch process                     │
│                  Authoritative Rutgers sources → rows       │
└─────────────────────────────────────────────────────────────┘
```

Data flows **up**; authority flows **down**. A layer may not assert something
the layer below it does not support.

---

## 3. Request lifecycle (target)

```
USER REQUEST
   │
   ▼
INTENT ANALYSIS ────── typed RouteDecision (not prose)
   │                   what is being asked; what's needed; what's missing
   ▼
CONTEXT ASSEMBLY ───── load only the selected skills
   │
   ▼
RETRIEVAL ──────────── hybrid search over authoritative data
   │                   returns candidate entity ids
   ▼
PLANNING ───────────── LLM selects from candidates; emits typed proposal
   │
   ▼
DETERMINISTIC VALIDATION
   │
   ├── valid ────────► STRUCTURED OUTPUT ──► UI
   │
   └── invalid ──────► REPLAN (bounded attempts, findings fed back)
                          │
                          └──► VALIDATE AGAIN
                                  │
                                  └── still invalid after N ──►
                                      return NEEDS_REVISION with the
                                      partial plan and the blocking findings
```

The last branch matters. When replanning fails, CoursePilot says so and shows
its work. It does not degrade into a plausible guess.

---

## 4. Repository layout

```
CoursePilot/
├── frontend/     Next.js app (App Router)
├── backend/      FastAPI service — the deterministic core
├── ingestion/    Separate batch package for Rutgers data
├── skills/       Versioned knowledge bundles, loaded per request
├── docs/         Architecture + the learning guide
├── scripts/      DB init, dev helpers
├── tests/        Cross-cutting tests; unit tests live with components
└── docker-compose.yml
```

---

## 5. Key decisions and their tradeoffs

### 5.1 Deterministic validation as a separate layer, not planner logic

**Chosen:** validators are pure functions in their own package, called *after*
planning, and their verdict overrides anything the planner said.

**Alternative:** have the planner check its own work, or prompt the model to
"only suggest valid courses."

**Why:** prompting reduces error rates; it does not eliminate them, and it
gives no signal when it fails. A separate layer converts an invisible failure
mode into a visible, testable one. It also means validators can be unit-tested
exhaustively without any model in the loop.

**Cost:** more code, and a validator can be wrong too. But a wrong validator is
a bug with a reproducible test case, which a wrong model output is not.

### 5.2 Findings list instead of fixed boolean validation fields

**Chosen:** `ValidationReport` holds a list of `Finding`s, each with a kind,
status, severity, sources, and remediation.

**Alternative:** the brief's `{prerequisites_valid: true, credit_load_valid: true}`.

**Why:** booleans cannot express "we could not determine this." That third
state is not an edge case here — it is the normal state whenever Rutgers data
is missing, stale, or ambiguous, which will be often. Forcing it into `true`
green-lights unverified plans; forcing it into `false` blocks valid ones.
`CheckStatus.INDETERMINATE` keeps it honest. Adding a validator also no longer
changes the response schema.

**Cost:** the frontend must render a list generically rather than reading known
fields. Worth it.

### 5.3 Provenance on every Rutgers-derived record

**Chosen:** every fact carries a `SourceRef` — source, URL, retrieval time,
academic year, version, verification status.

**Alternative:** store the data plainly; add provenance later.

**Why:** provenance is nearly impossible to retrofit. Once a million rows
exist without it, you cannot reconstruct where they came from. It also enables
staleness detection and source-conflict resolution, both of which are certain
to be needed.

**Cost:** more columns, more joins, more ingestion work.

### 5.4 Ingestion as a separate package

**Chosen:** `ingestion/` is its own installable package with its own
dependencies.

**Alternative:** a `scripts/` folder inside the backend.

**Why:** scraping needs heavy, fragile dependencies (browser engines, HTML
parsers) that have no business in the API image. Separation also keeps
scraping failures out of the request path.

**Cost:** two Python projects; shared model code must eventually be factored
into a common package.

### 5.5 Selective skill loading

**Chosen:** the router picks which skills to load per request.

**Alternative:** put all program knowledge in one system prompt.

**Why:** cost scales with context, and irrelevant material measurably degrades
model attention. With many Rutgers programs this becomes untenable quickly.

**Cost:** routing can pick wrong. Mitigated by making the routing decision
typed, logged, and testable, and by letting the planner request an additional
skill when it detects a gap.

### 5.6 Retrieval strategy is configurable, not fixed

**Chosen:** `RETRIEVAL_STRATEGY` selects bm25 / vector / hybrid behind one
`Retriever` interface.

**Why:** course codes ("CS 112") are exact-match queries where BM25 typically
beats embeddings outright. Conceptual queries ("courses about machine
learning") favor semantic search. Which wins is an empirical question about
*our* corpus and *our* users' queries — so the architecture is built to
measure it rather than to assume it.

**Cost:** maintaining multiple retrieval paths.

### 5.7 Frontend not containerized

**Chosen:** Postgres and backend in Docker; `npm run dev` on the host.

**Why:** Next.js hot reload through a Docker bind mount on Windows is slow and
unreliable. The productivity loss is daily; the consistency gain is marginal
for a dev-only concern.

**Cost:** contributors need Node locally. Production builds still containerize.

### 5.8 `echo` as the default LLM provider

**Chosen:** ship with a deterministic fake provider bound by default.

**Why:** a fresh clone runs with no API key, CI costs nothing, and no test can
accidentally depend on live model output.

**Cost:** you must explicitly opt into a real provider. That is the intended
friction.

---

## 6. Local development topology

```
Host                          Docker
────                          ──────
npm run dev  :3000  ──────►   backend  :8000  ──────►  db  :5432
(Next.js)                     (FastAPI)                (pg16 + pgvector)
```

Postgres is `pgvector/pgvector:pg16` so the extension is present without a
custom build. `scripts/init_db.sql` enables `vector`, `pg_trgm`, and
`uuid-ossp` on first volume creation.

---

## 7. What exists right now

| Component | State |
|---|---|
| Repo structure, Docker, Postgres+pgvector | **Working** |
| FastAPI app, `/health`, `/ready`, `/meta` | **Working** |
| Settings, structured logging | **Working** |
| LLM provider abstraction + echo fake | **Working** |
| Next.js app + status page | **Working** |
| Validation / plan schemas | **Types only** |
| ORM models (`data_source`, `subject`, `course`, `course_offering`) | **Working, verified on PostgreSQL 16.15** |
| ORM models (`course_section`, `section_meeting`, `section_instructor`, `section_cross_listing`) | **Working, verified on PostgreSQL 16.15** |
| Alembic migrations `27348b5d362d` → `ef89e1066a73` | **Applied on PostgreSQL** |
| SOC course ingestion pipeline | **Working, verified end-to-end on PostgreSQL** |
| SOC section ingestion pipeline | **Working; 11,992 sections / 17,457 meetings loaded** |
| Retrieval, planner, validators (academic), skills | **Contracts only** |

### PostgreSQL verification status — COMPLETE (2026-09-09)

Verified against PostgreSQL 16.15 (`pgvector/pgvector:pg16`), not merely
SQLite:

* migration applies from scratch; `uuid` native, `numeric(4,1)`,
  `timestamp with time zone` + `now()` all render correctly
* UNIQUE, CHECK, and FK constraints confirmed enforced **by the database
  itself**, by attempting direct SQL violations
* real Rutgers SOC data ingested end-to-end; at full term scale this is
  4,391 courses / 4,400 offerings / 11,992 sections / 17,457 meetings
* re-ingestion is idempotent (identical row counts, zero inserts)
* 138 tests pass, 0 skipped

Test isolation: the `db`-marked tests TRUNCATE, so they run against a separate
`coursepilot_test` database via `TEST_DATABASE_URL`. Pointing them at the
development database destroys ingested data.

## 8. Deliberately not built yet

Prerequisite parsing (prose is stored verbatim, unparsed) · course descriptions
(SOC provides none) · section restriction lists (majors/minors/honors) ·
enrollment and seat counts (**SOC provides none**) · degree requirements · the
agent · RAG · embeddings · degree planner · schedule optimizer · registration
predictor · Google Calendar · discussions · authentication.

Authentication deserves a note: it is absent because it is not needed to
validate the architecture, but it is required before any real student data is
stored. It must land before the first real user.

## 9. Known follow-ups

- Generate `frontend/src/types/api.ts` from the OpenAPI schema instead of
  hand-writing it, so the contract cannot drift.
- Factor shared ORM models into a package both `backend/` and `ingestion/`
  depend on, rather than duplicating them.
- Add request tracing (`trace_id` already reserved in `PlanResponse`).
