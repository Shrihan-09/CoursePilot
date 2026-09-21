"""Programs, catalog versions, and the requirement tree.

Every design choice here follows from measured catalog data - see
docs/DATA_SOURCES.md Phase 3. The two findings that shape this file:

1. **Rutgers publishes requirements as PROSE, not structured data.** The
   catalog runs on Coursedog, whose platform schema does contain requirement
   primitives (`requirementType`, `courseCount`, ...), but Rutgers has not
   populated them for the programs inspected. The CS major requirement is a
   1,661-character English paragraph.

   Consequence: structured requirements are *manually curated* from official
   prose. That is a derivation, not an extraction, so every requirement row
   carries the prose it came from plus a curation status. CoursePilot must
   never present a curated requirement as though Rutgers published it in this
   shape.

2. **Course descriptions live in the catalog, not SOC.** SOC returns an empty
   description for 100% of courses; the catalog supplies them for 100% of the
   43 CS courses measured. Descriptions are also catalog-year scoped, so they
   attach to a versioned catalog entry rather than overwriting `course`.

Deliberately NOT modeled yet: prerequisites as structure, equivalencies,
substitutions, double-counting policy between programs.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from enum import StrEnum

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


class RequirementType(StrEnum):
    """Node types, derived from what the real CS requirement prose needs.

    Each was required by an actual clause in the Rutgers CS major text; none
    is speculative:

      ALL_OF     "six required courses ... 111, 112, 205, 206, 211, and 344"
      ANY_OF     "four physics courses ... or chemistry ..."
      CHOOSE_N   "five electives from a designated list"
      CREDITS    "The B.A. option requires 51-55 credits"
      COURSE     a single required course (leaf)
    """

    ALL_OF = "all_of"
    ANY_OF = "any_of"
    CHOOSE_N = "choose_n"
    CREDITS = "credits"
    COURSE = "course"


class RequirementSystem(StrEnum):
    """Which body of requirements a node belongs to.

    Stored as a plain string rather than a constrained enum column, because
    Rutgers has more systems than we have modeled (school requirements,
    general education, college requirements) and adding one must not need a
    migration. These constants are the known values, not an exhaustive set.

    The system is what makes sharing expressible: allocation is exclusive
    WITHIN a system, and sharing ACROSS systems is governed by policy.
    """

    MAJOR = "major"
    CORE = "core"


class SharingPolicy(StrEnum):
    """Whether one course may satisfy requirements in more than one system.

    Only two values, deliberately. A third - sharing WITHIN a single tree -
    was considered and left out: no observed Rutgers rule needs it, and an
    unused policy value is an invitation to apply it wrongly. Add it when a
    real requirement demands it.

    EXCLUSIVE is the default because the conservative direction is the safe
    one: over-counting tells a student they can graduate when they cannot.
    """

    # One course fills at most one slot anywhere in the program.
    EXCLUSIVE = "exclusive"
    # One course may fill at most one slot in EACH system (e.g. one major
    # slot and one core slot), but never two slots in the same system.
    SHARE_ACROSS_SYSTEMS = "share_across_systems"


class CurationStatus(StrEnum):
    """How a requirement row came to exist.

    There is no `EXTRACTED` value, because nothing in the catalog is machine
    extractable as structure. Pretending otherwise is the failure mode this
    enum exists to prevent.
    """

    CURATED_FROM_PROSE = "curated_from_prose"   # a human read official prose and encoded it
    SYNTHETIC = "synthetic"                     # test-only; never real Rutgers data
    UNVERIFIED = "unverified"


class School(Base, TimestampMixin):
    """A Rutgers school, e.g. SAS. Distinct from `offering_unit_code` on a
    course: a school runs programs, a unit offers courses, and they do not
    correspond one-to-one."""

    __tablename__ = "school"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(Text)
    campus_code: Mapped[str | None] = mapped_column(String(16))
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("data_source.id"))

    programs: Mapped[list[Program]] = relationship(back_populates="school")

    __table_args__ = (UniqueConstraint("code", name="uq_school_code"),)

    def __repr__(self) -> str:
        return f"<School {self.code}>"


class Program(Base, TimestampMixin):
    """A degree program, independent of catalog year.

    Natural key: (school_id, code, degree_type).

    `degree_type` is part of identity because the Rutgers CS page defines a
    B.A. and a B.S. with *different* requirements under one program name. They
    are two programs a student can be in, not one program with a flag.
    """

    __tablename__ = "program"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    school_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("school.id"), index=True)

    code: Mapped[str] = mapped_column(String(16))          # e.g. "198"
    name: Mapped[str] = mapped_column(Text)                # "Computer Science"
    degree_type: Mapped[str] = mapped_column(String(16))   # "BA" | "BS" | "minor"

    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("data_source.id"))

    school: Mapped[School] = relationship(back_populates="programs")
    versions: Mapped[list[ProgramVersion]] = relationship(
        back_populates="program", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("school_id", "code", "degree_type", name="uq_program_natural_key"),
    )

    def __repr__(self) -> str:
        return f"<Program {self.code} {self.degree_type}>"


class ProgramVersion(Base, TimestampMixin):
    """One catalog year's rules for a program.

    This is the table that makes "Computer Science requirements" a
    time-scoped fact rather than a timeless one. A student is bound to the
    version in effect when they matriculated.

    Natural key: (program_id, catalog_year).

    Measured caveat: CS requirements were byte-identical between catalog
    2025-2026 and 2026-2027. Versioning is still required - the point is that
    the schema cannot ASSUME stability - but this project has not yet observed
    a real requirement change. See docs/DATA_SOURCES.md.
    """

    __tablename__ = "program_version"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    program_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("program.id", ondelete="CASCADE"), index=True
    )

    # Catalog year as Rutgers publishes it, e.g. "2026-2027". The catalog
    # subdomain encodes the same thing (newbrunswick-26-27-undergrad).
    catalog_year: Mapped[str] = mapped_column(String(16), index=True)

    # Total credits for the degree, when the prose states one. The CS text
    # gives a RANGE ("51-55 credits"), so this is min/max, not a single value.
    total_credits_min: Mapped[Decimal | None] = mapped_column(Numeric(5, 1))
    total_credits_max: Mapped[Decimal | None] = mapped_column(Numeric(5, 1))

    # The exact published paragraph this version was curated from. Kept so a
    # human can always re-check the derivation against the source.
    # How this program treats a course that is eligible in two systems.
    # EXCLUSIVE by default: a program that has not stated a sharing rule must
    # not silently grant extra credit.
    sharing_policy: Mapped[str] = mapped_column(
        String(32), default=SharingPolicy.EXCLUSIVE.value
    )

    source_prose: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text)
    curation_status: Mapped[str] = mapped_column(
        String(32), default=CurationStatus.UNVERIFIED.value
    )

    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("data_source.id"))

    program: Mapped[Program] = relationship(back_populates="versions")
    requirements: Mapped[list[Requirement]] = relationship(
        back_populates="program_version", cascade="all, delete-orphan"
    )
    rules: Mapped[list[ProgramRule]] = relationship(
        back_populates="program_version", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("program_id", "catalog_year", name="uq_program_version"),
        CheckConstraint(
            "total_credits_min IS NULL OR total_credits_min >= 0",
            name="total_credits_min_non_negative",
        ),
        CheckConstraint(
            "total_credits_max IS NULL OR total_credits_min IS NULL "
            "OR total_credits_max >= total_credits_min",
            name="total_credits_range_ordered",
        ),
        # Closed set, unlike requirement_system: an unknown policy would make
        # the allocator fall back to some default silently.
        CheckConstraint(
            "sharing_policy IN ('exclusive','share_across_systems')",
            name="sharing_policy_known",
        ),
    )

    def __repr__(self) -> str:
        return f"<ProgramVersion {self.catalog_year}>"


class Requirement(Base, TimestampMixin):
    """A node in the requirement tree.

    Recursive via `parent_id`. A tree rather than a flat list because the real
    CS text nests: the B.S. science requirement is "physics OR chemistry",
    where each branch is itself an alternative set of course sequences.
    Flattening that into independent requirements loses the OR and would let a
    student satisfy it by mixing one physics course with one chemistry course.

    `sort_order` preserves the published ordering, which is how a student
    recognises their own requirements.
    """

    __tablename__ = "requirement"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    program_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("program_version.id", ondelete="CASCADE"), index=True
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("requirement.id", ondelete="CASCADE"), index=True
    )

    # Stable human-usable label, unique within a version so tests and
    # explanations can refer to a requirement without knowing its UUID.
    code: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(Text)
    requirement_type: Mapped[str] = mapped_column(String(24))
    # Which body of requirements this node belongs to - see RequirementSystem.
    # Intentionally unconstrained at the database level: new systems must not
    # require a migration.
    requirement_system: Mapped[str] = mapped_column(
        String(32), default=RequirementSystem.MAJOR.value, index=True
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    # --- rule parameters (all nullable; meaning depends on requirement_type) ---
    # CHOOSE_N / ANY_OF: how many children or options must be satisfied.
    min_count: Mapped[int | None] = mapped_column(Integer)
    # CREDITS: how many credits must accumulate.
    min_credits: Mapped[Decimal | None] = mapped_column(Numeric(5, 1))

    # Constraints measured in the real CS elective clause:
    #   "at most, two of the five electives may be taken outside the
    #    Department of Computer Science"
    #   "at least two must be computer science courses at the 300 level or above"
    max_outside_subject: Mapped[int | None] = mapped_column(Integer)
    constraint_subject_code: Mapped[str | None] = mapped_column(String(8))
    min_at_level: Mapped[int | None] = mapped_column(Integer)
    min_at_level_count: Mapped[int | None] = mapped_column(Integer)

    # How many DISTINCT eligibility categories the allocated courses must
    # cover. Generic, not an Arts-and-Humanities special case: it expresses
    # any "meet at least N of these goals" rule.
    #
    # Rutgers SAS, Arts and the Humanities: "Students must take two degree
    # credit-bearing courses and meet at least two of these goals." Those are
    # two INDEPENDENT conditions - min_count=2 courses AND
    # min_distinct_categories=2 goals - which is why a single course certified
    # for two goals cannot satisfy the area on its own.
    min_distinct_categories: Mapped[int | None] = mapped_column(Integer)

    notes: Mapped[str | None] = mapped_column(Text)
    source_prose: Mapped[str | None] = mapped_column(Text)
    curation_status: Mapped[str] = mapped_column(
        String(32), default=CurationStatus.UNVERIFIED.value
    )
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("data_source.id"))

    program_version: Mapped[ProgramVersion] = relationship(back_populates="requirements")
    children: Mapped[list[Requirement]] = relationship(
        back_populates="parent", cascade="all, delete-orphan", remote_side=None
    )
    parent: Mapped[Requirement | None] = relationship(
        back_populates="children", remote_side="Requirement.id"
    )
    course_options: Mapped[list[RequirementCourseOption]] = relationship(
        back_populates="requirement", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("program_version_id", "code", name="uq_requirement_code"),
        CheckConstraint("min_count IS NULL OR min_count >= 0", name="min_count_non_negative"),
        CheckConstraint(
            "min_distinct_categories IS NULL OR min_distinct_categories >= 0",
            name="min_distinct_categories_non_negative",
        ),
        CheckConstraint("min_credits IS NULL OR min_credits >= 0", name="min_credits_non_negative"),
        CheckConstraint(
            "requirement_type IN ('all_of','any_of','choose_n','credits','course')",
            name="requirement_type_known",
        ),
        Index("ix_requirement_parent_sort", "parent_id", "sort_order"),
    )

    def __repr__(self) -> str:
        return f"<Requirement {self.code} {self.requirement_type}>"


class RequirementCourseOption(Base, TimestampMixin):
    """ELIGIBILITY: course X *can* satisfy requirement Y.

    Deliberately NOT satisfaction. Nothing about a student lives here. The
    audit engine computes satisfaction from (eligibility + student history +
    rules), so the same eligibility rows serve every student.

    A course may appear under many requirements - that is normal and is why
    allocation is a real problem rather than a lookup.
    """

    __tablename__ = "requirement_course_option"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    requirement_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("requirement.id", ondelete="CASCADE"), index=True
    )
    course_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("course.id", ondelete="CASCADE"), index=True
    )

    # WHY the course is eligible - the source's own certification identifier.
    # For SAS Core this is the goal code (AHp, AHq, ...); for a major
    # requirement there is no sub-category and it is ''.
    #
    # Empty string rather than NULL, deliberately: this column is part of the
    # unique constraint, and in SQL NULL != NULL, so a nullable column would
    # let the same (requirement, course) pair be inserted without limit.
    #
    # Keeping the certifying category is what lets "meet at least two of these
    # goals" be evaluated at all. Collapsing AHo/AHp/AHq/AHr into one
    # undifferentiated option destroys exactly the information the rule needs.
    category: Mapped[str] = mapped_column(String(16), default="", server_default="")

    # Credits this course contributes toward THIS requirement, when the
    # program counts it differently from the course's own credit value.
    credits_applied: Mapped[Decimal | None] = mapped_column(Numeric(4, 1))
    # Minimum grade, when the prose states one for this specific option.
    min_grade: Mapped[str | None] = mapped_column(String(4))

    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("data_source.id"))

    requirement: Mapped[Requirement] = relationship(back_populates="course_options")

    __table_args__ = (
        # Includes category: one course may be certified for a requirement
        # under several goals (AHp AND AHq), and each certification is a
        # distinct fact from the source.
        UniqueConstraint(
            "requirement_id", "course_id", "category", name="uq_requirement_course"
        ),
    )

    def __repr__(self) -> str:
        return f"<RequirementCourseOption {self.requirement_id}:{self.course_id}>"


class CatalogCourseEntry(Base, TimestampMixin):
    """A course as described by ONE catalog year.

    Separate from `course` on purpose. `course` is the term-independent SOC
    identity; this is the catalog's *description* of that course in a given
    year, and descriptions, titles, and credits can differ between years.
    Writing them onto `course` would overwrite history.

    Natural key: (course_string, catalog_year).

    `credits_min`/`credits_max`: the catalog publishes ranges such as "3-4",
    which SOC's single numeric credits field cannot represent.

    ## Why `course_id` is NULLABLE

    Measured: of 43 CS catalog courses, 13 (30%) have no `course` row. None is
    a formatting or supplement problem - they simply were not OFFERED in the
    terms we ingested. Four appear in other archived terms (Spring 2026, Fall
    2025); nine appear in no archived term at all.

    **The catalog is a superset of what SOC offers in any term.** A required
    FK would make 30% of the authoritative description source unstorable, and
    a degree audit must be able to name a course a student took years ago that
    nobody is teaching this term.

    So the key is `course_string` (always present) and `course_id` is a
    nullable link that gets filled in if and when SOC offers the course. This
    does NOT duplicate `course`: no SOC identity or credits are copied here,
    only the catalog's own description of a course code.
    """

    __tablename__ = "catalog_course_entry"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    # The catalog's own identifier, e.g. "01:198:111". Always populated - this
    # is what makes a catalog-only course storable.
    course_string: Mapped[str] = mapped_column(String(32), index=True)
    # Link to the SOC course, when one exists. NULL means "the catalog
    # describes this course but we have no SOC record for it yet".
    course_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("course.id", ondelete="CASCADE"), index=True
    )
    catalog_year: Mapped[str] = mapped_column(String(16), index=True)

    title: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    credits_min: Mapped[Decimal | None] = mapped_column(Numeric(4, 1))
    credits_max: Mapped[Decimal | None] = mapped_column(Numeric(4, 1))
    credits_raw: Mapped[str | None] = mapped_column(String(32))

    source_url: Mapped[str | None] = mapped_column(Text)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("data_source.id"))

    __table_args__ = (
        UniqueConstraint(
            "course_string", "catalog_year", name="uq_catalog_entry_course_year"
        ),
        CheckConstraint(
            "credits_min IS NULL OR credits_min >= 0", name="catalog_credits_min_non_negative"
        ),
        CheckConstraint(
            "credits_max IS NULL OR credits_min IS NULL OR credits_max >= credits_min",
            name="catalog_credits_range_ordered",
        ),
    )

    def __repr__(self) -> str:
        linked = "linked" if self.course_id else "unlinked"
        return f"<CatalogCourseEntry {self.course_string} {self.catalog_year} {linked}>"


class ProgramRuleType(StrEnum):
    """Program-level rules: constraints on the DEGREE, not on one requirement.

    Each comes from a real clause in the Rutgers CS prose that the requirement
    tree cannot express, because the tree evaluates nodes independently and
    these rules span the whole program:

      MAX_GRADE_COUNT   "No more than one grade of D can be accepted in the
                         courses required for the major."
      COURSE_EXCLUSION  "Declared computer science majors (198) will not
                         receive credit ... for ... 105, 107, 110, 142, 170,
                         or 405."
      RESIDENCY         "A minimum of seven courses must be taken in the
                         Rutgers University-New Brunswick Department of
                         Computer Science."
    """

    MAX_GRADE_COUNT = "max_grade_count"
    COURSE_EXCLUSION = "course_exclusion"
    RESIDENCY = "residency"


class ProgramRule(Base, TimestampMixin):
    """A degree-level constraint attached to one ProgramVersion.

    Deliberately NOT a Requirement. A requirement is satisfied by courses; a
    rule constrains how courses count. Modeling "no more than one D" as a
    requirement node would mean inventing a node that no course can satisfy.

    ## `is_evaluable`

    Some authoritative rules cannot be checked with the data CoursePilot has.
    Residency is the measured example: it requires knowing which courses were
    taken *at Rutgers-New Brunswick* versus transferred in, and the student
    record has no transfer provenance.

    Rather than guess, such a rule is stored with `is_evaluable = False` and a
    reason. The audit then reports NOT_EVALUABLE and refuses to call the degree
    complete - which is the honest answer, and the one a student can act on.
    """

    __tablename__ = "program_rule"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    program_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("program_version.id", ondelete="CASCADE"), index=True
    )

    code: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(Text)
    rule_type: Mapped[str] = mapped_column(String(32))

    # --- parameters (meaning depends on rule_type; all nullable) ---
    # MAX_GRADE_COUNT: at most `max_count` occurrences of `grade`.
    grade: Mapped[str | None] = mapped_column(String(4))
    max_count: Mapped[int | None] = mapped_column(Integer)
    # RESIDENCY: at least `min_count` courses in this subject/unit.
    min_count: Mapped[int | None] = mapped_column(Integer)
    subject_code: Mapped[str | None] = mapped_column(String(8))
    offering_unit_code: Mapped[str | None] = mapped_column(String(8))

    # COURSE_EXCLUSION: the excluded course codes, as published. Stored as
    # text rather than FKs because an excluded course need not exist in the
    # `course` table (it may not be offered), and the exclusion is still real.
    excluded_course_strings: Mapped[str | None] = mapped_column(Text)

    # False when the rule is authoritative but the data to check it is absent.
    is_evaluable: Mapped[bool] = mapped_column(default=True)
    not_evaluable_reason: Mapped[str | None] = mapped_column(Text)

    source_prose: Mapped[str | None] = mapped_column(Text)
    curation_status: Mapped[str] = mapped_column(
        String(32), default=CurationStatus.UNVERIFIED.value
    )
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("data_source.id"))

    program_version: Mapped[ProgramVersion] = relationship(back_populates="rules")

    __table_args__ = (
        UniqueConstraint("program_version_id", "code", name="uq_program_rule_code"),
        CheckConstraint(
            "rule_type IN ('max_grade_count','course_exclusion','residency')",
            name="program_rule_type_known",
        ),
        CheckConstraint("max_count IS NULL OR max_count >= 0", name="rule_max_count_non_negative"),
        CheckConstraint("min_count IS NULL OR min_count >= 0", name="rule_min_count_non_negative"),
        # A non-evaluable rule must say why, or it is just a silent omission.
        CheckConstraint(
            "is_evaluable OR not_evaluable_reason IS NOT NULL",
            name="not_evaluable_requires_reason",
        ),
    )

    @property
    def excluded_courses(self) -> list[str]:
        if not self.excluded_course_strings:
            return []
        return [c.strip() for c in self.excluded_course_strings.split(",") if c.strip()]

    def __repr__(self) -> str:
        return f"<ProgramRule {self.code} {self.rule_type}>"
