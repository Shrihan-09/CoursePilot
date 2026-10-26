"""Pure preference boundary. The caller must prove academic validity first.

Preserves dependency/scarcity/offering/eligibility priorities. Broader evidenced
requirement candidacy precedes canonical order; actual allocation remains the
Degree Engine's decision. This is a greedy heuristic, not an optimality claim.

Phase 7.2.4: the student's academic strategy contributes ONE key, after the
dependency priority (a prerequisite the plan needs still comes first) and
before the default evidence order. Only already-valid courses are ranked, so
a strategy can reorder valid choices but never make an invalid one eligible.
"""

from app.domain.course_ranking import CourseRankingEvidence
from app.domain.planning import Strategy


def strategy_key(evidence: CourseRankingEvidence, strategy: Strategy) -> int:
    if strategy is Strategy.MAJOR_FIRST:
        return 0 if evidence.serves_major else 1
    if strategy is Strategy.REQUIREMENTS_FIRST:
        return 0 if evidence.serves_school else 1
    if strategy is Strategy.UNLOCK_FUTURE_OPTIONS:
        return -evidence.unlock_breadth
    return 0


def rank_key(course: str, evidence: CourseRankingEvidence,
             strategy: Strategy = Strategy.BALANCED) -> tuple:
    """Phase 7.6 sequencing: after the dependency and strategy keys, a course
    that starts a LONGER chain of required courses comes first, then a course
    the loaded schedules show in one regular season only (missed, the next
    chance is a year later). Counting toward several requirements at once
    (`candidate_requirement_coverage`, now only as many as the sharing policy
    allows) keeps its place; the workload levels' multi-requirement step is
    where it leads (measured with plan_quality.py: moving it up front-loaded
    Core and pushed required major courses to the last term)."""
    return (
        0 if evidence.required_dependency else 1,
        strategy_key(evidence, strategy),
        -evidence.downstream_targets,
        -evidence.unlock_chain_depth,
        0 if evidence.season_only else 1,
        evidence.scarcity,
        0 if evidence.confirmed_offering else 1,
        0 if evidence.prerequisites_met_by_history else 1,
        -evidence.candidate_requirement_coverage,
        course,
    )
