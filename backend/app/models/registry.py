"""Program discovery registry and review evidence (Phase 6.3).

```
catalog_page ─< program_candidate ──> program_version   (when curated)
    │                                      │
    └── data_source (latest snapshot)      └─< program_review   (append-only human evidence)
```

## A page is not a program

A Rutgers catalog page is a SOURCE: "Mathematics 640" describes a major with
three options, a minor, a certificate and two interdisciplinary majors. So:

  * `catalog_page` - one row per navigation page per catalog year, from the
    navigation tree Coursedog embeds in every page (Phase 6.1).
  * `program_candidate` - zero, one or many per page: a credential the page
    appears to define, extracted from its headings. A candidate is a CLAIM
    to be checked, never a supported program.
  * `program_version` - the curated, evaluable definition, linked from a
    candidate only once someone has encoded it.

Discovery never creates a Program, and nothing here makes anything
student-facing: that takes a curated definition, validation, a human review
and an explicit publish (app.services.program_lifecycle).

## Page identity is not requirement identity

Coursedog `pageId` is stable across catalog years (430 of 433 pages in
Phase 6.1). A stable page id says the PAGE persisted; it says nothing about
whether its requirements changed. Pages are keyed per catalog year, and a
program version belongs to exactly one catalog year.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

PAGE_CLASSES = ("subject_coded", "program_area", "other", "external")
CREDENTIAL_TYPES = ("major", "option", "minor", "certificate", "interdisciplinary_major",
                    "unknown")


class CatalogPage(Base, TimestampMixin):
    __tablename__ = "catalog_page"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    #: Which Rutgers catalog, e.g. "nb-undergrad". Camden, Newark, graduate
    #: catalogs are separate Coursedog sites.
    catalog_key: Mapped[str] = mapped_column(String(32))
    catalog_year: Mapped[str] = mapped_column(String(16))
    url_path: Mapped[str] = mapped_column(Text)
    source_page_id: Mapped[str | None] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(Text)
    school_slug: Mapped[str] = mapped_column(String(64))
    #: Navigation ancestry below the school, " / "-joined.
    trail: Mapped[str] = mapped_column(Text, default="")
    page_class: Mapped[str] = mapped_column(String(24))
    subject_code: Mapped[str | None] = mapped_column(String(8))

    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    #: Latest archived snapshot of the page, when fetched.
    snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("data_source.id", ondelete="SET NULL"))
    #: sha256 of the page's normalized prose - its CONTENT identity, which
    #: ignores Coursedog build hashes and markup churn. See catalog_registry.
    prose_sha256: Mapped[str | None] = mapped_column(String(64))
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("catalog_key", "catalog_year", "url_path", name="uq_catalog_page"),
        CheckConstraint("page_class IN ('" + "','".join(PAGE_CLASSES) + "')",
                        name="page_class_known"),
        Index("ix_catalog_page_year_school", "catalog_year", "school_slug"),
    )

    @property
    def lifecycle(self) -> str:
        return "fetched" if self.snapshot_id else "discovered"


class ProgramCandidate(Base, TimestampMixin):
    __tablename__ = "program_candidate"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    catalog_page_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("catalog_page.id", ondelete="CASCADE"), index=True)
    #: Stable within its page: a slug of the heading that defines it.
    candidate_key: Mapped[str] = mapped_column(String(160))
    heading: Mapped[str] = mapped_column(Text)
    credential_type: Mapped[str] = mapped_column(String(32))
    #: For an option: the candidate key of the major it belongs to.
    parent_candidate_key: Mapped[str | None] = mapped_column(String(160))
    curriculum_code: Mapped[str | None] = mapped_column(String(16))
    extraction_method: Mapped[str] = mapped_column(String(64))
    #: Set only when a curated definition exists for this candidate.
    program_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("program_version.id", ondelete="SET NULL"))

    __table_args__ = (
        UniqueConstraint("catalog_page_id", "candidate_key", name="uq_program_candidate"),
        CheckConstraint("credential_type IN ('" + "','".join(CREDENTIAL_TYPES) + "')",
                        name="credential_type_known"),
    )


class ProgramReview(Base, TimestampMixin):
    """One human review of one program version against one source snapshot.

    Append-only: a later review adds a row, it never edits an earlier one, so
    "who approved what, against which text" survives re-review.
    """

    __tablename__ = "program_review"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    program_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("program_version.id", ondelete="CASCADE"), index=True)
    #: A named human. Validated by program_lifecycle: never an AI or system label.
    reviewer: Mapped[str] = mapped_column(String(128))
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decision: Mapped[str] = mapped_column(String(24))
    #: What exactly was reviewed: the source content and the definition bytes.
    source_prose_sha256: Mapped[str] = mapped_column(String(64))
    definition_sha256: Mapped[str] = mapped_column(String(64))
    notes: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint("decision IN ('approved','changes_requested')",
                        name="review_decision_known"),
    )


__all__ = ["CREDENTIAL_TYPES", "PAGE_CLASSES", "CatalogPage", "ProgramCandidate",
           "ProgramReview"]
