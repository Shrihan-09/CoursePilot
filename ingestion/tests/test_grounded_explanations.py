"""Grounded explanations (Phase 5.1).

    CoursePilot computes the answer.
    Retrieval supplies evidence.
    The LLM explains the answer.

These tests defend that direction. The load-bearing ones are the rejection
tests: a model response that contradicts the audit must be discarded, not
blended, and the deterministic explanation - which was correct all along -
is returned instead.

Every test here runs with NO model configured or with a scripted one. There
is no network call anywhere in this suite.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from app.domain.audit import (
    Allocation,
    AuditStatus,
    CourseRef,
    DegreeAuditResult,
    RequirementResult,
    RequirementStatus,
)
from app.services.explanations import (
    ExplanationType,
    NoModel,
    RecommendationExplanationService,
    ScriptedModel,
    build_not_recommended_evidence,
    build_recommendation_evidence,
    deterministic_explanation,
    render_context,
    validate_response,
)
from app.services.search.documents import CourseDocument, Provenance

COURSE = "01:198:344"
TITLE = "DESIGN AND ANALYSIS OF COMPUTER ALGORITHMS"


def _document(*, description: str | None = None) -> CourseDocument:
    return CourseDocument(
        document_id=f"{COURSE}|",
        course_key=COURSE,
        subject_code="198",
        subject_name="Computer Science",
        course_number="344",
        title=TITLE,
        description=description,
        credits=Decimal("4"),
        level="U",
        course_provenance=Provenance(source_kind="rutgers_soc"),
        description_provenance=(
            Provenance(source_kind="rutgers_catalog", catalog_year="2026-2027")
            if description
            else None
        ),
    )


def _audit(
    *,
    allocated: bool = True,
    shared: list[str] | None = None,
    eligible_not_allocated: bool = False,
    excluded: bool = False,
) -> DegreeAuditResult:
    ref = CourseRef(
        course_id="c1", course_string=COURSE, title=TITLE, credits=Decimal("4")
    )
    requirement = RequirementResult(
        requirement_code="CS_ELECTIVES",
        requirement_name="Computer Science Electives",
        requirement_type="choose_n",
        status=RequirementStatus.SATISFIED,
        needed_count=5,
        satisfied_count=5,
        allocated_courses=[ref] if allocated else [],
        eligible_not_allocated=[ref] if eligible_not_allocated else [],
        children=[],
        reason="5 of 5 courses completed.",
        source_prose="Six electives, at least five at the 300 level or above.",
        curation_status="curated_from_prose",
    )
    return DegreeAuditResult(
        program_name="Computer Science",
        program_code="198",
        degree_type="BA",
        catalog_year="2026-2027",
        status=AuditStatus.IN_PROGRESS,
        credits_completed=Decimal("60"),
        credits_applicable_to_degree=Decimal("60"),
        credits_excluded=Decimal("0"),
        credits_in_progress=Decimal("0"),
        requirements=[requirement],
        rules=[],
        allocation=(
            [
                Allocation(
                    course=ref,
                    requirement_code="CS_ELECTIVES",
                    requirement_name="Computer Science Electives",
                    requirement_system="major",
                    shared_with_systems=shared or [],
                    term_code="20269",
                    status="completed",
                    credits_applied=Decimal("4"),
                    reason=(
                        f"Course {COURSE} is eligible for 'Computer Science "
                        "Electives' and was allocated to it."
                    ),
                )
            ]
            if allocated
            else []
        ),
        sharing_policy="share_across_systems",
        findings=[],
        excluded_courses=[ref] if excluded else [],
        unallocated_courses=[],
        disclaimers=["CoursePilot is a planning aid, not an official degree audit."],
    )


def _service(**kwargs) -> RecommendationExplanationService:
    documents = kwargs.pop("documents", {COURSE: _document(description="Algorithms.")})
    return RecommendationExplanationService(documents_by_key=documents, **kwargs)


# ==========================================================================
# deterministic evidence
# ==========================================================================


def test_evidence_uses_the_audits_own_reason() -> None:
    """The engine has produced deterministic reasons since Phase 3; the
    explanation layer reuses them rather than re-deriving them."""
    evidence = build_recommendation_evidence(_audit(), COURSE, document=_document())

    assert evidence.course_key == COURSE
    statements = [f.statement for f in evidence.decision_facts]
    assert any("was allocated to it" in s for s in statements)
    assert any("CS_ELECTIVES" in (f.requirement_code or "") for f in evidence.decision_facts)


def test_evidence_records_the_resulting_requirement_state() -> None:
    evidence = build_recommendation_evidence(_audit(), COURSE)
    assert any("satisfied" in f.statement for f in evidence.decision_facts)
    assert any("5 of 5" in f.statement for f in evidence.decision_facts)


def test_evidence_records_credits_applied() -> None:
    evidence = build_recommendation_evidence(_audit(), COURSE)
    assert Decimal("4") in {f.credits for f in evidence.decision_facts if f.credits}


def test_evidence_records_sharing_when_the_audit_did() -> None:
    evidence = build_recommendation_evidence(_audit(shared=["core"]), COURSE)
    assert any("also counts toward core" in f.statement for f in evidence.decision_facts)
    assert any("share_across_systems" in f.statement for f in evidence.decision_facts)


def test_evidence_records_baseline_state() -> None:
    """Phase 4.5's baseline is a FACT, not a judgement."""
    evidence = build_recommendation_evidence(
        _audit(), COURSE, baseline_satisfied=frozenset({"CS_ELECTIVES"})
    )
    assert any("already satisfied" in f.statement for f in evidence.decision_facts)


