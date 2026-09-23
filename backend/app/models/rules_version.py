"""Database-maintained rules-state version (Phase 5.8).

> Any database mutation capable of changing Degree Engine rule inputs also
> changes this version. The guarantee is enforced at the **database
> boundary**, not by application convention.

## Why this exists

Phase 5.7 proved rule-state identity by hashing every rule row - correct,
and ~14 ms on every audit. This replaces the hash on the hot path with a
single-row read, without weakening the guarantee, because the version is
maintained by PostgreSQL triggers rather than by application code that
someone could forget to call.

That distinction is the whole point:

```
ORM event hooks   fire when the write went through SQLAlchemy   DISCIPLINE
database triggers fire when the write reached the table         CONSTRUCTION
```

Phase 5.7 rejected `MAX(updated_at)` because ORM-maintained timestamps miss
raw-SQL recuration. A trigger has no such gap: `psql`, a migration, a bulk
`UPDATE`, an ingestion job and the ORM all go through the table.

## One row, one counter

```sql
rules_version(id = 1, version bigint, updated_at)
```

A single row, pinned by `CHECK (id = 1)`, so "the rules version" is
unambiguous and a reader never has to decide which row is current.

## Global rather than per-program

A change to any program's rules bumps the counter for every program. That
over-invalidates: recurating Computer Science makes a History student's
cached audit unreachable.

Accepted deliberately. Per-program versioning would need each trigger to
resolve its row's owning `program_version` - and for
`requirement_course_option` that means a lookup through `requirement`, which
may already be gone when a cascading delete fires the trigger. Subtle
ordering logic in a correctness-critical path buys, at present, nothing:
CoursePilot has one program, and recuration is a human reading catalog prose,
not a frequent event. Recuration is also precisely when recomputation is
*wanted*.

**Over-invalidation costs a recomputation. Under-invalidation serves a
student the wrong degree status.** When choosing which way to be wrong,
choose the one that is merely slow.

Per-program versioning becomes worth its complexity when there are many
programs *and* recuration is frequent. Neither is true yet.

## What is NOT covered

A rule-relevant table that nobody declared. Triggers protect known tables
against unknown *writes*; nothing protects against an unknown *table*.
`RULES_TABLES` is the declared list, and a test asserts the installed
triggers match it exactly - so a table added to the list without a migration,
or a trigger dropped without updating the list, fails loudly. A table added
to neither is invisible, and that is written down rather than pretended away.
"""

from __future__ import annotations

from sqlalchemy import BigInteger, CheckConstraint, SmallInteger, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

#: Tables whose rows are read by `DegreeAuditEngine.audit()` as RULE state.
#:
#: Derived by tracing the engine's inputs (Phase 5.8, Part 7), not guessed:
#:
#:   program_version            catalog year, credit range, sharing policy
#:   program                    name, code, degree_type - all appear in the
#:                              DegreeAuditResult, and Phase 5.7's fingerprint
#:                              MISSED this table, so renaming a program
#:                              served a stale audit
#:   requirement                the requirement tree itself
#:   requirement_course_option  which courses are eligible, and in which category
#:   program_rule               exclusions that change which credits count
#:
#: Deliberately absent: `school` (no field of it reaches the result),
#: `data_source` (the engine reads provenance columns on the rule rows
#: themselves, never the source table), and `course`/`student_course`, which
#: are STUDENT state and belong to the academic fingerprint.
RULES_TABLES: tuple[str, ...] = (
    "program",
    "program_version",
    "requirement",
    "requirement_course_option",
    "program_rule",
)


class RulesVersion(Base, TimestampMixin):
    """The single row PostgreSQL triggers increment. Never written by hand."""

    __tablename__ = "rules_version"

    #: Pinned to 1 by a CHECK constraint - there is exactly one rules version.
    id: Mapped[int] = mapped_column(
        SmallInteger, primary_key=True, default=1, server_default=text("1")
    )
    version: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=1, server_default=text("1")
    )

    __table_args__ = (CheckConstraint("id = 1", name="single_row"),)

    def __repr__(self) -> str:
        return f"<RulesVersion {self.version}>"
