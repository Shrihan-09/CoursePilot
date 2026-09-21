"""Section entities: CourseSection, SectionMeeting, SectionInstructor,
SectionCrossListing.

Every decision here is driven by measurements against the real SOC payload
(4,400 courses / 11,992 sections / 17,457 meeting rows). See
docs/DATA_SOURCES.md for the numbers behind each one.

A section is NOT attached to a Course directly. It hangs off CourseOffering
(course + term + campus), because a section only exists within a specific term
at a specific campus. Verified: 0 of 11,992 sections have a campusCode that
differs from their parent course's campusCode, and the NB/OB duplicate pair
(16:400:513) carries distinct section indexes per campus (19370 / 19371).

Deliberately NOT modeled, and why:

  * enrollment capacity, current enrollment, waitlist - the SOC courses.json
    endpoint provides NONE of these. There is no key containing "capacity",
    "enroll", "seat", "wait", "avail", "max", or "limit" anywhere in the
    section objects. Only a boolean openStatus. Inventing columns for data the
    source does not supply would invite fabricated values later.
  * sessionDates, subtopic, legendKey - empty on 100% of 11,992 sections.
  * printed - the constant 'Y' on 100% of sections; carries no information.
  * majors / minors / unitMajors / honorPrograms - real one-to-many
    restriction lists (21.9% / 2.1% / 8.9% / 1.2%). Deferred to a later phase;
    the human-readable forms are preserved in open_to_text and
    section_eligibility so nothing is lost.
"""

from __future__ import annotations

import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

# Observed meetingDay values across 11,025 timed meetings:
# M, T, W, H (Thursday), F, S (Saturday), U (Sunday). Single character.
VALID_MEETING_DAYS = ("M", "T", "W", "H", "F", "S", "U")


class CourseSection(Base, TimestampMixin):
    """One registerable section of a course in a given term and campus.

    Natural key: (term_code, index_number)

    `index_number` is the Rutgers registration index - the 5-digit number a
    student actually types into WebReg. Measured: 11,992 distinct values
    across 11,992 sections, i.e. globally unique within a term payload, and
    numeric in 100% of cases.

    term_code is part of the key rather than relying on index alone, because
    only ONE term has been observed. Rutgers is widely understood to reuse
    index numbers across terms, and this project has not verified otherwise.
    Keying on index alone would collide the first time a second term is
    ingested - a failure that would corrupt data rather than raise an error.
    Including term_code costs nothing and fails safe.

    A second UNIQUE constraint on (offering_id, section_number) is also
    measured-true (course + campus + number had 0 collisions) and is what
    catches a section renumbering that reuses an index.
    """

    __tablename__ = "course_section"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    offering_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("course_offering.id", ondelete="CASCADE"), index=True
    )

    # Denormalized from course_offering so the natural key can be enforced by
    # a single-table UNIQUE constraint. A cross-table uniqueness rule would
    # otherwise need a trigger, which is far heavier for the same guarantee.
    term_code: Mapped[str] = mapped_column(String(16), index=True)

    index_number: Mapped[str] = mapped_column(String(16), index=True)
    # 2 characters in 100% of observed sections, but NOT always numeric:
    # 344 distinct non-numeric values such as '1R', '9A', 'A1'. A numeric
    # column here would reject real Rutgers sections.
    section_number: Mapped[str] = mapped_column(String(16))

    campus_code: Mapped[str] = mapped_column(String(16))

    # Present on 100% of sections. This is a point-in-time observation of
    # registration state, not a durable fact - the name says so at the call
    # site. SOC gives no seat counts to go with it.
    open_status: Mapped[bool] = mapped_column(Boolean)
    open_status_text: Mapped[str | None] = mapped_column(String(32))

    # 'T' (9,949), 'H' (605), 'O' (1,438). Meaning is not documented by
    # Rutgers, so the raw code is stored rather than an interpretation.
    section_course_type: Mapped[str | None] = mapped_column(String(8))
    exam_code: Mapped[str | None] = mapped_column(String(8))
    exam_code_text: Mapped[str | None] = mapped_column(Text)
    final_exam: Mapped[str | None] = mapped_column(Text)

    subtitle: Mapped[str | None] = mapped_column(Text)
    section_notes: Mapped[str | None] = mapped_column(Text)
    comments_text: Mapped[str | None] = mapped_column(Text)
    open_to_text: Mapped[str | None] = mapped_column(Text)
    section_eligibility: Mapped[str | None] = mapped_column(Text)

    special_permission_add_code: Mapped[str | None] = mapped_column(String(8))
    special_permission_add_description: Mapped[str | None] = mapped_column(Text)
    special_permission_drop_code: Mapped[str | None] = mapped_column(String(8))
    special_permission_drop_description: Mapped[str | None] = mapped_column(Text)

    cross_listed_section_type: Mapped[str | None] = mapped_column(String(8))

    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("data_source.id"))

    offering: Mapped["CourseOffering"] = relationship(back_populates="sections")  # noqa: F821
    meetings: Mapped[list[SectionMeeting]] = relationship(
        back_populates="section", cascade="all, delete-orphan", order_by="SectionMeeting.meeting_index"
    )
    instructors: Mapped[list[SectionInstructor]] = relationship(
        back_populates="section",
        cascade="all, delete-orphan",
        order_by="SectionInstructor.instructor_index",
    )
    cross_listings: Mapped[list[SectionCrossListing]] = relationship(
        back_populates="section", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("term_code", "index_number", name="uq_section_term_index"),
        UniqueConstraint("offering_id", "section_number", name="uq_section_offering_number"),
        # Measured: numeric in 11,992 of 11,992. Enforced because a
        # non-numeric index would mean the source shape changed materially.
        #
        # `ddl_if` because `~` is PostgreSQL's regex operator and SQLite
        # cannot parse it. PostgreSQL is the real target; SQLite is only used
        # to build a throwaway schema for the fast offline unit tests. This
        # keeps ONE definition and full strength where it matters, rather
        # than deleting the constraint to make tests run.
        CheckConstraint("index_number ~ '^[0-9]+$'", name="index_number_numeric").ddl_if(
            dialect="postgresql"
        ),
        Index("ix_section_offering_open", "offering_id", "open_status"),
    )

    def __repr__(self) -> str:
        return f"<CourseSection {self.index_number} sec={self.section_number} {self.term_code}>"