def test_evidence_quotes_requirement_source_prose() -> None:
    evidence = build_recommendation_evidence(_audit(), COURSE)
    assert evidence.requirement_facts
    assert "300 level" in evidence.requirement_facts[0].statement


def test_unallocated_course_produces_no_recommendation_evidence() -> None:
    evidence = build_recommendation_evidence(_audit(allocated=False), COURSE)
    assert evidence.decision_facts == ()
    assert not evidence.is_grounded
    assert any("did not allocate" in n for n in evidence.notes)


# ==========================================================================
# why NOT recommended - only where the engine said something
# ==========================================================================


def test_not_recommended_uses_eligible_not_allocated() -> None:
    evidence = build_not_recommended_evidence(
        _audit(allocated=False, eligible_not_allocated=True), COURSE
    )
    assert evidence.is_grounded
    assert any("eligible for" in f.statement for f in evidence.decision_facts)


def test_not_recommended_uses_program_rule_exclusion() -> None:
    evidence = build_not_recommended_evidence(
        _audit(allocated=False, excluded=True), COURSE
    )
    assert any("excluded from degree credit" in f.statement for f in evidence.decision_facts)


def test_not_recommended_refuses_when_the_engine_said_nothing() -> None:
    """The invention this shape exists to prevent.

    With no deterministic reason, the evidence is ungrounded and no model is
    ever asked - so nothing can produce "it conflicts with your schedule".
    """
    evidence = build_not_recommended_evidence(_audit(allocated=False), "01:640:151")
    assert evidence.decision_facts == ()
    assert not evidence.is_grounded
    assert any("no deterministic reason" in n.lower() for n in evidence.notes)


# ==========================================================================
# provenance
# ==========================================================================


def test_course_facts_carry_their_source() -> None:
    evidence = build_recommendation_evidence(
        _audit(), COURSE, document=_document(description="Sorting and searching.")
    )
    by_label = {f.label: f for f in evidence.course_facts}
    assert by_label["title"].citation() == "rutgers_soc"
    assert by_label["description"].citation() == "rutgers_catalog 2026-2027"


def test_missing_provenance_is_unattributed_never_invented() -> None:
    from app.services.explanations import CourseFact

    assert CourseFact("title", "X", COURSE, None).citation() == "unattributed"


def test_decision_facts_cite_the_degree_engine() -> None:
    evidence = build_recommendation_evidence(_audit(), COURSE)
    assert "coursepilot_degree_audit" in evidence.citations()


def test_citations_are_deduplicated_and_ordered() -> None:
    evidence = build_recommendation_evidence(_audit(), COURSE, document=_document())
    citations = evidence.citations()
    assert len(citations) == len(set(citations))


# ==========================================================================
# deterministic fallback - the path that always works
# ==========================================================================


def test_deterministic_explanation_needs_no_model() -> None:
    outcome = _service().explain_recommendation(_audit(), COURSE)
    assert outcome.used_model is False
    assert outcome.grounded
    assert COURSE in outcome.explanation.summary
    assert outcome.explanation.reasons
    assert outcome.explanation.generated_by == "deterministic"


def test_fallback_still_works_without_a_search_layer() -> None:
    outcome = RecommendationExplanationService(
        documents_by_key={}, searcher=None, model=NoModel()
    ).explain_recommendation(_audit(), COURSE)
    assert outcome.grounded
    assert outcome.explanation.reasons


