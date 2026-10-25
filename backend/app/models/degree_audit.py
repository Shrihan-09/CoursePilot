"""Degree Navigator audit import staging (Phase 7.4; DATA_MODEL section 51).

```
upload -> extract -> normalize -> validate -> STAGING -> reconciliation
       -> student review -> confirm
```

One uploaded DN audit print may hold several degree blocks; each block is its
own `degree_audit_import` (same content hash, its own `block_index`), imported
and confirmed separately.

  * `degree_audit_import`          - one degree block of one uploaded print;
  * `degree_audit_requirement_row` - one DN requirement heading or Core goal
    set: the ORIGINAL extracted values (never overwritten), the mapped
    CoursePilot node, the normalized DN status, review state and the
    reconciliation label against CoursePilot's own audit;
  * `degree_audit_course_row`      - one course line as DN printed it, in a
    requirement's list or a program course list.

Completed and in-progress courses are NOT written from here: they are staged
as rows of a linked `transcript_import` (source_document
'degree_navigator_audit') and go through the existing transcript review and
confirmation path. No file name, name or RUID is stored anywhere; the raw
document is deleted as soon as extraction ends (`storage_key` exists only
while it is being read).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

DN_IMPORT_STATES = ("processing", "extracted", "failed", "confirmed", "discarded")
_OPEN = "state IN ('processing','extracted')"


class DnRowState:
    MATCHED = "matched"              # usable as it stands
    NEEDS_REVIEW = "needs_review"    # the student must look (and resolve or exclude)
    UNMAPPED = "unmapped"            # no reviewed CoursePilot node: kept and shown, never used


DN_ROW_STATES = (DnRowState.MATCHED, DnRowState.NEEDS_REVIEW, DnRowState.UNMAPPED)
#: The status DN printed, normalized (the student may correct it in review).
DN_STATUSES = ("satisfied", "in_progress_or_planned", "not_satisfied", "unknown")
RECONCILIATIONS = ("agrees", "dn_satisfied_cp_not", "cp_satisfied_dn_not",
                   "allocation_differs", "unmapped", "unknown")


class DegreeAuditImport(Base, TimestampMixin):
    __tablename__ = "degree_audit_import"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    student_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("student.id", ondelete="CASCADE"), index=True)
    state: Mapped[str] = mapped_column(String(16))
    content_sha256: Mapped[str] = mapped_column(String(64))
    byte_size: Mapped[int] = mapped_column(Integer)
    page_count: Mapped[int | None] = mapped_column(Integer)
    block_index: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    block_count: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    extractor_version: Mapped[str | None] = mapped_column(String(32))
    failure_code: Mapped[str | None] = mapped_column(String(48))
    storage_key: Mapped[str | None] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    warnings: Mapped[list] = mapped_column(JSON, default=list)

    # --- as printed (never overwritten) ---
    dn_subject: Mapped[str | None] = mapped_column(String(255))
    #: The DN requirement version printed after the subject, e.g. "Summer 2019".
    dn_version_label: Mapped[str | None] = mapped_column(String(32))
    dn_degree_id: Mapped[str | None] = mapped_column(String(16))
    dn_report_date: Mapped[str | None] = mapped_column(String(32))
    #: Chrome's print timestamp from the page header ("10/30/26, 9:15 AM").
    printed_at_text: Mapped[str | None] = mapped_column(String(32))
    completed_requirements_text: Mapped[str | None] = mapped_column(String(32))

    # --- derived ---
    #: The audit's "as of": the print timestamp, else the upload time.
    as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    as_of_source: Mapped[str | None] = mapped_column(String(24))   # print_header | upload_time
    #: The mapping table that matched dn_subject (program key), and its version.
    mapping_key: Mapped[str | None] = mapped_column(String(64))
    mapping_version: Mapped[str | None] = mapped_column(String(16))
    #: matches_track | matches_school_core | mismatch | no_mapping
    track_check: Mapped[str | None] = mapped_column(String(24))
    track_message: Mapped[str | None] = mapped_column(Text)
    #: The linked course staging (existing transcript path).
    transcript_import_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("transcript_import.id", ondelete="SET NULL"))

    extracted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    discarded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: A newer confirmed import of the same mapping replaced this one.
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    requirement_rows: Mapped[list[DegreeAuditRequirementRow]] = relationship(
        back_populates="degree_audit_import", cascade="all, delete-orphan",
        order_by="DegreeAuditRequirementRow.row_index")
    course_rows: Mapped[list[DegreeAuditCourseRow]] = relationship(
        back_populates="degree_audit_import", cascade="all, delete-orphan",
        order_by="DegreeAuditCourseRow.row_index")

    __table_args__ = (
        CheckConstraint("state IN ('processing','extracted','failed','confirmed','discarded')",
                        name="state_known"),
        CheckConstraint("version >= 1", name="version_positive"),
        Index("uq_degree_audit_import_open_block", "student_id", "content_sha256",
              "block_index", unique=True, postgresql_where=text(_OPEN),
              sqlite_where=text(_OPEN)),
    )


class DegreeAuditRequirementRow(Base, TimestampMixin):
    __tablename__ = "degree_audit_requirement_row"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    import_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("degree_audit_import.id", ondelete="CASCADE"), index=True)
    row_index: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(16))                 # requirement | goal
    #: For a goal: the requirement row it was printed under.
    parent_row_index: Mapped[int | None] = mapped_column(Integer)

    # --- as printed ---
    page_number: Mapped[int] = mapped_column(Integer)
    line_number: Mapped[int] = mapped_column(Integer)
    dn_number: Mapped[int | None] = mapped_column(Integer)          # R<n>
    dn_heading: Mapped[str | None] = mapped_column(Text)            # "Requirement R1 : ... [CC]"
    dn_code: Mapped[str | None] = mapped_column(String(16))         # a goal's code
    extracted_title: Mapped[str | None] = mapped_column(Text)
    extracted_codes: Mapped[list] = mapped_column(JSON, default=list)
    #: fulfilled | not_fulfilled | in_progress_or_planned | unknown | missing |
    #: (goal) derived:<...> from its course icons
    extracted_icon: Mapped[str | None] = mapped_column(String(32))
    extracted_total: Mapped[int | None] = mapped_column(Integer)
    extracted_completed: Mapped[int | None] = mapped_column(Integer)
    extracted_needs: Mapped[str | None] = mapped_column(Text)
    #: [{course, term_code, kind, icon}] - what DN allocated / listed here.
    extracted_courses: Mapped[list] = mapped_column(JSON, default=list)
    extraction_problems: Mapped[list] = mapped_column(JSON, default=list)

    # --- normalized ---
    requirement_code: Mapped[str | None] = mapped_column(String(64))
    mapping_review_status: Mapped[str | None] = mapped_column(String(24))
    dn_status: Mapped[str] = mapped_column(String(24))

    # --- review ---
    state: Mapped[str] = mapped_column(String(16))
    issues: Mapped[list] = mapped_column(JSON, default=list)
    excluded: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    edited: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    # --- reconciliation with CoursePilot's own audit ---
    reconciliation: Mapped[str | None] = mapped_column(String(24))
    cp_status: Mapped[str | None] = mapped_column(String(32))
    cp_courses: Mapped[list] = mapped_column(JSON, default=list)

    degree_audit_import: Mapped[DegreeAuditImport] = relationship(
        back_populates="requirement_rows")

    __table_args__ = (
        UniqueConstraint("import_id", "row_index", name="uq_dn_requirement_row_index"),
        CheckConstraint("kind IN ('requirement','goal')", name="kind_known"),
        CheckConstraint("state IN ('matched','needs_review','unmapped')", name="state_known"),
        CheckConstraint("dn_status IN ('satisfied','in_progress_or_planned','not_satisfied',"
                        "'unknown')", name="dn_status_known"),
    )


class DegreeAuditCourseRow(Base, TimestampMixin):
    __tablename__ = "degree_audit_course_row"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    import_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("degree_audit_import.id", ondelete="CASCADE"), index=True)
    row_index: Mapped[int] = mapped_column(Integer)
    #: requirement | used | unused | below_minimum_grade
    list_kind: Mapped[str] = mapped_column(String(24))
    requirement_number: Mapped[int | None] = mapped_column(Integer)

    # --- as printed ---
    page_number: Mapped[int] = mapped_column(Integer)
    line_number: Mapped[int] = mapped_column(Integer)
    raw_text: Mapped[str] = mapped_column(Text)
    extracted_course_code: Mapped[str | None] = mapped_column(String(16))
    extracted_credits: Mapped[str | None] = mapped_column(String(8))
    extracted_term: Mapped[str | None] = mapped_column(String(32))
    extracted_grade: Mapped[str | None] = mapped_column(String(16))
    #: course | current | planned | placement | unclassified
    kind: Mapped[str] = mapped_column(String(16))
    extraction_problems: Mapped[list] = mapped_column(JSON, default=list)

    # --- review ---
    #: matched | needs_review (unclassified / unreadable rows) | info (placement, planned)
    state: Mapped[str] = mapped_column(String(16))
    issues: Mapped[list] = mapped_column(JSON, default=list)
    excluded: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    #: The staged history row this line feeds (one per course and term).
    transcript_row_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("transcript_import_row.id", ondelete="SET NULL"))

    degree_audit_import: Mapped[DegreeAuditImport] = relationship(back_populates="course_rows")

    __table_args__ = (
        UniqueConstraint("import_id", "row_index", name="uq_dn_course_row_index"),
        CheckConstraint("state IN ('matched','needs_review','info')", name="state_known"),
    )


__all__ = ["DN_IMPORT_STATES", "DN_ROW_STATES", "DN_STATUSES", "RECONCILIATIONS",
           "DegreeAuditCourseRow", "DegreeAuditImport", "DegreeAuditRequirementRow",
           "DnRowState"]
