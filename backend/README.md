# CoursePilot backend

FastAPI service and the deterministic academic core.

```
app/
├── api/          HTTP routes (thin: validate input, delegate, return)
├── core/         settings, logging
├── db/           engine, session, Alembic migrations
├── domain/       framework-free types: provenance, validation, plan
├── models/       SQLAlchemy ORM
├── llm/          provider abstraction (echo fake by default)
└── services/     retrieval · planning · validation · skills (contracts only)
```

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -e ".[dev]"
uvicorn app.main:app --reload
```

## Tests

```bash
pytest
```

No database or network required.

## Migrations

```bash
alembic upgrade head                        # apply
alembic revision --autogenerate -m "..."    # create
```

Every model must be imported in `app/models/__init__.py`, or autogenerate will
not see it and will silently omit its table.

See the repository root `README.md` and `docs/` for architecture.
