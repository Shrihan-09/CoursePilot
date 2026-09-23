"""audit cache compression and trigger-maintained rules version

Revision ID: 2b11bfbd8874
Revises: 9f217e335925
Create Date: 2026-09-23 01:07:44.077653

WHY
---
Two measured costs from Phase 5.7:

  * a cache MISS was more expensive than having no cache at all, because
    inserting the 24,277-byte audit took ~46 ms. Measured here: the cost is
    payload SIZE, not column type - a 24 KB bytea was just as slow as 24 KB
    of text, while 3,487 compressed bytes insert in ~2.2 ms.
  * proving rule state by hashing every rule row cost ~14 ms on EVERY audit,
    hit or miss.

WHAT
----
1. `student_audit_cache.result_json` (text) becomes `result_blob` (bytea),
   holding the zlib-compressed bytes of the exact json a fresh audit emits.
2. `student_audit_cache.rules_fingerprint` becomes `rules_token`, which now
   holds either `v:<n>` (the database-maintained version) or `f:<sha256>`
   (the Phase 5.7 fingerprint, on a backend without the triggers).
3. A new `rules_version` table with one row, plus a trigger on every
   audit-relevant rules table that increments it.

THE SCHEMA CONTRACT THIS ESTABLISHES
------------------------------------
    Any database mutation capable of changing Degree Engine rule inputs
    also changes `rules_version.version`.

Enforced at the DATABASE boundary, not by application convention. `psql`, a
migration, a bulk UPDATE, an ingestion job and the ORM all reach the table,
and the trigger fires for all of them. This is why Phase 5.7 rejected
`MAX(updated_at)`: ORM-maintained timestamps miss raw-SQL recuration, and an
ORM event hook would have the identical hole.

Triggers are STATEMENT-level and cover INSERT, UPDATE, DELETE and TRUNCATE.
Statement-level so a bulk UPDATE of 800 eligibility rows costs one bump
rather than 800; TRUNCATE included because emptying `requirement` obviously
changes an audit and is not a row event.

A statement that matches zero rows still bumps the version. That is a
spurious cache miss, not an error: over-invalidation costs a recomputation,
under-invalidation serves a student the wrong degree status.

Tables covered, derived by tracing the engine's inputs:

    program                    name/code/degree_type appear in the result
    program_version            catalog year, credit range, sharing policy
    requirement                the requirement tree
    requirement_course_option  eligibility and category certification
    program_rule               exclusions

`program` is a Phase 5.8 CORRECTION: Phase 5.7's fingerprint never hashed
it, so renaming a program served a stale audit. Demonstrated against the
real development data before being fixed.

HOW EXISTING DATA BEHAVES
-------------------------
`student_audit_cache` is DERIVED state - every row is reconstructable by
running the engine again - so the column change simply drops the old cached
bytes rather than attempting a conversion. Nothing academic is lost, and the
next request per student recomputes. This is the one table in the schema
where discarding data on migration is the correct choice, and it is correct
precisely because the table is not a source of truth.

No academic table is read or written. `student`, `student_course`, `course`,
`user_account` and `student_link_event` are untouched.

The downgrade drops the triggers, the function, the version table, and
restores the text column - again discarding cached bytes only.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "2b11bfbd8874"
down_revision: str | None = "9f217e335925"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


#: Kept in step with app.models.rules_version.RULES_TABLES. A test asserts
#: the installed triggers match that list exactly, so the two cannot drift
#: silently.
RULES_TABLES = (
    "program",
    "program_version",
    "requirement",
    "requirement_course_option",
    "program_rule",
)

_TRIGGER = "trg_rules_version_bump"


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    # --- cache payload: text -> compressed bytea ---------------------------
    # Derived state, so the old rows are discarded rather than converted.
    op.drop_column("student_audit_cache", "result_json")
    op.add_column(
        "student_audit_cache",
        sa.Column("result_blob", sa.LargeBinary(), nullable=False,
                  server_default=sa.text("''::bytea" if _is_postgres() else "''")),
    )
    op.alter_column("student_audit_cache", "result_blob", server_default=None)

    # --- rules_fingerprint -> rules_token ---------------------------------
    # Renamed because the MEANING changed: it may now hold a version rather
    # than a hash. Widened for the prefix.
    op.alter_column(
        "student_audit_cache",
        "rules_fingerprint",
        new_column_name="rules_token",
        type_=sa.String(length=80),
        existing_type=sa.String(length=64),
        existing_nullable=False,
    )

    # Any surviving row was written under the old encoding and must not be
    # read as if it were the new one. Emptying the table is the honest move
    # for a derived cache.
    op.execute("DELETE FROM student_audit_cache")

    # --- rules_version table ----------------------------------------------
    op.create_table(
        "rules_version",
        sa.Column("id", sa.SmallInteger(), nullable=False,
                  server_default=sa.text("1")),
        sa.Column("version", sa.BigInteger(), nullable=False,
                  server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("id = 1", name=op.f("ck_rules_version_single_row")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rules_version")),
    )
    op.execute("INSERT INTO rules_version (id, version) VALUES (1, 1)")

    if not _is_postgres():
        # SQLite gets the table but no triggers. The application detects the
        # absence and falls back to the full fingerprint, which is the Phase
        # 5.7 mechanism and exactly as correct - just slower. Faking a
        # SQLite equivalent would claim a guarantee that was never tested
        # against production semantics.
        return

    op.execute(
        """
        CREATE OR REPLACE FUNCTION bump_rules_version() RETURNS trigger AS $$
        BEGIN
            UPDATE rules_version
               SET version = version + 1, updated_at = now()
             WHERE id = 1;
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    for table in RULES_TABLES:
        op.execute(
            f"""
            CREATE TRIGGER {_TRIGGER}
            AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE ON "{table}"
            FOR EACH STATEMENT EXECUTE FUNCTION bump_rules_version();
            """
        )


def downgrade() -> None:
    if _is_postgres():
        for table in RULES_TABLES:
            op.execute(f'DROP TRIGGER IF EXISTS {_TRIGGER} ON "{table}"')
        op.execute("DROP FUNCTION IF EXISTS bump_rules_version()")

    op.drop_table("rules_version")

    op.execute("DELETE FROM student_audit_cache")
    op.alter_column(
        "student_audit_cache",
        "rules_token",
        new_column_name="rules_fingerprint",
        type_=sa.String(length=64),
        existing_type=sa.String(length=80),
        existing_nullable=False,
    )
    op.drop_column("student_audit_cache", "result_blob")
    op.add_column(
        "student_audit_cache",
        sa.Column("result_json", sa.Text(), nullable=False,
                  server_default=sa.text("''")),
    )
    op.alter_column("student_audit_cache", "result_json", server_default=None)
