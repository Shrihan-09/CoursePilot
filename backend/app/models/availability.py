"""Registration availability observations (Phase 6.7).

Structural facts (course, offering, section, meetings) and volatile
availability are kept apart: `course_section` is written by the catalog/SOC
ingestion; these tables are written ONLY by the availability refresh
(app.services.availability / coursepilot_ingestion.pipelines.availability),
which never touches structural rows. See DATA_MODEL section 43 and
docs/investigations/phase-6-7-live-availability.md.

    availability_refresh          one row per poll of openSections.json - when,
                                  which payload (hash), accepted or why rejected
    section_availability          one row per section: its CURRENT state and
                                  since when (rows change only when the state does)
    section_availability_change   append-only: one row per state TRANSITION -
                                  what a future notification ("closed -> open")
                                  or predictor (time-to-close) needs

Why not one row per section per poll: 12,012 sections x a 2-minute cadence is
~8.6 M rows/day for information that is almost entirely "unchanged". "Still
closed as of T" is recoverable from the refresh log instead.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

AVAILABILITY_STATUSES = ("open", "closed", "unknown")
REFRESH_VERDICTS = ("accepted", "rejected_malformed", "rejected_empty",
                    "rejected_no_overlap_with_term", "rejected_implausible_drop",
                    "rejected_term_mismatch", "rejected_fetch_failed")
_STATUS_CHECK = "IN ('" + "','".join(AVAILABILITY_STATUSES) + "')"


class AvailabilityRefresh(Base, TimestampMixin):
    """One observation of `openSections.json` for one term."""

    __tablename__ = "availability_refresh"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    term_code: Mapped[str] = mapped_column(String(16))
    source_url: Mapped[str] = mapped_column(Text)
    #: CoursePilot's fetch time. Rutgers publishes no "last updated" time.
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    content_sha256: Mapped[str | None] = mapped_column(String(64))
    body_bytes: Mapped[int | None] = mapped_column(Integer)
    entries: Mapped[int | None] = mapped_column(Integer)       # list length as published
    duplicates: Mapped[int | None] = mapped_column(Integer)
    matched: Mapped[int | None] = mapped_column(Integer)       # open indexes of this term
    ignored: Mapped[int | None] = mapped_column(Integer)       # other campuses / unknown indexes
    transitions: Mapped[int | None] = mapped_column(Integer)   # state changes recorded
    verdict: Mapped[str] = mapped_column(String(40))
    detail: Mapped[str | None] = mapped_column(Text)
    archive_path: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint("verdict IN ('" + "','".join(REFRESH_VERDICTS) + "')",
                        name="verdict_known"),
        # "latest accepted refresh of term T" - the freshness of every state.
        Index("ix_availability_refresh_term_observed", "term_code", "observed_at"),
    )


class SectionAvailability(Base, TimestampMixin):
    """A section's CURRENT availability. Updated only when the state changes;
    its "as of" time is the latest ACCEPTED refresh of its term."""

    __tablename__ = "section_availability"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    section_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("course_section.id", ondelete="CASCADE"), unique=True)
    term_code: Mapped[str] = mapped_column(String(16))
    index_number: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16))
    #: First observation of the current state.
    since: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    changed_by_refresh_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("availability_refresh.id", ondelete="RESTRICT"))

    __table_args__ = (
        CheckConstraint("status " + _STATUS_CHECK, name="status_known"),
        Index("ix_section_availability_term_index", "term_code", "index_number", unique=True),
    )


class SectionAvailabilityChange(Base, TimestampMixin):
    """Append-only: one row per state transition (never per unchanged poll)."""

    __tablename__ = "section_availability_change"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    # Kept if the section is later removed from SOC: history outlives structure.
    section_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("course_section.id", ondelete="SET NULL"))
    term_code: Mapped[str] = mapped_column(String(16))
    index_number: Mapped[str] = mapped_column(String(16))
    from_status: Mapped[str | None] = mapped_column(String(16))   # NULL = first observation
    to_status: Mapped[str] = mapped_column(String(16))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    refresh_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("availability_refresh.id", ondelete="RESTRICT"))

    __table_args__ = (
        CheckConstraint("from_status IS NULL OR from_status " + _STATUS_CHECK,
                        name="from_status_known"),
        CheckConstraint("to_status " + _STATUS_CHECK, name="to_status_known"),
        CheckConstraint("from_status IS NULL OR from_status <> to_status",
                        name="is_a_change"),
        # a future notifier reads "changes of term T since time X"
        Index("ix_availability_change_term_observed", "term_code", "observed_at"),
        Index("ix_availability_change_section", "section_id"),
        # Phase 6.8: the event processor scans only openings (rare), in order.
        Index("ix_availability_change_openings", "observed_at", "id",
              postgresql_where=text("from_status = 'closed' AND to_status = 'open'"),
              sqlite_where=text("from_status = 'closed' AND to_status = 'open'")),
    )


__all__ = ["AVAILABILITY_STATUSES", "REFRESH_VERDICTS", "AvailabilityRefresh",
           "SectionAvailability", "SectionAvailabilityChange"]
