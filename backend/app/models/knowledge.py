"""The course knowledge index (Prompt 08 B1, docs/investigations/phase-08a §index).

A PRECOMPUTED, VERSIONED answer to "what could this course fill?" for every
course offered in every loaded term:

    knowledge_build       one immutable build: its input manifest hash, state
                          (building | ready | failed), counts. Readers use the
                          newest READY build whose manifest still matches.
    knowledge_membership  course x term x OWNER x requirement node (+category),
                          with provenance (how: curated list | query | term
                          certification) and labels (lifecycle, conflicts,
                          stage). OWNER = the program version or the component
                          that authored the node, so a shared component's
                          membership is stored ONCE, not per program.
    knowledge_context     which owners compose each program version in the
                          build (its own nodes + school gen-ed + linked
                          components) - the join from owners to programs.
    knowledge_coverage    per program version x term: offered courses, mapped,
                          excluded, unknown, and whether the context is
                          COMPLETE enough that "no membership" may be read as
                          "free elective only". Never inferred from zero rows.

No student data is ever written here. Whether a course is still NEEDED by one
student is computed at query time on top of this (app.services.knowledge).
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
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class KnowledgeBuild(Base, TimestampMixin):
    __tablename__ = "knowledge_build"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    #: sha256 of the input manifest (definitions, lifecycle, eligibility,
    #: offerings, certifications, builder version). Identical inputs -> identical hash.
    manifest_sha256: Mapped[str] = mapped_column(String(64), index=True)
    manifest: Mapped[dict] = mapped_column(JSON)
    builder_version: Mapped[str] = mapped_column(String(32))
    state: Mapped[str] = mapped_column(String(16), default="building")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    counts: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))
    error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint("state IN ('building','ready','failed')",
                        name="knowledge_build_state_known"),
    )


class KnowledgeMembership(Base):
    __tablename__ = "knowledge_membership"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    build_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_build.id", ondelete="CASCADE"))
    term_code: Mapped[str] = mapped_column(String(16))
    course_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("course.id", ondelete="CASCADE"))
    course_string: Mapped[str] = mapped_column(String(32))
    #: "program_version" | "component"
    owner_kind: Mapped[str] = mapped_column(String(24))
    owner_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    owner_key: Mapped[str] = mapped_column(String(96))           # e.g. "sas-198-bs", "sebs-core"
    requirement_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("requirement.id", ondelete="CASCADE"))
    requirement_code: Mapped[str] = mapped_column(String(64))
    requirement_name: Mapped[str] = mapped_column(Text)
    requirement_system: Mapped[str] = mapped_column(String(32))
    category: Mapped[str] = mapped_column(String(16), default="")
    #: how the membership is known: curated_list | curated_query | term_certification
    basis: Mapped[str] = mapped_column(String(24))
    #: provenance: the encoding's data_source id and the certifying SOC term
    source_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    certified_term: Mapped[str | None] = mapped_column(String(16))
    #: labels the reader must show, never drop
    lifecycle_state: Mapped[str] = mapped_column(String(24))
    conflicts: Mapped[list | None] = mapped_column(JSON(none_as_null=True))
    available_after_stage: Mapped[str | None] = mapped_column(String(48))
    min_grade: Mapped[str | None] = mapped_column(String(4))

    __table_args__ = (
        UniqueConstraint("build_id", "term_code", "course_id", "requirement_id", "category",
                         name="uq_knowledge_membership"),
        Index("ix_knowledge_membership_lookup", "build_id", "term_code", "course_id"),
        Index("ix_knowledge_membership_owner", "build_id", "owner_id", "term_code"),
        CheckConstraint("owner_kind IN ('program_version','component')",
                        name="knowledge_owner_kind_known"),
        CheckConstraint("basis IN ('curated_list','curated_query','term_certification')",
                        name="knowledge_basis_known"),
    )


class KnowledgeContext(Base):
    __tablename__ = "knowledge_context"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    build_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_build.id", ondelete="CASCADE"), index=True)
    program_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("program_version.id", ondelete="CASCADE"))
    program_key: Mapped[str] = mapped_column(String(96))
    owner_kind: Mapped[str] = mapped_column(String(24))
    owner_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    owner_key: Mapped[str] = mapped_column(String(96))
    #: why this owner composes the program: own | school_gen_ed | linked
    via: Mapped[str] = mapped_column(String(24))

    __table_args__ = (
        UniqueConstraint("build_id", "program_version_id", "owner_id", name="uq_knowledge_context"),
    )


class KnowledgeCoverage(Base):
    __tablename__ = "knowledge_coverage"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    build_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_build.id", ondelete="CASCADE"), index=True)
    program_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("program_version.id", ondelete="CASCADE"))
    program_key: Mapped[str] = mapped_column(String(96))
    term_code: Mapped[str] = mapped_column(String(16))
    offered_courses: Mapped[int] = mapped_column(Integer)
    mapped_courses: Mapped[int] = mapped_column(Integer)
    excluded_courses: Mapped[int] = mapped_column(Integer)
    #: Only when TRUE may an unmapped, non-excluded course be called
    #: "free elective only"; otherwise it is "unknown".
    complete: Mapped[bool] = mapped_column(Boolean)
    #: why not complete, in plain words (unreviewed components, open conflicts,
    #: no certifications published for the term, unknown course lists ...)
    incomplete_reasons: Mapped[list] = mapped_column(JSON)

    __table_args__ = (
        UniqueConstraint("build_id", "program_version_id", "term_code",
                         name="uq_knowledge_coverage"),
    )


class RequirementReport(Base, TimestampMixin):
    """A student's "this requirement looks wrong" report (Prompt 08 B1 item 5).

    Stores the program, the node and the message - nothing about the student
    beyond the reporting ACCOUNT id (for abuse control and follow-up); no
    transcript, plan or grade is attached.
    """
    __tablename__ = "requirement_report"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_account.id", ondelete="CASCADE"), index=True)
    program_key: Mapped[str] = mapped_column(String(96))
    catalog_year: Mapped[str] = mapped_column(String(16))
    requirement_code: Mapped[str] = mapped_column(String(64))
    requirement_name: Mapped[str] = mapped_column(Text)
    message: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="open", server_default="open")
    resolved_note: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint("status IN ('open','resolved','dismissed')",
                        name="requirement_report_status"),
        CheckConstraint("length(message) BETWEEN 1 AND 2000",
                        name="requirement_report_message_length"),
        Index("ix_requirement_report_status_created", "status", "created_at"),
    )


__all__ = ["KnowledgeBuild", "KnowledgeContext", "KnowledgeCoverage", "KnowledgeMembership",
           "RequirementReport"]
