"""Provenance tables.

Mirrors the in-memory types in `app/domain/provenance.py`. One row per
distinct fetch of a source document, so any Rutgers-derived record can be
traced back to the exact response it came from.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class DataSource(Base, TimestampMixin):
    """One retrieval of one source.

    Re-fetching the same URL creates a NEW row when the content has changed,
    and reuses the existing row when it has not (matched on `content_hash`).
    That way a record's `source_id` points at the exact payload that produced
    it, and unchanged re-ingestion does not churn the table.
    """

    __tablename__ = "data_source"

    # sqlalchemy.Uuid (not the postgresql-specific type) so the same models
    # can run against SQLite in tests. Renders as native UUID on Postgres.
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    kind: Mapped[str] = mapped_column(String(64))
    url: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)

    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source_last_modified: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # sha256 of the raw response body. Detects change without re-parsing, and
    # makes re-ingestion idempotent.
    content_hash: Mapped[str] = mapped_column(String(64), index=True)

    # Where the archived raw payload lives. We keep the original bytes so a
    # parser bug can be fixed by reprocessing rather than re-scraping — the
    # source may have moved on by then.
    raw_payload_ref: Mapped[str | None] = mapped_column(Text)

    # Rutgers data is term-scoped. A fact true for one term is not
    # automatically true for another, so the term travels with the source.
    academic_year: Mapped[str | None] = mapped_column(String(16))
    term_code: Mapped[str | None] = mapped_column(String(16), index=True)

    version: Mapped[int] = mapped_column(Integer, default=1)
    record_count: Mapped[int | None] = mapped_column(Integer)

    __table_args__ = (
        Index("ix_data_source_kind_term", "kind", "term_code"),
    )

    def __repr__(self) -> str:
        return f"<DataSource {self.kind} {self.term_code} {self.content_hash[:8]}>"
