"""SQLAlchemy ORM models.

Every model must be imported here so that `Base.metadata` is fully populated
before Alembic autogenerate runs. A model that is defined but not imported is
invisible to migrations, and the resulting migration silently omits its table.

Implemented so far:

  Phase 1 - course ingestion
    * DataSource            provenance for every Rutgers-derived record
    * Subject               normalized subject codes
    * Course                catalog course, term- and campus-independent
    * CourseOffering        a course in a specific term and campus

  Phase 2 - section ingestion
    * CourseSection         a registerable section of an offering
    * SectionMeeting        one meeting pattern (1..5 per section)
    * SectionInstructor     an instructor listed on a section (0..2)
    * SectionCrossListing   a cross-listing reference (0..n)

  Phase 3 - catalog and degree requirements
    * School                a Rutgers school (SAS, SOE, ...)
    * Program               a degree program, catalog-year independent
    * ProgramVersion        one catalog year's rules for a program
    * Requirement           a node in the recursive requirement tree
    * RequirementCourseOption   ELIGIBILITY: course X can satisfy requirement Y
    * CatalogCourseEntry    a course as described by one catalog year
    * Student               minimal academic record (no auth, no PII)
    * StudentCourse         completed / in-progress / planned courses

Not yet modeled: prerequisites as structure, equivalencies, substitutions,
section restriction lists, cross-program double-counting policy.
"""

from app.models.academic import Course, CourseOffering, Subject
from app.models.provenance import DataSource
from app.models.requirements import (
    CatalogCourseEntry,
    CurationStatus,
    Program,
    ProgramRule,
    ProgramRuleType,
    ProgramVersion,
    Requirement,
    RequirementCourseOption,
    RequirementSystem,
    RequirementType,
    School,
    SharingPolicy,
)
from app.models.identity import PROVIDER_DEV, PROVIDER_OIDC, UserAccount
from app.models.student import EnrollmentStatus, Student, StudentCourse
from app.models.sections import (
    CourseSection,
    SectionCrossListing,
    SectionInstructor,
    SectionMeeting,
)

__all__ = [
    "CatalogCourseEntry",
    "Course",
    "CourseOffering",
    "CourseSection",
    "CurationStatus",
    "DataSource",
    "EnrollmentStatus",
    "Program",
    "ProgramRule",
    "ProgramRuleType",
    "ProgramVersion",
    "Requirement",
    "RequirementCourseOption",
    "RequirementSystem",
    "RequirementType",
    "SharingPolicy",
    "School",
    "SectionCrossListing",
    "SectionInstructor",
    "SectionMeeting",
    "PROVIDER_DEV",
    "PROVIDER_OIDC",
    "Student",
    "UserAccount",
    "StudentCourse",
    "Subject",
]
