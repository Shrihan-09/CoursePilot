"""SQLAlchemy declarative base and shared column conventions.

No tables are defined yet. `app/models/` is intentionally empty — the schema
in docs/DATA_MODEL.md is a design, not yet an implementation, and it should be
reviewed before it becomes migrations.

The naming convention below matters: without it, Alembic autogenerate emits
database-assigned constraint names that differ between environments, which
makes later `ALTER`/`DROP` migrations fail unpredictably. Set it before the
first migration; changing it afterwards is painful.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    """Row-level audit timestamps. Distinct from *provenance* timestamps:
    these record when CoursePilot's row changed, while `SourceRef.retrieved_at`
    records when the underlying Rutgers fact was fetched. Both are needed."""

    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
