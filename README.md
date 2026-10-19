# CoursePilot

Rutgers degree-planning and schedule-optimization platform.

> **Status: a tested backend and a working student web app (Degree, Plan, Schedule).**
>
> - **Engines:** the deterministic engines exist (degree audit, prerequisites,
>   planner, schedules, availability monitoring), together with the HTTP API
>   and the Rutgers data pipeline.
> - **Web app:** sign-in and onboarding (Phase 7.1), and the core workflow
>   (Phase 7.2):
>   - **Degree:** requirement-by-requirement progress, with reasons, and
>     what-if comparisons;
>   - **Plan:** a saved deterministic semester plan that knows when it is out
>     of date;
>   - **Schedule:** real Rutgers sections, conflict-free options, a weekly
>     view, and index numbers to register with yourself.
> - **Not yet in the web app:** course search, notifications, the assistant.
> - **Programs:** one is published (human-reviewed); seven more are encoded and
>   pending review.
>
> See [What works today](#what-works-today) and
> [What is not built](#what-is-not-built).

---

## The idea

A Rutgers student should be able to ask "what should I take next semester?"
and get an answer that is **actually correct**, not merely fluent.

That distinction drives the whole design:

> **The LLM proposes. Deterministic code decides.**

A language model is good at interpreting a vague question, weighing tradeoffs
and explaining a plan. It is not a reliable authority on whether a specific
student has a specific prerequisite, so CoursePilot never lets it be one. Every
academic claim is computed by ordinary, testable Python from public Rutgers
data and the student's own record. A model may only *explain* a result the
engine produced.

---

## Documentation

| Document | Contents |
|---|---|
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | System design, layers, and every major decision with its tradeoffs |
| [DATA_MODEL.md](docs/DATA_MODEL.md) | Schema and semantics, phase by phase (sections 12–48 describe what is built) |
| [AUTHENTICATION.md](docs/AUTHENTICATION.md) | Sign-in, and the three trust boundaries: identity, academic data, institutional authorization |
| [DESIGN_SYSTEM.md](docs/DESIGN_SYSTEM.md) | Tokens, components, responsive shell, motion language, accessibility |
| [AGENT_ARCHITECTURE.md](docs/AGENT_ARCHITECTURE.md) | Agent flow and the anti-hallucination safeguards |
| [RAG_ARCHITECTURE.md](docs/RAG_ARCHITECTURE.md) | Retrieval strategies and how they are measured |
| [DECISION_TREE.md](docs/DECISION_TREE.md) | Step-by-step flows for each kind of student request |
| [LEARNING.md](docs/LEARNING.md) | The engineering learning guide, one lesson per phase |

---

## Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.12+, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic |
| Database | PostgreSQL 16 + pgvector (`pgvector/pgvector:pg16`) |
| Ingestion | Separate Python package (`ingestion/`), public Rutgers sources only |
| Frontend | Next.js 15 (App Router), React 19, TypeScript, Tailwind, Geist; OIDC via oauth4webapi |
| Local dev | Docker Compose: `db`, `backend`, `live-worker` |

---

## Quick start

**Prerequisites:** Docker Desktop, Python 3.12+, Node 20+, Git.

```bash
cp .env.example .env
docker compose up -d db                      # PostgreSQL 16 + pgvector

cd backend
python -m venv .venv
.venv\Scripts\activate                       # Windows
pip install -e ".[dev,postgres]"
alembic upgrade head
uvicorn app.main:app --reload                # http://localhost:8000/docs
```

Then the web app, in another terminal:

```bash
cd frontend
npm install
AUTH_MODE=dev npm run dev                    # http://localhost:3000
```

For local development, run the API with `AUTH_PROVIDER=dev`,
`DEV_AUTH_ENABLED=true` and `COURSEPILOT_ENV=local`, and the web app with
`AUTH_MODE=dev`. That gives a "development sign-in" where any name signs in.
Both halves refuse it in production.

Real sign-in is OIDC Authorization Code + PKCE, requesting `openid email`
only, against any standards-based provider (for example Google). See
[docs/AUTHENTICATION.md](docs/AUTHENTICATION.md) for the configuration and for
what signing in does and does not grant.

### Tests

```bash
cd backend && pytest            # API, engines, onboarding; PostgreSQL tests need TEST_DATABASE_URL
cd ingestion && pytest          # pipeline, real-data fixtures (SQLite by default)
pytest                          # from the repository root: everything
```

PostgreSQL-only tests run against a database whose name ends in `_test`
(`TEST_DATABASE_URL=postgresql+psycopg://coursepilot:coursepilot@localhost:5432/coursepilot_test`)
and are skipped without one. `scripts/inject_*_failures.py` deliberately break
the code and check that the tests notice.

---

## What works today

**Rutgers data (public sources only).**

- Schedule of Classes courses, sections, meetings and restrictions for the
  loaded terms.
- Catalog requirement definitions with a review lifecycle (parsed → validated
  → reviewed → published).
- Prerequisites parsed into expression trees.
- Live seat availability from the public `openSections` feed.

**Engines (deterministic, no LLM in any decision).**

- **Degree audit.** Requirement trees, grade semantics, retakes, transfer and
  exam credit, allocation, and a fingerprint-keyed cache.
- **Prerequisites and co-requisites.**
- **Semester planner.**
- **Conflict-free section schedules.**
- **What-if scenarios** for other programs.
- **Seat-opening watches** with an inbox and push delivery: APNs or a fake
  provider; no physical device has been tested.

**Student onboarding (Phase 7.0).** An authenticated student can create their
own profile, choose their program and catalog year, enter courses, or import
their **unofficial transcript PDF**. The import goes through staged extraction,
row-by-row review and explicit confirmation. Nothing from a document becomes
history without the student's confirmation. See
[DATA_MODEL.md §46](docs/DATA_MODEL.md).

**Student web app (Phases 7.1-7.2).**

- **Degree, Plan and Schedule pages.** They render the engines' results and
  decide nothing academic themselves.
- **Saved plans.** A plan is saved with fingerprints of its inputs, and is
  flagged out of date when your record, program, its rules or the Rutgers data
  change.
- **Unknowns are labelled.** Conditions CoursePilot cannot evaluate are
  labelled as such, never as failures.
- **Live availability.** It is shown apart from schedule validity.

See [DATA_MODEL.md §48](docs/DATA_MODEL.md).

**API.** Under `/api/v1`, all of the above. The student is always the
authenticated account's own record; no request can name another student.

## What is not built

- **Some student-facing screens.** Course search, Notifications and the
  assistant are in the navigation as "Later".
  - Plans cannot be edited by hand; you regenerate them with different
    settings.
  - Spring 2027 sections are not loaded yet, because Rutgers had not
    published them.
  - There is no mobile app yet; the design tokens and the OIDC account model
    are built to carry over to one.
- **A configured production identity provider.** The OIDC flow is tested
  end to end against a local provider. It has also been exercised in local
  development with a real Google client and a ScarletMail-backed Google
  account. No production client is configured. Institution ("Rutgers email")
  verification stays off: no `hd` value has been confirmed, and one account
  would not establish a rule.
- **Program coverage.** Computer Science BA is published. Economics,
  Linguistics, Mathematics, Philosophy, Psychology, Sociology and Statistics
  are encoded but **pending human review**, and every audit of them says so.
  There is one catalog year (2026-2027) and one program per student: no
  double majors, minors, or school-wide core outside the two programs that
  include it.
- **Broad transcript coverage.**
  - The parser reads the real Rutgers unofficial-transcript layout, and was
    checked against one real transcript (16/16 courses).
  - Transfer and AP sections, credit prefixes and other schools' or older
    transcripts have not been seen in a real document yet.
  - Two-column reading-order problems are detected and sent to review, never
    guessed.
  - Scanned or photographed transcripts (no text layer) are refused.
  - See [DATA_MODEL.md §46.6](docs/DATA_MODEL.md).
- **Production deployment.** Hosting, real OIDC configuration and Apple push
  credentials.
- **Registration.** By design: CoursePilot never logs in to Rutgers systems
  (CAS, WebReg, Degree Navigator), never asks for a Rutgers password, and never
  registers anyone.

---

## Layout

```
CoursePilot/
├── backend/           FastAPI service - the deterministic core
│   └── app/
│       ├── api/       HTTP routes (thin), auth, error envelope
│       ├── core/      config, logging, metrics
│       ├── db/        engine, session, Alembic migrations
│       ├── domain/    framework-free rules: grades, attempts, prerequisites, ...
│       ├── models/    SQLAlchemy ORM
│       ├── llm/       provider abstraction (explanations only)
│       └── services/  audit · planning · scheduling · onboarding · transcripts · live
├── ingestion/         Rutgers public-data pipeline and worker CLIs
├── data/programs/     curated requirement definitions + review packets
├── frontend/          Next.js web app: auth, onboarding, design system, brand assets
├── docs/              architecture, data model, learning guide
└── scripts/           DB init, benchmarks, failure injection
```

---

## Rutgers data

Every Rutgers fact in this repository comes from a public Rutgers source:

- the Schedule of Classes API;
- catalog pages;
- the public `openSections` feed.

Each fact carries its provenance (`data_source`). Requirement encodings stay
**pending review** until a person has checked them against the official prose.
Where Rutgers does not say something, CoursePilot reports *unknown*; it never
guesses.

---

## Disclaimer

CoursePilot is a planning aid. It is not affiliated with, endorsed by, or an
official service of Rutgers University. A student's official Rutgers advising
record and their academic advisor are authoritative.
