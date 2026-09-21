"""Academic entities: Subject, Course, CourseOffering.

Every design choice here is driven by observed SOC data, not assumption.
See docs/DATA_SOURCES.md for the measurements behind each one.

Deliberately NOT modeled yet: sections, meeting times, instructors,
prerequisites, equivalencies, requirements. FKs are shaped so they can be
added without restructuring what exists.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class Subject(Base, TimestampMixin):
    """A Rutgers subject, e.g. `013` = African, Middle Eastern and South Asian
    Languages and Literatures.

    Normalized out of the course table: the SOC payload repeats
    `subjectDescription` on every course row, so storing it per course would
    duplicate the same string thousands of times and make a rename require
    rewriting every affected course.
    """

    __tablename__ = "subject"

    # sqlalchemy.Uuid (not the postgresql-specific type) so the same models can
    # run against SQLite in tests. Renders as native UUID on Postgres.
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    code: Mapped[str] = mapped_column(String(8), index=True)
    description: Mapped[str | None] = mapped_column(Text)

    # A subject code is scoped to its offering unit: the same numeric code
    # under a different unit is a different subject.
    offering_unit_code: Mapped[str] = mapped_column(String(8))

    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("data_source.id"))

    courses: Mapped[list[Course]] = relationship(back_populates="subject")

    __table_args__ = (UniqueConstraint("offering_unit_code", "code", name="uq_subject_unit_code"),)

    def __repr__(self) -> str:
        return f"<Subject {self.offering_unit_code}:{self.code}>"


class Course(Base, TimestampMixin):
    """A catalog course, independent of term and campus.

    Natural key: (offering_unit_code, subject_code, course_number, supplement_code)

    Derived from measurement, not intuition. `courseString` alone is NOT
    unique in the SOC payload: 4,389 distinct values across 4,400 records.
    The 11 collisions have two causes.

      * `supplementCode` differs (e.g. 01:750:193 lecture '  ' vs lab 'LB').
        These are genuinely different courses with different credits and
        titles, so supplement_code IS part of course identity.

      * `campusCode` differs (e.g. 16:400:513 at NB and OB). This is the SAME
        course offered at two campuses; credits and title are identical and
        only section data differs. So campus is NOT part of course identity;
        it belongs to CourseOffering.

    Getting this wrong in either direction is silently destructive: including
    campus would create duplicate course rows, and excluding supplement would
    collapse a 4-credit lecture and its 0-credit lab into one record.
    """

    __tablename__ = "course"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    # --- identity ---
    offering_unit_code: Mapped[str] = mapped_column(String(8))
    subject_code: Mapped[str] = mapped_column(String(8))
    course_number: Mapped[str] = mapped_column(String(16))
    # Observed as two spaces ('  ') when absent. Normalized to '' on the way
    # in. NULL would break the unique constraint, since in SQL NULL != NULL
    # and Postgres would then permit unlimited duplicate rows.
    supplement_code: Mapped[str] = mapped_column(String(8), default="")

    # Denormalized display form, e.g. "01:013:120". Convenient for lookup and
    # display; NOT the key, for the reasons above.
    course_string: Mapped[str] = mapped_column(String(32), index=True)

    # --- content ---
    # SOC `title` is abbreviated to ~20 chars ("COMICS MIDEAST"); the readable
    # form is in `expandedTitle`, present on only 60.8% of records. We keep
    # both: `title` is the best available, `title_abbrev` is what SOC sent.
    title: Mapped[str] = mapped_column(Text)
    title_abbrev: Mapped[str | None] = mapped_column(Text)

    # SOC returns this empty for 100% of courses (measured, n=4,400). Kept so
    # a catalog source can populate it later without a migration.
    description: Mapped[str | None] = mapped_column(Text)

    # Numeric, NOT Integer, and nullable. Measured: 11.1% null, and 91 courses
    # carry fractional credits (0.5, 1.5, 2.5, 4.5). An Integer column would
    # reject or silently truncate them.
    credits: Mapped[Decimal | None] = mapped_column(Numeric(4, 1))
    credits_description: Mapped[str | None] = mapped_column(Text)

    level: Mapped[str | None] = mapped_column(String(4), index=True)  # observed: 'U', 'G'

    school_code: Mapped[str | None] = mapped_column(String(8))
    school_description: Mapped[str | None] = mapped_column(Text)

    # Prerequisite prose, stored verbatim. Parsing is deliberately deferred:
    # the text is HTML-laced boolean prose, and a wrong parse would feed the
    # validator false confidence. Raw text is honest; a bad parse is not.
    prereq_notes_raw: Mapped[str | None] = mapped_column(Text)

    synopsis_url: Mapped[str | None] = mapped_column(Text)

    subject_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("subject.id"), index=True)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("data_source.id"))

    subject: Mapped[Subject] = relationship(back_populates="courses")
    offerings: Mapped[list[CourseOffering]] = relationship(
        back_populates="course", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint(
            "offering_unit_code",
            "subject_code",
            "course_number",
            "supplement_code",
            name="uq_course_natural_key",
        ),
        # Credits are never negative. A cheap invariant that catches parser
        # regressions at the database boundary rather than in a UI.
        CheckConstraint("credits IS NULL OR credits >= 0", name="credits_non_negative"),
        Index("ix_course_subject_number", "subject_code", "course_number"),
    )

    def __repr__(self) -> str:
        suffix = f"/{self.supplement_code}" if self.supplement_code else ""
        return f"<Course {self.course_string}{suffix}>"


class CourseOffering(Base, TimestampMixin):
    """A course made available in a specific term at a specific campus.

    This table exists because of the measured NB/OB duplication: the same
    course appears twice in one payload, differing only by campus. Modeling
    campus here keeps the course table free of duplicates while preserving
    the fact that the course runs in two places.

    Sections will hang off this table in a later phase.
    """

    __tablename__ = "course_offering"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    course_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("course.id", ondelete="CASCADE"), index=True
    )

    # Format "<year><term>", e.g. "20269" = Fall 2026. This matches the
    # `effective` field SOC itself emits inside coreCodes, so we adopt the
    # source's own convention rather than inventing a parallel one.
    term_code: Mapped[str] = mapped_column(String(16), index=True)
    campus_code: Mapped[str] = mapped_column(String(16))

    # Point-in-time observations from the payload, not live state. Named to
    # make that obvious at the call site.
    open_sections_observed: Mapped[int | None] = mapped_column(Integer)
    section_count_observed: Mapped[int | None] = mapped_column(Integer)

    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("data_source.id"))

    course: Mapped[Course] = relationship(back_populates="offerings")
    # Sections hang off the offering, not the course: a section only exists
    # within a specific term at a specific campus. See app/models/sections.py.
    sections: Mapped[list["CourseSection"]] = relationship(  # noqa: F821
        back_populates="offering", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint(
            "course_id", "term_code", "campus_code", name="uq_offering_course_term_campus"
        ),
    )

    def __repr__(self) -> str:
        return f"<CourseOffering {self.term_code} {self.campus_code}>"
