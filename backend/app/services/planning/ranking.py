"""Pure preference boundary. The caller must prove academic validity first.

Preserves dependency/scarcity/offering/eligibility priorities. Broader evidenced
requirement candidacy precedes canonical order; actual allocation remains the
Degree Engine's decision. This is a greedy heuristic, not an optimality claim.
"""

from app.domain.course_ranking import CourseRankingEvidence


def rank_key(course: str, evidence: CourseRankingEvidence) -> tuple:
    return (
        0 if evidence.required_dependency else 1,
        -evidence.downstream_targets,
        evidence.scarcity,
        0 if evidence.confirmed_offering else 1,
        0 if evidence.prerequisites_met_by_history else 1,
        -evidence.candidate_requirement_coverage,
        course,
    )