class SectionMeeting(Base, TimestampMixin):
    """One meeting pattern of a section.

    Modeled as its own table because the source is genuinely one-to-many:
    7,965 sections have 1 pattern, 2,661 have 2, 1,302 have 3, 56 have 4, and
    8 have 5 - 17,457 rows in total. Columns like meeting_day_1 / meeting_day_2
    would have overflowed at 3 and lost data at 6.

    Natural key: (section_id, meeting_index)

    Ordinal position, NOT the attribute tuple. Two reasons, both measured:

      * 36.8% of meetings have no day and no time (TBA / asynchronous). Those
        columns are therefore NULL, and in SQL NULL != NULL - a UNIQUE
        constraint containing them would silently permit unlimited duplicates.
        This is the same trap that supplement_code hit in Phase 1.
      * An ordinal is always present and always comparable.

    The tradeoff: if Rutgers reorders a section's meetings between runs, the
    rows are rewritten in place rather than duplicated. Row counts stay
    correct either way, which is what idempotency requires here.
    """

    __tablename__ = "section_meeting"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    section_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("course_section.id", ondelete="CASCADE"), index=True
    )
    # 0-based position within the section's meetingTimes array.
    meeting_index: Mapped[int] = mapped_column(Integer)

    # NULL means TBA. 6,432 of 17,457 meeting rows (36.8%) carry no day and no
    # time at all - these are research, independent-study, and asynchronous
    # online meetings. Measured: day and time are all-or-nothing (0 rows have
    # a day without a time, or a start without an end), which the CHECK
    # constraints below enforce.
    meeting_day: Mapped[str | None] = mapped_column(String(1))
    start_time_military: Mapped[str | None] = mapped_column(String(4))
    end_time_military: Mapped[str | None] = mapped_column(String(4))

    # Present on 100% of meeting rows. '90' is ONLINE INSTRUCTION(INTERNET);
    # 27 distinct modes observed.
    meeting_mode_code: Mapped[str] = mapped_column(String(8))
    meeting_mode_desc: Mapped[str | None] = mapped_column(String(64))

    # Building and room are all-or-nothing too (0 rows have one without the
    # other). Absent on 39.2% - online and TBA meetings have no room.
    building_code: Mapped[str | None] = mapped_column(String(16))
    room_number: Mapped[str | None] = mapped_column(String(32))

    campus_location: Mapped[str | None] = mapped_column(String(16))
    campus_name: Mapped[str | None] = mapped_column(String(64))
    campus_abbrev: Mapped[str | None] = mapped_column(String(32))

    section: Mapped[CourseSection] = relationship(back_populates="meetings")

    __table_args__ = (
        UniqueConstraint("section_id", "meeting_index", name="uq_meeting_section_index"),
        CheckConstraint("meeting_index >= 0", name="meeting_index_non_negative"),
        CheckConstraint(
            "meeting_day IS NULL OR meeting_day IN ('M','T','W','H','F','S','U')",
            name="meeting_day_valid",
        ),
        # PostgreSQL-only for the same reason as index_number_numeric above:
        # `~` is a Postgres operator. The equivalent check runs unconditionally
        # in NormalizedMeeting, so SQLite-backed tests still reject bad times -
        # just one layer up.
        CheckConstraint(
            "start_time_military IS NULL OR start_time_military ~ '^[0-9]{4}$'",
            name="start_time_format",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "end_time_military IS NULL OR end_time_military ~ '^[0-9]{4}$'",
            name="end_time_format",
        ).ddl_if(dialect="postgresql"),
        # Measured: 0 rows have one time without the other.
        CheckConstraint(
            "(start_time_military IS NULL) = (end_time_military IS NULL)",
            name="times_paired",
        ),
        # NOTE: there is deliberately NO "end_time > start_time" constraint.
        # Three real Rutgers meetings violate it - ('1100','1100') and two
        # rows of ('2330','1250') on 07:966:333. Adding that constraint would
        # reject authentic source data. The validator warns about these
        # instead, which surfaces the anomaly without discarding the section.
        Index("ix_meeting_day_time", "meeting_day", "start_time_military"),
    )

    @property
    def is_tba(self) -> bool:
        """No scheduled day or time. Such a meeting cannot participate in
        conflict detection, and callers must not treat it as free time."""
        return self.meeting_day is None and self.start_time_military is None

    def __repr__(self) -> str:
        when = "TBA" if self.is_tba else f"{self.meeting_day} {self.start_time_military}"
        return f"<SectionMeeting {when} {self.meeting_mode_code}>"


