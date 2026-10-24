"""Phase 7.2.4 academic strategy: a ranking preference among VALID courses only.

Pure ordering tests use invented course keys; the integration tests run the
real planner on the CS planning world with a fabricated student.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from app.domain.course_ranking import CourseRankingEvidence
from app.domain.planning import EligibilityState, PlanningConstraints, Strategy
from app.services.planning.offerings import PLACEABLE
from app.services.planning.ranking import rank_key
from app.services.planning.service import generate_plan

from .test_planning_engine import CS_DONE, START, _student, cs  # noqa: F401

MAJOR = ("01:999:101", CourseRankingEvidence(serves_major=True))
SCHOOL = ("01:999:102", CourseRankingEvidence(serves_school=True))
UNLOCKS = ("01:999:103", CourseRankingEvidence(unlock_breadth=7))
NEEDED = ("01:999:199", CourseRankingEvidence(required_dependency=True))


def order(strategy, *items):
    return [k for k, _ in sorted(items, key=lambda kv: rank_key(kv[0], kv[1], strategy))]


# G. Major-first changes order only among valid choices -------------------
def test_major_first_prefers_major_requirements():
    assert order(Strategy.BALANCED, SCHOOL, MAJOR)[0] == MAJOR[0]   # canonical tie-break
    assert order(Strategy.MAJOR_FIRST, SCHOOL, MAJOR) == [MAJOR[0], SCHOOL[0]]


# H. Requirements-first behaves the same way for school/Core --------------
def test_requirements_first_prefers_school_requirements():
    assert order(Strategy.REQUIREMENTS_FIRST, MAJOR, SCHOOL) == [SCHOOL[0], MAJOR[0]]


def test_unlock_future_options_prefers_widely_cited_prerequisites():
    assert order(Strategy.UNLOCK_FUTURE_OPTIONS, MAJOR, UNLOCKS)[0] == UNLOCKS[0]


@pytest.mark.parametrize("strategy", list(Strategy))
def test_a_needed_prerequisite_always_outranks_any_strategy(strategy):
    assert order(strategy, MAJOR, SCHOOL, UNLOCKS, NEEDED)[0] == NEEDED[0]


# I. Hard constraints always beat preferences -----------------------------
@pytest.mark.parametrize("strategy", list(Strategy))
def test_every_strategy_keeps_validity_and_hard_limits(cs, strategy):  # noqa: F811
    limits = PlanningConstraints(max_credits_per_term=Decimal(12), max_courses_per_term=3,
                                 strategy=strategy)
    plan = generate_plan(cs, _student(cs, CS_DONE), start_term=START, constraints=limits)
    assert plan.terms, "the fixture must leave something to plan"
    for term in plan.terms:
        assert term.credits <= 12 and len(term.courses) <= 3
        for pc in term.courses:
            assert pc.offering.kind in PLACEABLE
            assert pc.prerequisite.state in (EligibilityState.SATISFIED_BY_HISTORY,
                                             EligibilityState.CONDITIONAL_ON_PLAN)


@pytest.mark.parametrize("strategy", list(Strategy))
def test_strategy_never_places_a_course_for_its_own_sake(cs, strategy):  # noqa: F811
    plan = generate_plan(cs, _student(cs, CS_DONE), start_term=START,
                         constraints=PlanningConstraints(strategy=strategy))
    needed_for = {"required_prerequisite_for", "required_corequisite_for"}
    for pc in (pc for t in plan.terms for pc in t.courses):
        # Serves an open requirement, or is a prerequisite/co-requisite the plan needs.
        assert pc.requirements or needed_for & {r.value for r in pc.reasons}, pc.course
