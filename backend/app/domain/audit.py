"""Typed contract for the deterministic degree audit.

Everything the audit produces is a Pydantic model, never a bare dict. The
eventual LLM layer will *read* these structures and explain them; it will
never produce them. That boundary is what keeps the model out of the
correctness path.

Mirrors the design already used by `app.domain.validation`: statuses include
an explicit "could not determine" value, because with curated requirement data
that state is common and collapsing it into satisfied/unsatisfied lies in one
direction or the other.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class RequirementStatus(StrEnum):
    """Note SATISFIED vs PROVISIONALLY_SATISFIED.

    An in-progress course has not been passed yet. Reporting it as SATISFIED
    would tell a student they are done with something they could still fail.
    """

    SATISFIED = "satisfied"
    PROVISIONALLY_SATISFIED = "provisionally_satisfied"  # relies on in-progress work
    PARTIALLY_SATISFIED = "partially_satisfied"
    UNSATISFIED = "unsatisfied"
    # We lack the data to decide. Never rendered as a pass.
    INDETERMINATE = "indeterminate"
    # The rule is authoritative and we know it exists, but the data needed to
    # check it is absent. Distinct from INDETERMINATE: nothing is missing from
    # the REQUIREMENT, something is missing from the STUDENT RECORD.
    NOT_EVALUABLE = "not_evaluable"


class AuditStatus(StrEnum):
    """Overall verdict.

    INDETERMINATE is the important one: every modeled requirement may be
    satisfied while an authoritative rule remains unevaluable. Reporting
    COMPLETE in that case would be a promise the data does not support.
    """

    COMPLETE = "complete"
    IN_PROGRESS = "in_progress"
    INCOMPLETE = "incomplete"
    # Authoritative rules exist that we cannot check with the data we hold.
    INDETERMINATE = "indeterminate"
    INSUFFICIENT_DATA = "insufficient_data"


class Severity(StrEnum):
    BLOCKING = "blocking"
    WARNING = "warning"
    INFO = "info"


class CourseRef(BaseModel):
    """A course as referenced by the audit. Always a resolved database entity -
    the audit never names a course it could not look up."""

    model_config = ConfigDict(frozen=True)

    course_id: str
    course_string: str
    supplement_code: str = ""
    title: str | None = None
    credits: Decimal | None = None


class Allocation(BaseModel):
    """One completed/in-progress course assigned to one requirement.

    Allocation is the audit's central output, not a detail. A course can be
    ELIGIBLE for several requirements but is ALLOCATED to at most one unless
    the program explicitly permits double-counting - so this record is what
    makes an audit reproducible and explainable.
    """

    course: CourseRef
    requirement_code: str
    requirement_name: str
    # Which body of requirements this slot belongs to (major, core, ...).
    requirement_system: str = "major"
    # Other systems this same course also counts toward. Non-empty only when
    # the program's sharing policy permits it - which is what lets the UI say
    # "this course satisfies both your CS requirement and a Core requirement".
    shared_with_systems: list[str] = Field(default_factory=list)
    term_code: str | None = None
    status: str  # the StudentCourse status that produced this allocation
    credits_applied: Decimal | None = None
    reason: str


class RequirementResult(BaseModel):
    """The verdict on one requirement node, with its reasoning."""

    requirement_code: str
    requirement_name: str
    requirement_type: str
    status: RequirementStatus

    # Progress, in whichever unit this requirement counts.
    needed_count: int | None = None
    satisfied_count: int = 0
    needed_credits: Decimal | None = None
    satisfied_credits: Decimal = Decimal(0)

    # For requirements that demand N DISTINCT eligibility categories, e.g.
    # "meet at least two of these goals". Distinct from the course count:
    # Rutgers SAS Arts and the Humanities requires two courses AND two goals.
    needed_distinct_categories: int | None = None
    distinct_categories: int = 0

    allocated_courses: list[CourseRef] = Field(default_factory=list)
    # Courses that COULD satisfy this requirement but were allocated
    # elsewhere, or are not yet taken. Useful for "what can I take?" later.
    eligible_not_allocated: list[CourseRef] = Field(default_factory=list)

    children: list[RequirementResult] = Field(default_factory=list)

    reason: str
    # Why a student can trust this: the curated prose behind the requirement.
    source_prose: str | None = None
    curation_status: str | None = None


class RuleResult(BaseModel):
    """The verdict on one program-level rule (grade / exclusion / residency).

    Separate from RequirementResult because a rule is not satisfied by
    courses - it constrains how courses count.
    """

    rule_code: str
    rule_name: str
    rule_type: str
    status: RequirementStatus

    observed_count: int | None = None
    allowed_count: int | None = None
    affected_courses: list[CourseRef] = Field(default_factory=list)

    reason: str
    source_prose: str | None = None
    curation_status: str | None = None


class AuditFinding(BaseModel):
    """Something the audit noticed that is not a plain requirement verdict."""

    severity: Severity
    code: str
    message: str
    requirement_code: str | None = None
    remediation: str | None = None


class DegreeAuditResult(BaseModel):
    """The complete audit.

    `status` is derived from the requirement results, never set by a caller -
    same principle as `ValidationReport.is_valid`.
    """

    program_name: str
    program_code: str
    degree_type: str
    catalog_year: str

    status: AuditStatus

    # Everything the student passed, including courses this degree excludes.
    credits_completed: Decimal = Decimal(0)
    # What actually counts toward THIS degree. Differs from credits_completed
    # whenever a program-level exclusion applies, so the two are never
    # collapsed - a student who took an excluded course deserves to see why
    # their total dropped.
    credits_applicable_to_degree: Decimal = Decimal(0)
    credits_excluded: Decimal = Decimal(0)
    credits_in_progress: Decimal = Decimal(0)
    credits_required_min: Decimal | None = None
    credits_remaining: Decimal | None = None

    requirements: list[RequirementResult] = Field(default_factory=list)
    rules: list[RuleResult] = Field(default_factory=list)
    allocation: list[Allocation] = Field(default_factory=list)
    # How this program treats a course eligible in two systems. Surfaced so an
    # audit can be explained without re-reading the database.
    sharing_policy: str = "exclusive"
    findings: list[AuditFinding] = Field(default_factory=list)
    # Courses that earn no credit toward this program, per a program rule.
    excluded_courses: list[CourseRef] = Field(default_factory=list)

    # Courses on the record that no requirement could use. Not an error - free
    # electives are normal - but the student should be able to see them.
    unallocated_courses: list[CourseRef] = Field(default_factory=list)

    # Standing caveat. CoursePilot is a planning aid; Rutgers' own language is
    # that only an academic advisor can certify degree requirements.
    disclaimers: list[str] = Field(default_factory=list)

    @property
    def is_complete(self) -> bool:
        return self.status is AuditStatus.COMPLETE

    @property
    def shared_allocations(self) -> list[Allocation]:
        """Allocations where one course counted toward more than one system."""
        return [a for a in self.allocation if a.shared_with_systems]

    @property
    def has_indeterminate(self) -> bool:
        """True when any requirement or rule could not be decided. The UI must
        not present such an audit as confirmed."""

        def walk(results: list[RequirementResult]) -> bool:
            for r in results:
                if r.status in (
                    RequirementStatus.INDETERMINATE,
                    RequirementStatus.NOT_EVALUABLE,
                ):
                    return True
                if walk(r.children):
                    return True
            return False

        return walk(self.requirements) or any(
            r.status in (RequirementStatus.INDETERMINATE, RequirementStatus.NOT_EVALUABLE)
            for r in self.rules
        )

    @property
    def not_evaluable_rules(self) -> list[RuleResult]:
        """Authoritative rules we could not check. If this is non-empty the
        audit can never be COMPLETE."""
        return [r for r in self.rules if r.status is RequirementStatus.NOT_EVALUABLE]
