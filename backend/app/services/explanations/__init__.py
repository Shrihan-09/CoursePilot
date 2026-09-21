"""Grounded explanation of deterministic CoursePilot decisions (Phase 5.1).

    CoursePilot computes the answer.
    Retrieval supplies evidence.
    The LLM explains the answer.

The model is downstream of academic reasoning and can never be upstream of
it. A correct explanation exists before any model is called.
"""

from app.services.explanations.evidence import (
    CourseFact,
    DecisionFact,
    ExplanationEvidence,
    ExplanationType,
    RequirementFact,
    build_not_recommended_evidence,
    build_recommendation_evidence,
)
from app.services.explanations.model import (
    SYSTEM_PROMPT,
    ExplanationModel,
    ModelRequest,
    NoModel,
    ScriptedModel,
    render_context,
)
from app.services.explanations.service import (
    ExplanationOutcome,
    RecommendationExplanationService,
    deterministic_explanation,
)
from app.services.explanations.validation import (
    Explanation,
    ValidationResult,
    validate_response,
)

__all__ = [
    "SYSTEM_PROMPT",
    "CourseFact",
    "DecisionFact",
    "Explanation",
    "ExplanationEvidence",
    "ExplanationModel",
    "ExplanationOutcome",
    "ExplanationType",
    "ModelRequest",
    "NoModel",
    "RecommendationExplanationService",
    "RequirementFact",
    "ScriptedModel",
    "ValidationResult",
    "build_not_recommended_evidence",
    "build_recommendation_evidence",
    "deterministic_explanation",
    "render_context",
    "validate_response",
]
