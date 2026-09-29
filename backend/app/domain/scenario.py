"""Typed contract for hypothetical program scenarios (Phase 6.0).

```
Student academic record
        │
        ├── actual ProgramVersion  ──> Degree Engine ──> actual DegreeAuditResult
        │
        └── target ProgramVersion  ──> Degree Engine ──> ScenarioAudit.audit
```

A scenario is the SAME engine over the SAME record with DIFFERENT rules. It is
not the student's program, not a request to change it, and not a statement
that Rutgers would admit the student to it. Every scenario carries its
assumptions so a client can never render one as though it were the real audit.

Like `app.domain.audit`, these models are produced by deterministic code only.
A language model may later READ a `ProgramComparison` to explain it; it never
produces one.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field

from app.domain.audit import AuditStatus, DegreeAuditResult, RequirementStatus


class ScenarioTarget(BaseModel):
    """Which rules the scenario used, by natural key - never a UUID."""

    program_key: str
    program_name: str
    program_code: str
    degree_type: str
    school_code: str
    catalog_year: str
    support_status: str
    curation_status: str
    #: True when the target IS the student's own program version. Then the
    #: scenario audit equals the actual audit, which a test asserts.
    is_current_program: bool


class ScenarioAssumption(BaseModel):
    """Something the scenario takes for granted that the student should know."""

    code: str
    message: str


class ScenarioAudit(BaseModel):
    target: ScenarioTarget
    assumptions: list[ScenarioAssumption] = Field(default_factory=list)
    #: The engine's own result under the target rules, unmodified.
    audit: DegreeAuditResult


# --------------------------------------------------------------------------
# deterministic comparison
# --------------------------------------------------------------------------


class RequirementRef(BaseModel):
    code: str
    name: str
    system: str


class CourseComparison(BaseModel):
    """How one course on the record is used under each program.

    Keyed by course, not by term: a retaken course appears once, with every
    requirement it was applied to.
    """

    course_string: str
    title: str | None = None
    current_requirements: list[RequirementRef] = Field(default_factory=list)
    target_requirements: list[RequirementRef] = Field(default_factory=list)
    #: Earns no credit toward that program under one of its program rules.
    excluded_by_current: bool = False
    excluded_by_target: bool = False


class RequirementOutcome(BaseModel):
    code: str
    name: str
    status: RequirementStatus


class SharedRequirementOutcome(BaseModel):
    """A requirement node that is the same requirement in both programs.

    "Same" means identical requirement code AND identical source prose - that
    is, loaded from the same official source into both versions (SAS Core is
    the real case). Code alone is not enough: codes are unique only within a
    version, and the real CS and Mathematics definitions both contain a
    `MATH_151` curated from different catalog pages. Nothing is inferred from
    similar names or purposes.
    """

    code: str
    name: str
    current_status: RequirementStatus
    target_status: RequirementStatus


class ProgramOutcome(BaseModel):
    """One program's audit, reduced to comparable facts. No new judgments."""

    program_name: str
    degree_type: str
    catalog_year: str
    status: AuditStatus
    credits_applicable_to_degree: Decimal
    credits_excluded: Decimal
    credits_required_min: Decimal | None
    credits_remaining: Decimal | None
    #: Leaf requirement nodes - the ones courses are allocated to.
    leaf_requirements: int
    leaf_satisfied: int
    leaf_provisionally_satisfied: int


class ProgramComparison(BaseModel):
    """The deterministic difference between two audits of one record.

    Everything here is read off two `DegreeAuditResult`s. Nothing is
    estimated: there is no "semesters longer" figure, because that depends on
    what the student takes next, which is the Planning Engine's question.
    There is no ranking, and no recommendation of either program.
    """

    current: ProgramOutcome
    target: ProgramOutcome

    courses_applied_in_both: list[CourseComparison] = Field(default_factory=list)
    courses_applied_only_current: list[CourseComparison] = Field(default_factory=list)
    courses_applied_only_target: list[CourseComparison] = Field(default_factory=list)
    courses_applied_in_neither: list[CourseComparison] = Field(default_factory=list)

    requirements_in_both: list[SharedRequirementOutcome] = Field(default_factory=list)
    requirements_only_current: list[RequirementOutcome] = Field(default_factory=list)
    requirements_only_target: list[RequirementOutcome] = Field(default_factory=list)

    notes: list[str] = Field(default_factory=list)
