"""Rutgers term registry (Phase 7.2.4): what Rutgers has PUBLISHED vs what CoursePilot LOADED.

Three facts are kept apart, because confusing them produced "Spring 2027 isn't
published yet" while Rutgers was already publishing it:

    publication_state   what the public SOC returned when last checked
    ingestion_state     whether CoursePilot loaded and VALIDATED that payload
    planning ready      both of the above (see app.services.term_authority)

Counts and fingerprints only - no course content is stored here.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

#: publication_state
UNCHECKED, NOT_PUBLISHED, PUBLISHED, CHECK_FAILED = (
    "unchecked", "not_published", "published", "check_failed")
#: ingestion_state
NOT_INGESTED, INGESTED, INGESTION_FAILED = "not_ingested", "ingested", "failed"


class RutgersTerm(Base, TimestampMixin):
    __tablename__ = "rutgers_term"
    __table_args__ = (UniqueConstraint("term_code", "campus", name="uq_rutgers_term_campus"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    term_code: Mapped[str] = mapped_column(String(16), index=True)
    campus: Mapped[str] = mapped_column(String(8), default="NB")
    source: Mapped[str] = mapped_column(String(64), default="rutgers_soc_courses_json")

    publication_state: Mapped[str] = mapped_column(String(16), default=UNCHECKED)
    #: First time the source returned a NON-EMPTY schedule for this term.
    first_published_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Last successful fetch (any HTTP 200 with a well-formed body).
    last_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Fingerprint and size of the most recent payload seen.
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    course_count: Mapped[int] = mapped_column(Integer, default=0)
    section_count: Mapped[int] = mapped_column(Integer, default=0)

    ingestion_state: Mapped[str] = mapped_column(String(16), default=NOT_INGESTED)
    #: The payload fingerprint CoursePilot loaded and validated (may lag content_hash).
    ingested_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_validated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    #: Closed code only (e.g. SECTION_COUNT_MISMATCH); never response text.
    last_error: Mapped[str | None] = mapped_column(String(64), nullable=True)

    #: Whether the live open/closed source answers for this term.
    availability_supported: Mapped[bool] = mapped_column(Boolean, default=False)
    #: Aggregate source disagreements (counts), recorded - never resolved silently.
    disagreements: Mapped[dict] = mapped_column(JSON, default=dict)

    # Phase 7.3 term watcher ------------------------------------------------
    #: Recent successful fetches, newest last: {at, hash, courses, sections} (bounded).
    observations: Mapped[list] = mapped_column(JSON, default=list)
    #: 'partial' until stability evidence promotes it; never inferred.
    coverage_state: Mapped[str] = mapped_column(String(16), default="partial",
                                                server_default="partial")
    #: The stated evidence for a promotion to 'complete' (human-readable).
    coverage_evidence: Mapped[str | None] = mapped_column(String(256), nullable=True)
    coverage_promoted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
