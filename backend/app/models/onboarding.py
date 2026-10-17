"""Transcript import staging (Phase 7.0; DATA_MODEL section 46).

```
upload -> extraction -> normalization -> validation -> STAGING
       -> student review -> explicit confirmation -> student_course
```

Nothing a parser produces is academic history. It lands here, in rows the
Degree Engine never reads, and becomes `student_course` only when the
student confirms - in one transaction that re-validates every row against
the record as it is at that moment.

  * `transcript_import`     - one uploaded document and its lifecycle;
  * `transcript_import_row` - one course line: the ORIGINAL extracted values
    (never overwritten), the normalized values (which the student may
    correct), the structured issues, and what confirmation did with it.

No file name is stored anywhere: the upload API takes the document as the
request body, so there is none - a file name is often the student's name or
RUID. The raw document itself is NOT retained: it is deleted from storage as
soon as extraction finishes, success or failure (`storage_key` is set only
while it exists).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class ImportState:
    #: Row exists, document stored, extraction running. A crash leaves it
    #: here; `stale_processing` treats an old one as failed.
    PROCESSING = "processing"
    #: Staged and under review.
    EXTRACTED = "extracted"
    #: Extraction refused the document (failure_code says why). No rows.
    FAILED = "failed"
    #: Written to student_course. Terminal.
    CONFIRMED = "confirmed"
    #: The student threw it away. Terminal; staged rows deleted.
    DISCARDED = "discarded"


IMPORT_STATES = (ImportState.PROCESSING, ImportState.EXTRACTED, ImportState.FAILED,
                 ImportState.CONFIRMED, ImportState.DISCARDED)
#: States in which one document (by content hash) may exist once per student.
_OPEN = "state IN ('processing','extracted')"


class RowState:
    MATCHED = "matched"              # storable as it stands
    NEEDS_REVIEW = "needs_review"    # a person must supply or decide something
    UNRECOGNIZED = "unrecognized"    # no CoursePilot course has this code
    CONFLICT = "conflict"            # disagrees with the recorded history
    INVALID = "invalid"              # cannot be stored as it stands


ROW_STATES = (RowState.MATCHED, RowState.NEEDS_REVIEW, RowState.UNRECOGNIZED,
              RowState.CONFLICT, RowState.INVALID)


class Resolution:
    KEEP_EXISTING = "keep_existing"
    REPLACE_EXISTING = "replace_existing"


RESOLUTIONS = (Resolution.KEEP_EXISTING, Resolution.REPLACE_EXISTING)


class RowOutcome:
    INSERTED = "inserted"
    REPLACED = "replaced"
    SKIPPED = "skipped"              # already recorded identically, or keep_existing
    EXCLUDED = "excluded"


class TranscriptImport(Base, TimestampMixin):
    __tablename__ = "transcript_import"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    student_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("student.id", ondelete="CASCADE"), index=True)
    state: Mapped[str] = mapped_column(String(16))
    #: Replay detection: the same bytes uploaded twice while open are one import.
    content_sha256: Mapped[str] = mapped_column(String(64))
    byte_size: Mapped[int] = mapped_column(Integer)
    page_count: Mapped[int | None] = mapped_column(Integer)
    parser_version: Mapped[str | None] = mapped_column(String(64))
    failure_code: Mapped[str | None] = mapped_column(String(48))
    #: Opaque storage handle while the raw document exists; NULL afterwards.
    storage_key: Mapped[str | None] = mapped_column(String(64))
    #: Bumped by every review edit. Confirm names the version it reviewed, so
    #: a confirmation can never apply rows the student has not seen.
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    #: What the TRANSCRIPT said, kept apart from anything CoursePilot computes
    #: and never overwritten by it (see DATA_MODEL section 46, "GPA").
    transcript_reported_gpa: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    #: Document-level issue codes, e.g. ["NO_TERM_HEADINGS"].
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    #: Per term, what the document STATED (credits attempted, averages) and
    #: whether the rows read under it add up - review evidence, never an input.
    term_summaries: Mapped[list] = mapped_column(JSON, default=list)
    extracted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    discarded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rows_inserted: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    rows_replaced: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    rows_skipped: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    rows_excluded: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    rows: Mapped[list[TranscriptImportRow]] = relationship(
        back_populates="transcript_import", cascade="all, delete-orphan",
        order_by="TranscriptImportRow.row_index")

    __table_args__ = (
        CheckConstraint("state IN ('processing','extracted','failed','confirmed','discarded')",
                        name="state_known"),
        CheckConstraint("version >= 1", name="version_positive"),
        Index("uq_transcript_import_open_content", "student_id", "content_sha256",
              unique=True, postgresql_where=text(_OPEN), sqlite_where=text(_OPEN)),
    )


class TranscriptImportRow(Base, TimestampMixin):
    __tablename__ = "transcript_import_row"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    import_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("transcript_import.id", ondelete="CASCADE"), index=True)
    row_index: Mapped[int] = mapped_column(Integer)

    # --- provenance: where on the document, and exactly what it said ---
    page_number: Mapped[int] = mapped_column(Integer)
    line_number: Mapped[int] = mapped_column(Integer)
    section: Mapped[str] = mapped_column(String(16))         # term | transfer | exam | none
    raw_text: Mapped[str] = mapped_column(Text)
    extracted_course_code: Mapped[str | None] = mapped_column(String(16))
    extracted_title: Mapped[str | None] = mapped_column(String(255))
    extracted_term: Mapped[str | None] = mapped_column(String(32))
    extracted_credits: Mapped[str | None] = mapped_column(String(8))
    extracted_credit_prefix: Mapped[str | None] = mapped_column(String(1))
    extracted_grade: Mapped[str | None] = mapped_column(String(8))
    extracted_class_section: Mapped[str | None] = mapped_column(String(8))   # SEC column
    extracted_supplement: Mapped[str | None] = mapped_column(String(4))      # SUP column
    #: Parse-level problem codes for this line (e.g. MULTIPLE_COURSE_CODES).
    extraction_problems: Mapped[list] = mapped_column(JSON, default=list)

    # --- normalized values: what would be written; the student may correct ---
    course_code: Mapped[str | None] = mapped_column(String(16))
    supplement_code: Mapped[str | None] = mapped_column(String(4))
    course_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("course.id", ondelete="SET NULL"))
    term_code: Mapped[str | None] = mapped_column(String(16))
    status: Mapped[str | None] = mapped_column(String(16))
    grade: Mapped[str | None] = mapped_column(String(8))
    credits_earned: Mapped[Decimal | None] = mapped_column(Numeric(4, 1))
    credit_origin: Mapped[str | None] = mapped_column(String(16))

    # --- review ---
    state: Mapped[str] = mapped_column(String(16))
    issues: Mapped[list] = mapped_column(JSON, default=list)
    excluded: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    resolution: Mapped[str | None] = mapped_column(String(16))
    edited: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    #: The recorded entry this row collides with, when it does.
    existing_record_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("student_course.id", ondelete="SET NULL"))

    # --- confirmation ---
    outcome: Mapped[str | None] = mapped_column(String(16))
    student_course_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("student_course.id", ondelete="SET NULL"))

    transcript_import: Mapped[TranscriptImport] = relationship(back_populates="rows")

    __table_args__ = (
        UniqueConstraint("import_id", "row_index", name="uq_transcript_import_row_index"),
        CheckConstraint(
            "state IN ('matched','needs_review','unrecognized','conflict','invalid')",
            name="state_known"),
        CheckConstraint(
            "resolution IS NULL OR resolution IN ('keep_existing','replace_existing')",
            name="resolution_known"),
    )


__all__ = ["IMPORT_STATES", "RESOLUTIONS", "ROW_STATES", "ImportState", "Resolution",
           "RowOutcome", "RowState", "TranscriptImport", "TranscriptImportRow"]