class SectionInstructor(Base, TimestampMixin):
    """An instructor listed on a section.

    Natural key: (section_id, instructor_index)

    Its own table because sections have 0 (898), 1 (10,121), or 2 (973)
    instructors.

    There is deliberately NO shared `instructor` entity. The source provides
    only a display name - no id, no email, no netid. 4,004 distinct name
    strings appear, and 'WANG, HAO' alone appears 92 times. Merging on name
    would assert that every 'WANG, HAO' is the same person, which the data
    cannot support. A real instructor entity needs a stable identifier the
    source does not give us.

    The key is ordinal rather than name because 53 sections legitimately list
    the same name twice (e.g. 01:447:380 lists 'GLODOWSKI TROTT' twice), so
    (section_id, name) is NOT unique in the real payload.
    """

    __tablename__ = "section_instructor"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    section_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("course_section.id", ondelete="CASCADE"), index=True
    )
    instructor_index: Mapped[int] = mapped_column(Integer)

    # Measured: 0 instructor entries have a blank name, so this is NOT NULL.
    name: Mapped[str] = mapped_column(Text, index=True)

    section: Mapped[CourseSection] = relationship(back_populates="instructors")

    __table_args__ = (
        UniqueConstraint("section_id", "instructor_index", name="uq_instructor_section_index"),
        CheckConstraint("instructor_index >= 0", name="instructor_index_non_negative"),
        CheckConstraint("length(trim(name)) > 0", name="instructor_name_not_blank"),
    )

    def __repr__(self) -> str:
        return f"<SectionInstructor {self.name!r}>"


class SectionCrossListing(Base, TimestampMixin):
    """A cross-listing reference from one section to another course's section.

    Present on 6.9% of sections (827 sections, 966 references).

    Natural key: (section_id, registration_index) - measured unique, and
    registrationIndex is populated on 100% of cross-listing rows.

    Stored as identifier COMPONENTS, not as a foreign key to course_section.
    That is deliberate: 36 of the 966 references point at a registration index
    that does not exist in this campus/term payload, because the partner
    section lives on another campus. An FK would make those rows unstorable,
    forcing us either to drop real data or to fabricate the missing section.
    Storing identifiers keeps the reference intact and lets resolution happen
    later, when more of the catalog has been ingested.
    """

    __tablename__ = "section_cross_listing"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    section_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("course_section.id", ondelete="CASCADE"), index=True
    )

    registration_index: Mapped[str] = mapped_column(String(16), index=True)
    primary_registration_index: Mapped[str | None] = mapped_column(String(16))

    # The partner course's natural-key components, in CoursePilot's vocabulary.
    offering_unit_code: Mapped[str | None] = mapped_column(String(8))
    subject_code: Mapped[str | None] = mapped_column(String(8))
    course_number: Mapped[str | None] = mapped_column(String(16))
    supplement_code: Mapped[str] = mapped_column(String(8), default="")
    section_number: Mapped[str | None] = mapped_column(String(16))
    offering_unit_campus: Mapped[str | None] = mapped_column(String(16))

    section: Mapped[CourseSection] = relationship(back_populates="cross_listings")

    __table_args__ = (
        UniqueConstraint("section_id", "registration_index", name="uq_xlist_section_index"),
    )

    def __repr__(self) -> str:
        return f"<SectionCrossListing -> {self.registration_index}>"
