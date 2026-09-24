"""search corpus version triggers

Revision ID: ce2b9afd3fa7
Revises: 2b11bfbd8874
Create Date: 2026-09-24 01:38:19.129618

WHY
---
Phase 5.10 measured the explanation endpoint and found the BM25 index was
being rebuilt on every request. Measured here on the real development
corpus (4,415 courses, 318 subjects, 88 catalog entries):

    build_course_documents   130.36 ms
    build_bm25               133.21 ms
    ------------------------ --------
    per request              ~264 ms   of a ~336 ms endpoint  (78%)

    one search on a built index  0.09 ms

So the index must be built once and reused. Reuse is only safe if the
application can tell when the corpus underneath it has changed - and
**ingestion runs in a separate process** (`coursepilot_ingestion/cli.py`
builds its own engine), so an in-memory invalidation hook in the API process
cannot observe that write. Without a database-visible signal the only
options would be serving stale search results or requiring an API restart
after every ingestion.

That is the demonstrated cross-process requirement, and it is the sole
reason this phase persists anything.

WHAT
----
`search_version`: one row, incremented by statement-level triggers on the
three tables `build_course_documents` actually reads.

THE SCHEMA CONTRACT THIS ESTABLISHES
------------------------------------
    Any database mutation capable of changing BM25 search results also
    changes `search_version.version`.

Identical in shape to Phase 5.8's `rules_version`, deliberately: that
mechanism is already proven, tested against raw SQL, ORM, bulk and TRUNCATE
mutations, and understood. Reusing a known pattern is less new machinery
than inventing a second one.

A SEPARATE counter from `rules_version`, also deliberately. The two are
independent inputs:

    rules_version    requirement recuration -> the AUDIT changes
    search_version   catalog/SOC ingestion  -> SEARCH RESULTS change

Sharing one would rebuild the 264 ms BM25 index whenever a requirement was
recurated - which cannot affect any search result - and would invalidate
every cached audit whenever a course title was corrected.

Tables covered, traced from `build_course_documents` rather than assumed:

    course                 identity, title, credits, level, subject_code
    subject                description (the subject_name field)
    catalog_course_entry   description and catalog_year

Not covered, and correctly so: the requirement/program tables (they change
the audit, never a search result) and `data_source` - `course_provenance`
is a hardcoded literal and the only varying provenance, `catalog_year`,
comes from `catalog_course_entry`.

Triggers are STATEMENT-level and cover INSERT, UPDATE, DELETE and TRUNCATE,
so a bulk ingestion of 4,415 courses costs one bump rather than 4,415, and
a TRUNCATE-and-reload is not missed for not being a row event. A statement
matching zero rows still bumps: a spurious rebuild costs 264 ms, while a
missed bump serves a stale catalog indefinitely.

HOW EXISTING DATA BEHAVES
-------------------------
Additive. No existing table is touched, nothing is backfilled, and no
academic or catalog row is read or written. The counter starts at 1 and the
first request after deployment builds the index, exactly as every request
did before.

The downgrade drops the triggers, the function and the table. The
application detects their absence and falls back to building per request -
the pre-5.11 behaviour, slower and equally correct.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "ce2b9afd3fa7"
down_revision: str | None = "2b11bfbd8874"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


#: Kept in step with app.models.search_version.SEARCH_TABLES. A test asserts
#: the installed triggers match that list exactly, so the two cannot drift.
SEARCH_TABLES = ("course", "subject", "catalog_course_entry")

_TRIGGER = "trg_search_version_bump"


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    op.create_table(
        "search_version",
        sa.Column("id", sa.SmallInteger(), nullable=False,
                  server_default=sa.text("1")),
        sa.Column("version", sa.BigInteger(), nullable=False,
                  server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("id = 1", name=op.f("ck_search_version_single_row")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_search_version")),
    )
    op.execute("INSERT INTO search_version (id, version) VALUES (1, 1)")

    if not _is_postgres():
        # SQLite gets the table but no triggers. The registry detects their
        # absence and rebuilds per request, which is the pre-5.11 behaviour:
        # slower, and equally correct. Faking a SQLite equivalent would claim
        # a guarantee never tested against production semantics.
        return

    op.execute(
        """
        CREATE OR REPLACE FUNCTION bump_search_version() RETURNS trigger AS $$
        BEGIN
            UPDATE search_version
               SET version = version + 1, updated_at = now()
             WHERE id = 1;
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    for table in SEARCH_TABLES:
        op.execute(
            f"""
            CREATE TRIGGER {_TRIGGER}
            AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE ON "{table}"
            FOR EACH STATEMENT EXECUTE FUNCTION bump_search_version();
            """
        )


def downgrade() -> None:
    if _is_postgres():
        for table in SEARCH_TABLES:
            op.execute(f'DROP TRIGGER IF EXISTS {_TRIGGER} ON "{table}"')
        op.execute("DROP FUNCTION IF EXISTS bump_search_version()")
    op.drop_table("search_version")
