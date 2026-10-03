"""Term-scoped course prerequisites (Phase 6.2).

```
course ─< course_offering (course × term × campus) ─ course_prerequisite ─< prerequisite_reference
                                                         │                      │
                                                    data_source            course_string (always)
                                                    (SOC, term,            course_id     (when known)
                                                     archive, hash)
```

## Why the prerequisite hangs off the OFFERING

SOC publishes `preReqNotes` per course PER TERM. Rutgers can change a
prerequisite between terms, and a planner must ask "what applied in THIS
term?". `course_offering` is already the course × term × campus fact, so the
prerequisite attaches there - one row per offering - and a later term can
never overwrite an earlier one. `course.prereq_notes_raw` remains, but it is
a single global column holding whichever term was loaded most recently, and
it is tag-stripped (see below); nothing new reads it.

## What Rutgers published vs what CoursePilot understood

  * `raw_text` - the exact SOC string, markup included. The <em>-wrapped
    operators are the ONLY reliable operator signal (titles contain AND/OR),
    which is why the tag-stripped `course.prereq_notes_raw` cannot be parsed.
  * `classification`, `expression`, `canonical_text`, `parser_version` -
    CoursePilot's interpretation, reproducible from `raw_text` at any time.
  * `condition_note` - a SOC `courseNotes` text that states a prerequisite
    condition (minimum grade, placement, permission, ...). Never interpreted;
    it caps any evaluation at UNKNOWN.

## Referenced course identities

`prerequisite_reference` keeps EVERY course a prerequisite names, as its
course string, whether or not CoursePilot has a `course` row for it. The same
pattern as `catalog_course_entry`: the identity is always stored, the FK is
filled only when the course is known. No title, credits or offering is ever
invented for an unresolved reference.
"""

from __future__ import annotations

import uuid

from sqlalchemy import JSON, CheckConstraint, ForeignKey, Index, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

PREREQUISITE_CLASSIFICATIONS = (
    "parsed",
    "unsupported_minimum_course_level",
    "unsupported_ambiguous_precedence",
    "malformed",
    "unknown",
    "condition_note_only",
)


class CoursePrerequisite(Base, TimestampMixin):
    __tablename__ = "course_prerequisite"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    offering_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("course_offering.id", ondelete="CASCADE"), unique=True
    )
    # Denormalized from the offering so the planner's question - course X,
    # term T - is one indexed lookup.
    course_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("course.id", ondelete="CASCADE"))
    term_code: Mapped[str] = mapped_column(String(16))

    # --- what Rutgers published ---
    raw_text: Mapped[str | None] = mapped_column(Text)
    raw_text_sha256: Mapped[str | None] = mapped_column(String(64))
    condition_note: Mapped[str | None] = mapped_column(Text)
    condition_kinds: Mapped[str | None] = mapped_column(String(128))

    # --- how CoursePilot interpreted it ---
    classification: Mapped[str] = mapped_column(String(40))
    expression: Mapped[dict | None] = mapped_column(JSON)
    canonical_text: Mapped[str | None] = mapped_column(Text)
    parse_detail: Mapped[str | None] = mapped_column(Text)
    parser_version: Mapped[str] = mapped_column(String(16))

    # --- Phase 6.4: conditions CoursePilot can now interpret ----------------
    # A SOC sectionNotes text that states a prerequisite condition and is
    # published IDENTICALLY on every section of the offering - only then is it
    # a course-level fact rather than a section-level one.
    section_condition_note: Mapped[str | None] = mapped_column(Text)
    # The deterministic reading of the condition texts (app.domain.conditions):
    # {"minimum_grade": {"grade": "C", "scope": "all"|"named"|"unspecified",
    #  "courses": [...]}, "alternatives": ["placement", ...],
    #  "uninterpreted": ["permission", ...]}. NULL when there is no condition.
    interpreted_conditions: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))
    condition_parser_version: Mapped[str | None] = mapped_column(String(16))

    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("data_source.id"))

    references: Mapped[list[PrerequisiteReference]] = relationship(
        back_populates="prerequisite", cascade="all, delete-orphan",
        order_by="PrerequisiteReference.course_string",
    )

    __table_args__ = (
        Index("ix_course_prerequisite_course_term", "course_id", "term_code"),
        CheckConstraint(
            "classification IN ('" + "','".join(PREREQUISITE_CLASSIFICATIONS) + "')",
            name="classification_known",
        ),
        # Something was published: an expression, a condition, or both.
        CheckConstraint(
            "raw_text IS NOT NULL OR condition_note IS NOT NULL "
            "OR section_condition_note IS NOT NULL",
            name="has_source_text",
        ),
    )

    def __repr__(self) -> str:
        return f"<CoursePrerequisite {self.term_code} {self.classification}>"


class PrerequisiteReference(Base, TimestampMixin):
    """One course identity named by one prerequisite."""

    __tablename__ = "prerequisite_reference"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    prerequisite_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("course_prerequisite.id", ondelete="CASCADE")
    )
    # Always present: the Rutgers identity exactly as published.
    course_string: Mapped[str] = mapped_column(String(32), index=True)
    # Present only when CoursePilot has loaded that course. NULL is a real,
    # expected state - never a reason to drop the edge.
    course_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("course.id", ondelete="SET NULL"), index=True
    )

    prerequisite: Mapped[CoursePrerequisite] = relationship(back_populates="references")

    __table_args__ = (
        UniqueConstraint("prerequisite_id", "course_string", name="uq_prerequisite_reference"),
    )


COREQUISITE_CLASSIFICATIONS = ("parsed", "unsupported")


class CourseCorequisite(Base, TimestampMixin):
    """A term-scoped co-requisite (Phase 6.4): COURSE-TAKING eligibility.

    "PRE OR COREQ: 01:146:356" - the course may be taken once 01:146:356 is
    passed OR in the SAME term. Co-requisites are not degree requirements;
    they live beside prerequisites, one row per offering, and are evaluated
    against completed courses PLUS a proposed term's courses
    (app.services.course_eligibility). Nothing here plans a term.

    Sources: SOC `courseNotes`, or a `sectionNotes` text that every section of
    the offering yields the same rule from. `raw_text` keeps every source text
    exactly as published.
    """

    __tablename__ = "course_corequisite"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    offering_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("course_offering.id", ondelete="CASCADE"), unique=True
    )
    course_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("course.id", ondelete="CASCADE"))
    term_code: Mapped[str] = mapped_column(String(16))

    # --- what Rutgers published ---
    raw_text: Mapped[str] = mapped_column(Text)
    source_field: Mapped[str] = mapped_column(String(24))      # courseNotes | sectionNotes:all
    # --- how CoursePilot interpreted it ---
    classification: Mapped[str] = mapped_column(String(24))
    expression: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))
    canonical_text: Mapped[str | None] = mapped_column(Text)
    parse_detail: Mapped[str | None] = mapped_column(Text)
    parser_version: Mapped[str] = mapped_column(String(16))

    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("data_source.id"))

    __table_args__ = (
        Index("ix_course_corequisite_course_term", "course_id", "term_code"),
        CheckConstraint(
            "classification IN ('" + "','".join(COREQUISITE_CLASSIFICATIONS) + "')",
            name="corequisite_classification_known",
        ),
    )


__all__ = ["COREQUISITE_CLASSIFICATIONS", "PREREQUISITE_CLASSIFICATIONS", "CourseCorequisite",
           "CoursePrerequisite", "PrerequisiteReference"]