def test_fallback_states_when_no_description_exists() -> None:
    """The 98% case in the real corpus."""
    service = _service(documents={COURSE: _document(description=None)})
    outcome = service.explain_recommendation(_audit(), COURSE)
    assert any("No catalog description" in x for x in outcome.explanation.limitations)


def test_ungrounded_request_is_refused_not_answered() -> None:
    outcome = _service().explain_recommendation(_audit(allocated=False), COURSE)
    assert not outcome.grounded
    assert "no recorded decision" in outcome.explanation.summary.lower()
    assert outcome.explanation.reasons == ()


def test_rendered_explanation_lists_its_sources() -> None:
    outcome = _service().explain_recommendation(_audit(), COURSE)
    assert "Sources:" in outcome.explanation.render()


# ==========================================================================
# model contract, with mocked responses
# ==========================================================================


def _valid_payload(**overrides) -> str:
    payload = {
        "summary": f"CoursePilot allocated {COURSE} to CS_ELECTIVES.",
        "reasons": ["The requirement is satisfied after this allocation."],
        "course_information": ["title: DESIGN AND ANALYSIS OF COMPUTER ALGORITHMS"],
        "limitations": [],
        "citations": ["coursepilot_degree_audit"],
    }
    payload.update(overrides)
    return json.dumps(payload)


def test_valid_model_response_is_accepted() -> None:
    service = _service(model=ScriptedModel([_valid_payload()]))
    outcome = service.explain_recommendation(_audit(), COURSE)
    assert outcome.used_model is True
    assert outcome.explanation.generated_by == "model"


def test_malformed_json_is_rejected() -> None:
    service = _service(model=ScriptedModel(["not json at all"]))
    outcome = service.explain_recommendation(_audit(), COURSE)
    assert outcome.used_model is False
    assert any("not valid JSON" in p for p in outcome.rejection_problems)


def test_wrong_course_key_is_rejected() -> None:
    service = _service(
        model=ScriptedModel([_valid_payload(summary="CoursePilot allocated 01:640:151.")])
    )
    outcome = service.explain_recommendation(_audit(), COURSE)
    assert outcome.used_model is False
    assert any("course keys not in the decision" in p for p in outcome.rejection_problems)


def test_invented_requirement_code_is_rejected() -> None:
    service = _service(
        model=ScriptedModel([_valid_payload(reasons=["It satisfies CORE_QFR."])])
    )
    outcome = service.explain_recommendation(_audit(), COURSE)
    assert outcome.used_model is False
    assert any("requirement codes not in the evidence" in p for p in outcome.rejection_problems)


def test_wrong_credits_are_rejected() -> None:
    service = _service(
        model=ScriptedModel([_valid_payload(reasons=["It is worth 3 credits."])])
    )
    outcome = service.explain_recommendation(_audit(), COURSE)
    assert outcome.used_model is False
    assert any("credits" in p for p in outcome.rejection_problems)


def test_invented_citation_is_rejected() -> None:
    service = _service(
        model=ScriptedModel([_valid_payload(citations=["Rutgers Registrar 2025"])])
    )
    outcome = service.explain_recommendation(_audit(), COURSE)
    assert outcome.used_model is False
    assert any("not in the evidence" in p for p in outcome.rejection_problems)


@pytest.mark.parametrize(
    "claim",
    [
        "You have completed your degree requirements.",
        "The prerequisite for this course is CS 112.",
        "You only need two more courses to graduate.",
        "This guarantees your graduation.",
    ],
)
def test_unsupported_academic_claims_are_rejected(claim) -> None:
    """The failure that matters most: fluent prose upgrading an allocation
    into an academic verdict the engine never made."""
    service = _service(model=ScriptedModel([_valid_payload(reasons=[claim])]))
    outcome = service.explain_recommendation(_audit(), COURSE)
    assert outcome.used_model is False
    assert any("unsupported academic claim" in p for p in outcome.rejection_problems)


def test_rejected_response_falls_back_rather_than_failing() -> None:
    """A rejected model response must degrade to the correct answer, never
    to an error or a partial blend."""
    service = _service(model=ScriptedModel(["garbage"]))
    outcome = service.explain_recommendation(_audit(), COURSE)
    assert outcome.explanation.generated_by == "deterministic"
    assert outcome.explanation.reasons


