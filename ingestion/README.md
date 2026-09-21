# Ingestion

Turns authoritative Rutgers sources into normalized, provenance-tagged rows.

**Status: working.** Course ingestion (Phase 1) and section ingestion
(Phase 2) from the Rutgers Schedule of Classes JSON API are implemented,
tested, and verified against PostgreSQL 16.

Not implemented: prerequisites-as-structure (prose is stored verbatim), course
descriptions (SOC provides none), section restriction lists, and degree
requirements.

See `docs/DATA_SOURCES.md` for what was verified about the source, and
`docs/LEARNING.md` (Lesson 2) for the concepts behind the design.

## Why this is a separate package

Ingestion is batch, slow, network-bound, and failure-prone; the API is none of
those. Keeping them apart means a scraping change cannot destabilize the
request path.

It writes to the same database through the same models.

> **Known coupling:** this package imports `app.models` from the backend.
> Those shared models should eventually move into their own package that both
> depend on. Tracked in `docs/ARCHITECTURE.md`.

## Pipeline

```
Rutgers SOC API
      |
   Fetcher      HTTP + retry; archives raw bytes to data/raw/; sha256
      |          fetchers/soc.py
   Parser       JSON -> RawSocCourse; reports bad records, never repairs them
      |          parsers/soc.py
  Normalizer    Rutgers vocabulary -> CoursePilot vocabulary
      |          normalizers/soc.py
  Validator     cross-field + cross-record checks; loud, specific rejections
      |          validators/course.py
   Loader       look-up-then-upsert on the natural key (idempotent)
      |          loaders/postgres.py
  PostgreSQL
```

Each stage is independently testable and knows nothing about the others.

## Setup

Install both packages into one environment (ingestion imports the backend's
models):

```bash
pip install -e ./backend -e ./ingestion
pip install "psycopg[binary]"          # database driver
```

## Running it

```bash
# 1. start the database
docker compose up -d db

# 2. create the schema
cd backend && alembic upgrade head && cd ..

# 3. point at the database
export DATABASE_URL_SYNC="postgresql+psycopg://coursepilot:coursepilot@localhost:5432/coursepilot"
#  PowerShell:
#  $env:DATABASE_URL_SYNC="postgresql+psycopg://coursepilot:coursepilot@localhost:5432/coursepilot"

# 4. ingest a small slice of courses, then query it back
python -m coursepilot_ingestion.cli --limit 25

# 5. courses + their sections, full term
python -m coursepilot_ingestion.cli --stage both --all

# just show what is already stored
python -m coursepilot_ingestion.cli --show-only
```

**Sections require their courses first.** A section is attached to a
`course_offering`, so `--stage sections` can only place a section whose course
is already loaded. Unplaceable sections are counted and listed as
`unmatched offering` - never dropped silently, and never used to invent a
course. Use `--stage both` to load them in the right order.

### Options

| Flag | Meaning |
|---|---|
| `--stage` | `courses` (default), `sections`, or `both` |
| `--limit N` | max courses to **load** (default 25) |
| `--all` | load everything (overrides `--limit`) |
| `--subject 198` | restrict to one subject code |
| `--year / --term / --campus` | which SOC payload (only `2026 / 9 / NB` verified) |
| `--refetch` | bypass the local archive and re-download |
| `--show-only` | query the database and exit |

### Verified scale

One full run of `--stage both --all` against PostgreSQL 16.15 (Fall 2026, NB),
from the local archive:

| Table | Rows |
|---|---|
| `course` | 4,391 |
| `course_offering` | 4,400 |
| `course_section` | 11,992 |
| `section_meeting` | 17,457 |
| `section_instructor` | 12,067 |
| `section_cross_listing` | 966 |

0 parse failures, 0 validation failures, 0 unmatched sections. Re-running
inserts nothing.

`--limit` and `--subject` restrict what is **loaded**, never what is fetched or
archived. A narrow run still archives the whole payload, so it cannot leave a
truncated file that a later, wider run would silently trust.

## Tests

```bash
cd ingestion && pytest          # 47 tests, no network, no database
```

### PostgreSQL integration tests

> **These tests `TRUNCATE` every table.** Point them at a dedicated test
> database, never at your development one, or you will lose ingested data.

One-time setup:

```bash
docker exec coursepilot-db psql -U coursepilot -d postgres \
  -c "CREATE DATABASE coursepilot_test OWNER coursepilot;"

cd backend
DATABASE_URL_SYNC="postgresql+psycopg://coursepilot:coursepilot@localhost:5432/coursepilot_test" \
  alembic upgrade head
```

Then run them:

```bash
cd ingestion
export TEST_DATABASE_URL="postgresql+psycopg://coursepilot:coursepilot@localhost:5432/coursepilot_test"
pytest -m db
```

`TEST_DATABASE_URL` takes precedence over `DATABASE_URL_SYNC`, which is what
keeps the two apart. Without it the tests fall back to `DATABASE_URL_SYNC` -
i.e. your development database.

These tests skip automatically when no Postgres is reachable. They matter
because SQLite is **not** proof the schema works on Postgres: `Numeric`
precision, native `Uuid`, and constraint behavior all differ.

**Status: verified.** All 4 pass against PostgreSQL 16.15.

## Rules any future ingestor must follow

1. **Persist the raw payload before parsing.** When a parser turns out to be
   wrong six weeks later, reprocessing beats re-scraping - and re-scraping may
   be impossible, since the source has moved on.

2. **Never fabricate.** If a field cannot be parsed, record it as missing. A
   `null` prerequisite is honest; an inferred one is dangerous, because the
   validator will treat it as authoritative.

3. **Everything is term-scoped.** Every row carries its academic year.

4. **Ingestion is idempotent.** Re-running against unchanged source produces
   no duplicates and no new provenance rows.

5. **Changes are versioned, not overwritten.** A changed payload gets a new
   `data_source` row; the old one is retained.

6. **Be a polite client.** Rate limit, identify the client honestly, cache
   aggressively. The archive means development runs cost Rutgers nothing.

## Still needed - `TODO(rutgers-source)`

- [ ] Terms of use for the SOC endpoint (**confirm before scaling beyond a prototype**)
- [ ] Authoritative source for course **descriptions** - SOC returns none
- [ ] Authoritative source for degree requirements, per school
- [ ] Confirm the full term-code and campus-code sets
- [ ] Whether historical terms remain queryable
