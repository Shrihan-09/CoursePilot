"""Deterministic explanation evidence (Phase 5.1, Parts B, D and E).

## The direction of authority

```
Degree Engine  ->  structured facts  ->  LLM  ->  explanation
```

Never the reverse. Everything in this module is produced by the deterministic
audit or retrieved verbatim from an authoritative source **before** any model
is called. A model that is handed this package can only phrase it; there is
nothing here for it to decide.

## Three kinds of fact, kept apart on purpose

| Kind | Produced by | May a model contradict it? |
|---|---|---|
| `DecisionFact` | the Degree Engine | never |
| `CourseFact` | Rutgers SOC / Catalog, verbatim | never |
| `RequirementFact` | curated requirement data, verbatim | never |

They are separate types rather than one list of strings because they fail
differently. A decision fact being wrong is an engine bug. A course fact
being wrong is an ingestion bug. Flattening them would make an explanation
unable to say which kind of thing it is asserting - and would let catalog
prose be mistaken for an academic ruling.

## What is deliberately absent

There is no `confidence`, no `score`, no `suggestion`. The audit already
decided; evidence records what it decided and why. A field that invited a
model to express doubt about a deterministic result would be an invitation
to average the two, which the architecture forbids.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from app.domain.audit import Allocation, CourseRef, DegreeAuditResult, RequirementResult
from app.services.search.documents import CourseDocument, Provenance


class ExplanationType(StrEnum):
    """Why the explanation was requested.

    Explicit types rather than one generic prompt: each needs different
    evidence, and a type that cannot be grounded must be refused rather than
    answered vaguely.
    """

    WHY_RECOMMENDED = "why_recommended"
    HOW_IT_HELPS = "how_it_helps"
    WHAT_IS_COURSE = "what_is_course"
    WHY_NOT_RECOMMENDED = "why_not_recommended"


#: Where a fact came from, when it is CoursePilot's own computation rather
#: than an external document.
SOURCE_DEGREE_ENGINE = "coursepilot_degree_audit"
SOURCE_REQUIREMENT_MODEL = "coursepilot_requirement_model"


@dataclass(frozen=True, slots=True)
class DecisionFact:
    """One thing the Degree Engine determined.

    `statement` is already readable prose, because the engine has been
    writing deterministic reasons since Phase 3 (`Allocation.reason`,
    `RequirementResult.reason`). The explanation layer reuses that rather
    than re-deriving it, which is why a model is not needed to produce a
    correct answer at all - only a fluent one.
    """

    statement: str
    #: Machine-checkable payload, used by validation to confirm the
    #: explanation did not drift from the decision.
    course_key: str | None = None
    requirement_code: str | None = None
    credits: Decimal | None = None
    source_kind: str = SOURCE_DEGREE_ENGINE

    def citation(self) -> str:
        return self.source_kind


@dataclass(frozen=True, slots=True)
class CourseFact:
    """Verbatim catalog or SOC information about one course."""

    label: str
    value: str
    course_key: str
    provenance: Provenance | None = None

    def citation(self) -> str:
        if self.provenance is None:
            return "unattributed"
        if self.provenance.catalog_year:
            return f"{self.provenance.source_kind} {self.provenance.catalog_year}"
        return self.provenance.source_kind


@dataclass(frozen=True, slots=True)
class RequirementFact:
    """Curated requirement information, quoted rather than characterised.

    `source_prose` on a requirement is the Rutgers wording that was curated
    in Phase 3/4. Quoting it keeps the explanation anchored to what the
    university actually published.
    """

    requirement_code: str
    requirement_name: str
    statement: str
    curation_status: str | None = None
    source_kind: str = SOURCE_REQUIREMENT_MODEL

    def citation(self) -> str:
        return self.source_kind


@dataclass(frozen=True, slots=True)
class ExplanationEvidence:
    """Everything an explanation may rely on. Assembled without a model."""

    explanation_type: ExplanationType
    course_key: str
    course_title: str
    decision_facts: tuple[DecisionFact, ...] = ()
    course_facts: tuple[CourseFact, ...] = ()
    requirement_facts: tuple[RequirementFact, ...] = ()
    #: Verbatim retrieved snippets, already bounded by the Phase 5.0 layer.
    source_documents: tuple[CourseFact, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def requirement_codes(self) -> tuple[str, ...]:
        codes = [f.requirement_code for f in self.decision_facts if f.requirement_code]
        codes += [f.requirement_code for f in self.requirement_facts]
        return tuple(dict.fromkeys(codes))

    @property
    def is_grounded(self) -> bool:
        """Whether there is any deterministic basis to explain at all.

        A `what_is_course` request needs only catalog facts; every other type
        needs the engine to have decided something first. An ungrounded
        request must be refused, not answered from retrieved text.
        """
        if self.explanation_type is ExplanationType.WHAT_IS_COURSE:
            return bool(self.course_facts or self.source_documents)
        return bool(self.decision_facts)

    def citations(self) -> tuple[str, ...]:
        seen: list[str] = []
        for fact in (
            *self.decision_facts,
            *self.requirement_facts,
            *self.course_facts,
            *self.source_documents,
        ):
            citation = fact.citation()
            if citation not in seen:
                seen.append(citation)
        return tuple(seen)


# --------------------------------------------------------------------------
# building evidence from an existing audit
# --------------------------------------------------------------------------


def _walk(results: list[RequirementResult]):
    for result in results:
        yield result
        yield from _walk(result.children)


def _find_requirement(
    audit: DegreeAuditResult, code: str
) -> RequirementResult | None:
    for result in _walk(audit.requirements):
        if result.requirement_code == code:
            return result
    return None


def _course_facts(document: CourseDocument | None, course_key: str) -> tuple[CourseFact, ...]:
    if document is None:
        return ()
    facts: list[CourseFact] = [
        CourseFact("title", document.title, course_key, document.course_provenance)
    ]
    if document.credits is not None:
        facts.append(
            CourseFact(
                "credits", str(document.credits), course_key, document.course_provenance
            )
        )
    if document.has_description:
        facts.append(
            CourseFact(
                "description",
                document.description or "",
                course_key,
                document.description_provenance,
            )
        )
    return tuple(facts)


def build_recommendation_evidence(
    audit: DegreeAuditResult,
    course_key: str,
    *,
    explanation_type: ExplanationType = ExplanationType.WHY_RECOMMENDED,
    document: CourseDocument | None = None,
    baseline_satisfied: frozenset[str] = frozenset(),
) -> ExplanationEvidence:
    """Assemble evidence for a course the audit ALREADY allocated.

    Reads the audit rather than recomputing anything: the allocation, its
    reason, the resulting requirement state, the credits applied, any shared
    systems, and whether the requirement was already satisfied at baseline.
    """
    allocations = [a for a in audit.allocation if a.course.course_string == course_key]
    title = allocations[0].course.title if allocations else ""
    if not title and document is not None:
        title = document.title

    decision: list[DecisionFact] = []
    requirement_facts: list[RequirementFact] = []

    for allocation in allocations:
        decision.append(
            DecisionFact(
                statement=allocation.reason,
                course_key=course_key,
                requirement_code=allocation.requirement_code,
                credits=allocation.credits_applied,
            )
        )

        requirement = _find_requirement(audit, allocation.requirement_code)
        if requirement is not None:
            decision.append(
                DecisionFact(
                    statement=(
                        f"After this allocation, {requirement.requirement_name} "
                        f"({requirement.requirement_code}) is "
                        f"{requirement.status.value.replace('_', ' ')}: "
                        f"{requirement.reason}"
                    ),
                    course_key=course_key,
                    requirement_code=requirement.requirement_code,
                )
            )
            if requirement.source_prose:
                requirement_facts.append(
                    RequirementFact(
                        requirement_code=requirement.requirement_code,
                        requirement_name=requirement.requirement_name,
                        statement=requirement.source_prose,
                        curation_status=requirement.curation_status,
                    )
                )
            # Baseline comparison is a FACT from Phase 4.5, not a judgement.
            if requirement.requirement_code in baseline_satisfied:
                decision.append(
                    DecisionFact(
                        statement=(
                            f"{requirement.requirement_code} was already satisfied "
                            "by previously completed coursework."
                        ),
                        course_key=course_key,
                        requirement_code=requirement.requirement_code,
                    )
                )

        if allocation.shared_with_systems:
            decision.append(
                DecisionFact(
                    statement=(
                        f"This course also counts toward "
                        f"{', '.join(allocation.shared_with_systems)} requirements, "
                        f"which this program's sharing policy "
                        f"({audit.sharing_policy}) permits."
                    ),
                    course_key=course_key,
                    requirement_code=allocation.requirement_code,
                )
            )

    notes: list[str] = []
    if not allocations:
        notes.append(
            f"The audit did not allocate {course_key}; there is no recommendation "
            "to explain."
        )

    return ExplanationEvidence(
        explanation_type=explanation_type,
        course_key=course_key,
        course_title=title,
        decision_facts=tuple(decision),
        course_facts=_course_facts(document, course_key),
        requirement_facts=tuple(requirement_facts),
        notes=tuple(notes),
    )


def build_not_recommended_evidence(
    audit: DegreeAuditResult,
    course_key: str,
    *,
    document: CourseDocument | None = None,
) -> ExplanationEvidence:
    """Explain why a course was NOT allocated - only where the engine says so.

    The ONLY admissible evidence is what the audit already recorded:

      * the course was eligible for a requirement and not allocated
        (`RequirementResult.eligible_not_allocated`);
      * the course was excluded by a program rule (`excluded_courses`);
      * the course is on the record but allocated nowhere
        (`unallocated_courses`).

    If none of those apply, the evidence comes back ungrounded and the
    service refuses. A model must never be asked to infer a reason - "it
    conflicts with your schedule" is exactly the kind of plausible invention
    this shape exists to prevent.
    """
    decision: list[DecisionFact] = []

    if any(c.course_string == course_key for c in audit.excluded_courses):
        decision.append(
            DecisionFact(
                statement=(
                    f"{course_key} is excluded from degree credit for this program "
                    "by a program rule, so it cannot be allocated."
                ),
                course_key=course_key,
            )
        )

    for requirement in _walk(audit.requirements):
        if any(c.course_string == course_key for c in requirement.eligible_not_allocated):
            decision.append(
                DecisionFact(
                    statement=(
                        f"{course_key} is eligible for {requirement.requirement_name} "
                        f"({requirement.requirement_code}) but was not allocated to it. "
                        f"That requirement is currently "
                        f"{requirement.status.value.replace('_', ' ')}: "
                        f"{requirement.reason}"
                    ),
                    course_key=course_key,
                    requirement_code=requirement.requirement_code,
                )
            )

    if not decision and any(
        c.course_string == course_key for c in audit.unallocated_courses
    ):
        decision.append(
            DecisionFact(
                statement=(
                    f"{course_key} is on the student's record but was not allocated "
                    "to any requirement in this audit."
                ),
                course_key=course_key,
            )
        )

    notes = (
        ()
        if decision
        else (
            f"The audit recorded no deterministic reason regarding {course_key}. "
            "No explanation can be grounded, and none will be generated.",
        )
    )

    return ExplanationEvidence(
        explanation_type=ExplanationType.WHY_NOT_RECOMMENDED,
        course_key=course_key,
        course_title=document.title if document else "",
        decision_facts=tuple(decision),
        course_facts=_course_facts(document, course_key),
        notes=notes,
    )


__all__ = [
    "SOURCE_DEGREE_ENGINE",
    "SOURCE_REQUIREMENT_MODEL",
    "CourseFact",
    "DecisionFact",
    "ExplanationEvidence",
    "ExplanationType",
    "RequirementFact",
    "build_not_recommended_evidence",
    "build_recommendation_evidence",
]