def test_model_exception_falls_back() -> None:
    class Exploding:
        name = "exploding"

        def is_available(self):
            return True

        def generate(self, request):
            raise RuntimeError("provider down")

    outcome = _service(model=Exploding()).explain_recommendation(_audit(), COURSE)
    assert outcome.used_model is False
    assert outcome.explanation.reasons


def test_model_is_never_called_for_ungrounded_requests() -> None:
    """No decision, no model. There is nothing to phrase."""
    model = ScriptedModel([_valid_payload()])
    service = _service(model=model)
    service.explain_recommendation(_audit(allocated=False), COURSE)
    assert model.calls == []


# ==========================================================================
# prompt and context
# ==========================================================================


def test_context_labels_decisions_separately_from_catalog_text() -> None:
    evidence = build_recommendation_evidence(
        _audit(), COURSE, document=_document(description="Sorting.")
    )
    rendered = render_context(evidence)
    assert "DECISION FACTS (authoritative" in rendered
    assert "COURSE FACTS" in rendered
    assert "AVAILABLE CITATIONS" in rendered


def test_system_prompt_forbids_the_dangerous_inventions() -> None:
    from app.services.explanations import SYSTEM_PROMPT

    for forbidden in ("prerequisites", "credit values", "requirement", "citation"):
        assert forbidden in SYSTEM_PROMPT.lower()
    assert "AUTHORITATIVE" in SYSTEM_PROMPT


# ==========================================================================
# retrieval integration is FILTERED
# ==========================================================================


def test_only_the_exact_course_context_is_attached() -> None:
    """Part G: a course whose description shares vocabulary is not evidence
    about this decision."""
    from app.services.search.contract import SearchResult

    class WideSearcher:
        name = "wide"

        def search(self, query, *, limit=10):
            return [
                SearchResult(f"{COURSE}|", COURSE, TITLE, 9.0),
                SearchResult("01:640:151|", "01:640:151", "CALC I", 8.0),
            ]

    documents = {
        COURSE: _document(description="Algorithm analysis."),
        "01:640:151": CourseDocument(
            document_id="01:640:151|",
            course_key="01:640:151",
            subject_code="640",
            subject_name="Mathematics",
            course_number="151",
            title="CALC I",
            description="Unrelated description.",
            credits=Decimal("4"),
            level="U",
            course_provenance=Provenance(source_kind="rutgers_soc"),
            description_provenance=Provenance(
                source_kind="rutgers_catalog", catalog_year="2026-2027"
            ),
        ),
    }
    service = RecommendationExplanationService(
        documents_by_key=documents, searcher=WideSearcher(), model=NoModel()
    )
    outcome = service.explain_recommendation(_audit(), COURSE)

    keys = {f.course_key for f in outcome.evidence.source_documents}
    assert keys <= {COURSE}


# ==========================================================================
# the architectural boundary
# ==========================================================================


def test_no_autonomous_recommendation_entry_point() -> None:
    """Every public method requires a course the audit already decided about.

    There is deliberately no "what should I take" method that a model could
    answer.
    """
    public = {
        name
        for name in dir(RecommendationExplanationService)
        if not name.startswith("_")
    }
    assert public == {
        "documents_by_key",
        "searcher",
        "model",
        "explain_recommendation",
        "explain_not_recommended",
        "describe_course",
    }


def test_explanation_carries_no_confidence_or_score() -> None:
    """A field inviting the model to express doubt about a deterministic
    result would be an invitation to average the two."""
    from app.services.explanations import Explanation

    fields = set(Explanation.__dataclass_fields__)
    assert not (fields & {"confidence", "score", "probability", "certainty"})


def test_explanations_package_does_not_import_a_vendor_sdk() -> None:
    import ast
    import pathlib

    from app.services.explanations import evidence, model, service, validation

    for module in (evidence, model, service, validation):
        tree = ast.parse(pathlib.Path(module.__file__).read_text(encoding="utf-8"))
        names: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names += [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.append(node.module)
        for vendor in ("openai", "anthropic", "google", "cohere", "litellm"):
            assert not any(n.split(".")[0] == vendor for n in names), module.__name__


def test_describe_course_makes_no_degree_claim() -> None:
    outcome = _service().describe_course(COURSE)
    assert outcome.evidence.explanation_type is ExplanationType.WHAT_IS_COURSE
    assert outcome.evidence.decision_facts == ()
    text = outcome.explanation.render().lower()
    assert "satisf" not in text and "requirement" not in text
