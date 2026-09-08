# CoursePilot

Rutgers degree-planning and schedule-optimization platform.

> **Status: foundation only.** The architecture, contracts, and development
> environment exist. The features do not. See
> [What is not built](#what-is-not-built).

---

## The idea

A Rutgers student should be able to ask "what should I take next semester?"
and get an answer that is **actually correct** — not merely fluent.

That distinction drives the whole design:

> **The LLM proposes. Deterministic code decides.**

A language model is good at interpreting a vague question, weighing tradeoffs,
and explaining a plan. It is not a reliable authority on whether a specific
student has a specific prerequisite. So CoursePilot never lets it be one.
Every academic claim is checked by ordinary, testable Python against
authoritative Rutgers data before it reaches a student.

```
USER REQUEST → INTENT ANALYSIS → RETRIEVAL → PLANNING
             → DETERMINISTIC VALIDATION → (replan if invalid)
             → STRUCTURED OUTPUT → UI
```

---

## Documentation

Read in this order:

| Document | Contents |
|---|---|
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | System design, layers, and every major decision with its tradeoffs |
| [DATA_MODEL.md](docs/DATA_MODEL.md) | Proposed schema — provenance, requirement trees, prerequisites |
| [AGENT_ARCHITECTURE.md](docs/AGENT_ARCHITECTURE.md) | Planned agent flow and the anti-hallucination safeguards |
| [RAG_ARCHITECTURE.md](docs/RAG_ARCHITECTURE.md) | Retrieval strategies and how we will *measure* which wins |
| [DECISION_TREE.md](docs/DECISION_TREE.md) | Step-by-step flows for each kind of student request |
| [LEARNING.md](docs/LEARNING.md) | The engineering learning guide for this project |

---

## Stack

| Layer | Technology |
|---|---|
| Frontend | Next.js 15, React 19, TypeScript, Tailwind, shadcn/ui, React Query |
| Backend | Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2 (async) |
| Database | PostgreSQL 16 + pgvector |
| Retrieval | Postgres FTS (BM25) + pgvector, hybrid + reranking |
| Ingestion | Separate Python package |
| Local dev | Docker Compose |

---

## Quick start

**Prerequisites:** Docker Desktop, Node 20+, Python 3.12+, Git.

> Docker is **not currently installed** on this machine. Install Docker Desktop
> for Windows, or point `DATABASE_URL` at a local Postgres 16 that has the
> `vector` extension available.

```bash
cp .env.example .env
```

### 1. Database

```bash
docker compose up -d db
```

Uses `pgvector/pgvector:pg16`, so the extension is prebuilt.
`scripts/init_db.sql` enables `vector`, `pg_trgm`, and `uuid-ossp` on first run.

### 2. Backend

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -e ".[dev]"
uvicorn app.main:app --reload
```

- API: http://localhost:8000
- Docs: http://localhost:8000/docs

> **Python version note:** local Python here is 3.14, but several dependencies
> don't publish 3.14 wheels yet. `pyproject.toml` targets `>=3.12,<3.14` and
> the Docker image pins 3.12. If a local install fails, use 3.12 or run the
> backend in Docker (`docker compose up backend`).

### 3. Frontend

```bash
cd frontend
npm install
npm run dev
```

http://localhost:3000 — a development status page showing the backend
connection and which capabilities exist.

The frontend runs on the host rather than in Docker; hot reload through a
Windows bind mount is slow and unreliable. Reasoning in
[ARCHITECTURE.md §5.7](docs/ARCHITECTURE.md).

### 4. Tests

```bash
cd backend && pytest
```

No database or network required — that's deliberate.

---

## Layout

```
CoursePilot/
├── frontend/          Next.js app
├── backend/           FastAPI service — the deterministic core
│   └── app/
│       ├── api/       HTTP routes (thin)
│       ├── core/      config, logging
│       ├── db/        engine, session, migrations
│       ├── domain/    framework-free types: provenance, validation, plan
│       ├── models/    SQLAlchemy ORM (empty on purpose)
│       ├── llm/       provider abstraction
│       └── services/  retrieval · planning · validation · skills
├── ingestion/         Rutgers data pipeline (separate package)
├── skills/            router · shared · majors · tools
├── docs/              architecture + learning guide
├── scripts/           DB init and dev helpers
└── tests/             cross-cutting tests
```

---

## What works today

FastAPI app with `/health`, `/ready`, `/meta` · settings and structured
logging · Postgres + pgvector via Compose · Alembic wired (zero migrations) ·
typed provenance / validation / plan contracts · LLM abstraction with a
deterministic fake · Next.js status page · passing test suite.

## What is not built

Rutgers scraping or API integration · the AI agent · RAG · embeddings · the
degree planner · the schedule optimizer · registration prediction · Google
Calendar · student discussions · **authentication**.

The ORM models and validators are empty **on purpose** — see the next section.

---

## Rutgers data

**This repository contains no Rutgers course data, requirements, endpoints, or
registration rules.** Nothing has been invented or inferred.

Everywhere Rutgers-specific information is required, the code and docs carry
an explicit marker:

```
TODO(rutgers-source)
```

Each one must be resolved from **official Rutgers documentation or a
Rutgers-provided data feed** before the dependent code is written. Open items
are collected in [DATA_MODEL.md §11](docs/DATA_MODEL.md) and
[ingestion/README.md](ingestion/README.md).

Guessing any of them would propagate a wrong assumption straight into the
validator — which would then confidently certify incorrect academic advice.
That is the exact failure this project is built to avoid.

---

## Disclaimer

CoursePilot is a planning aid. It is not affiliated with, endorsed by, or an
official service of Rutgers University. A student's official Rutgers advising
record and their academic advisor are authoritative.
