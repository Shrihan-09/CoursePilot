"""The explanation service (Phase 5.1, Parts C, F, G, L and M).

## The pipeline

```
existing deterministic recommendation
        |
        v
evidence from the audit            (no model)
        |
        v
retrieve supporting course context (Phase 5.0, reused)
        |
        v
deterministic explanation          <- already complete and correct here
        |
        v
optional model rephrasing -> validated -> accepted, or discarded
```

The order matters. A correct explanation exists **before** any model is
consulted, so the model is an improvement in fluency and never a dependency
for correctness. If it is missing, misconfigured, slow, or wrong, the answer
is still right.

## No autonomous recommendation

There is no entry point here that takes "what should I take?" and returns a
course. Every method requires a course the audit already decided about. The
recommendation exists first; this layer explains it.

## Retrieval is filtered, not dumped

Part G forbids retrieving loosely related courses and pushing them at a
model. Context is fetched for the **exact course key**, and any result that
is not that course is discarded - a course whose description happens to
share vocabulary is not evidence about this decision.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.domain.audit import DegreeAuditResult
from app.services.explanations.evidence import (
    CourseFact,
    ExplanationEvidence,
    ExplanationType,
    build_not_recommended_evidence,
    build_recommendation_evidence,
)
from app.services.explanations.model import (
    SYSTEM_PROMPT,
    ExplanationModel,
    ModelRequest,
    NoModel,
    render_context,
)
from app.services.explanations.validation import Explanation, validate_response
from app.services.search.contract import CourseSearcher
from app.services.search.documents import CourseDocument

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ExplanationOutcome:
    """The explanation plus an honest account of how it was produced."""

    explanation: Explanation
    evidence: ExplanationEvidence
    used_model: bool
    #: Why the model's output was not used, when it was not.
    rejection_problems: tuple[str, ...] = ()

    @property
    def grounded(self) -> bool:
        return self.evidence.is_grounded


def deterministic_explanation(evidence: ExplanationEvidence) -> Explanation:
    """Build an explanation with no model at all.

    This is the baseline the whole phase rests on. It is assembled by
    concatenating facts the engine already produced - which is why "no LLM"
    degrades the prose and never the accuracy.
    """
    if not evidence.is_grounded:
        return Explanation(
            summary=(
                f"CoursePilot has no recorded decision about {evidence.course_key} "
                "to explain."
            ),
            limitations=evidence.notes
            or ("No deterministic evidence was available for this request.",),
            citations=evidence.citations(),
        )

    if evidence.explanation_type is ExplanationType.WHAT_IS_COURSE:
        summary = f"{evidence.course_key} {evidence.course_title}".strip()
    elif evidence.explanation_type is ExplanationType.WHY_NOT_RECOMMENDED:
        summary = (
            f"CoursePilot did not allocate {evidence.course_key} in this audit."
        )
    else:
        codes = ", ".join(evidence.requirement_codes) or "your degree requirements"
        summary = (
            f"CoursePilot allocated {evidence.course_key} "
            f"{evidence.course_title} to {codes}."
        ).replace("  ", " ")

    course_info = [
        f"{fact.label}: {fact.value} [{fact.citation()}]"
        for fact in (*evidence.course_facts, *evidence.source_documents)
    ]
    limitations = list(evidence.notes)
    # Specifically a DESCRIPTION, not "any course fact": a course with a
    # title and credits but no catalog text is the 98% case in the real
    # corpus, and the explanation should say so rather than look complete.
    has_description = any(
        fact.label in ("description", "catalog description")
        for fact in (*evidence.course_facts, *evidence.source_documents)
    )
    if not has_description:
        limitations.append(
            "No catalog description is available for this course in the "
            "ingested data."
        )

    return Explanation(
        summary=summary,
        reasons=tuple(fact.statement for fact in evidence.decision_facts),
        course_information=tuple(course_info),
        limitations=tuple(limitations),
        citations=evidence.citations(),
    )


@dataclass(slots=True)
class RecommendationExplanationService:
    """Explains decisions the Degree Engine already made."""

    documents_by_key: dict[str, CourseDocument] = field(default_factory=dict)
    searcher: CourseSearcher | None = None
    model: ExplanationModel = field(default_factory=NoModel)

    # ------------------------------------------------------------------ #
    # public entry points - each REQUIRES an existing decision
    # ------------------------------------------------------------------ #

    def explain_recommendation(
        self,
        audit: DegreeAuditResult,
        course_key: str,
        *,
        explanation_type: ExplanationType = ExplanationType.WHY_RECOMMENDED,
        baseline_satisfied: frozenset[str] = frozenset(),
    ) -> ExplanationOutcome:
        evidence = build_recommendation_evidence(
            audit,
            course_key,
            explanation_type=explanation_type,
            document=self.documents_by_key.get(course_key),
            baseline_satisfied=baseline_satisfied,
        )
        evidence = self._attach_context(evidence)
        return self._finish(evidence)

    def explain_not_recommended(
        self, audit: DegreeAuditResult, course_key: str
    ) -> ExplanationOutcome:
        evidence = build_not_recommended_evidence(
            audit, course_key, document=self.documents_by_key.get(course_key)
        )
        evidence = self._attach_context(evidence)
        return self._finish(evidence)

    def describe_course(self, course_key: str) -> ExplanationOutcome:
        """A pure retrieval question - no degree audit involved.

        Kept separate from the recommendation paths precisely so it cannot be
        mistaken for one: describing a course says nothing about whether it
        counts toward anything.
        """
        document = self.documents_by_key.get(course_key)
        evidence = ExplanationEvidence(
            explanation_type=ExplanationType.WHAT_IS_COURSE,
            course_key=course_key,
            course_title=document.title if document else "",
            course_facts=(
                build_recommendation_evidence(
                    _EMPTY_AUDIT, course_key, document=document
                ).course_facts
            ),
            notes=(
                ()
                if document
                else (f"No ingested course record matches {course_key}.",)
            ),
        )
        return self._finish(evidence)

    # ------------------------------------------------------------------ #
    # internals
    # ------------------------------------------------------------------ #

    def _attach_context(self, evidence: ExplanationEvidence) -> ExplanationEvidence:
        """Retrieve supporting text for THIS course only.

        Results for other courses are dropped. Part G is explicit that
        similar-sounding courses are not evidence, and a bounded context of
        the wrong courses is still the wrong context.
        """
        if self.searcher is None:
            return evidence

        results = self.searcher.search(evidence.course_key, limit=5)
        snippets: list[CourseFact] = []
        for result in results:
            if result.course_key != evidence.course_key:
                continue
            document = self.documents_by_key.get(result.course_key)
            if document is None or not document.has_description:
                continue
            snippets.append(
                CourseFact(
                    label="catalog description",
                    value=document.description or "",
                    course_key=document.course_key,
                    provenance=document.description_provenance,
                )
            )

        # Deduplicate against facts already carried directly.
        existing = {(f.label, f.value) for f in evidence.course_facts}
        snippets = [s for s in snippets if (s.label, s.value) not in existing]
        if not snippets:
            return evidence
        return ExplanationEvidence(
            explanation_type=evidence.explanation_type,
            course_key=evidence.course_key,
            course_title=evidence.course_title,
            decision_facts=evidence.decision_facts,
            course_facts=evidence.course_facts,
            requirement_facts=evidence.requirement_facts,
            source_documents=tuple(snippets),
            notes=evidence.notes,
        )

    def _finish(self, evidence: ExplanationEvidence) -> ExplanationOutcome:
        """Attempt the model; fall back to the deterministic explanation.

        Instrumented in Phase 5.10 so an operator can tell WHY explanations
        are deterministic - an unconfigured provider, a failing provider and
        a provider whose output keeps failing validation look identical from
        the outside and need completely different responses.

        Every path here returns a correct explanation. The counters describe
        how it was produced, never whether it is trustworthy: the
        deterministic output is always the authority.
        """
        from app.core.metrics import (
            EXPLANATION_DETERMINISTIC,
            EXPLANATION_MODEL_ATTEMPTED,
            EXPLANATION_MODEL_FAILED,
            EXPLANATION_MODEL_REJECTED,
            EXPLANATION_MODEL_SUCCEEDED,
            EXPLANATION_NOT_GROUNDED,
            EXPLANATION_PROVIDER_UNAVAILABLE,
            STAGE_MODEL_CALL,
            STAGE_MODEL_VALIDATION,
            get_metrics,
        )
        from app.core.observability import stage

        metrics = get_metrics()
        baseline = deterministic_explanation(evidence)

        if not evidence.is_grounded:
            # No decision facts to explain. Counted apart from a provider
            # problem because the fix is data, not infrastructure.
            metrics.increment(EXPLANATION_NOT_GROUNDED)
            metrics.increment(EXPLANATION_DETERMINISTIC)
            return ExplanationOutcome(baseline, evidence, used_model=False)

        if not self.model.is_available():
            metrics.increment(EXPLANATION_PROVIDER_UNAVAILABLE)
            metrics.increment(EXPLANATION_DETERMINISTIC)
            return ExplanationOutcome(baseline, evidence, used_model=False)

        metrics.increment(EXPLANATION_MODEL_ATTEMPTED)
        try:
            with stage(STAGE_MODEL_CALL):
                raw = self.model.generate(
                    ModelRequest(
                        system_prompt=SYSTEM_PROMPT,
                        context=render_context(evidence),
                        explanation_type=evidence.explanation_type,
                    )
                )
        except Exception as exc:
            # A provider failure must never surface as a failed explanation.
            #
            # Only the exception CLASS is logged. `logger.exception` here
            # would ship a traceback whose frames and message can quote the
            # rendered context - which is the student's academic evidence.
            # The provider already translates vendor errors to a class-only
            # message; this makes the guarantee local rather than inherited.
            metrics.increment(EXPLANATION_MODEL_FAILED)
            metrics.increment(EXPLANATION_DETERMINISTIC)
            logger.warning(
                "explanation_model_failed",
                extra={"error_type": type(exc).__name__},
            )
            return ExplanationOutcome(baseline, evidence, used_model=False)

        with stage(STAGE_MODEL_VALIDATION):
            result = validate_response(raw, evidence)
        if not result.ok or result.explanation is None:
            # The rejection REASONS are a fixed vocabulary produced by the
            # validator, so they are safe to log; the course key and the
            # model text are not, and are not logged.
            metrics.increment(EXPLANATION_MODEL_REJECTED)
            metrics.increment(EXPLANATION_DETERMINISTIC)
            logger.warning(
                "explanation_model_rejected",
                extra={"problems": list(result.problems)},
            )
            return ExplanationOutcome(
                baseline, evidence, used_model=False, rejection_problems=result.problems
            )

        metrics.increment(EXPLANATION_MODEL_SUCCEEDED)
        return ExplanationOutcome(result.explanation, evidence, used_model=True)


#: An audit with nothing in it, used only to reuse the course-fact builder for
#: a pure description request. Never exposed.
_EMPTY_AUDIT = DegreeAuditResult(
    program_name="",
    program_code="",
    degree_type="",
    catalog_year="",
    status="insufficient_data",
    credits_completed=0,
    credits_applicable_to_degree=0,
    credits_excluded=0,
    credits_in_progress=0,
    requirements=[],
    rules=[],
    allocation=[],
    sharing_policy="exclusive",
    findings=[],
    excluded_courses=[],
    unallocated_courses=[],
    disclaimers=[],
)


__all__ = [
    "ExplanationOutcome",
    "RecommendationExplanationService",
    "deterministic_explanation",
]
