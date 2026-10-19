"""Phase 7.2.1: pace, target vs hard maximum, and validated levelling.

Same REAL archived SOC records and curated programs as test_planning_engine.py.
Every paced plan must still pass that file's invariants (no UNKNOWN placed,
evidence for every term, prerequisites strictly earlier, caps respected) AND
the independent order oracle `_verify_order`, which re-runs the prerequisite
check for every course against its (possibly moved) term.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from app.domain.planning import Pace, PlanningConstraints, PlanStatus

from .test_planning_engine import (  # noqa: F401
    CS_DONE,
    _invariants,
    _plan,
    _student,
    _verify_order,
    cs,
    math,
)

D = Decimal


def _cons(pace, target, maximum, courses=5, terms=8):
    return PlanningConstraints(target_credits_per_term=D(target), max_credits_per_term=D(maximum),
                               max_courses_per_term=courses, max_terms=terms, pace=pace)


def _loads(plan):
    return [t.credits for t in plan.terms]


EARLY = [("01:198:111", "20259"), ("01:640:151", "20259")]


@pytest.mark.parametrize("pace", list(Pace))
def test_every_pace_keeps_every_invariant(cs, pace) -> None:
    student = _student(cs, EARLY)
    plan = _plan(cs, student, constraints=_cons(pace, 12, 15))
    _invariants(plan)
    _verify_order(cs, student, plan)
    assert all(t.credits <= D(15) for t in plan.terms)             # the HARD maximum
    assert plan.metadata.constraints.pace is pace


def test_lighter_never_exceeds_the_target(cs) -> None:
    student = _student(cs, EARLY)
    plan = _plan(cs, student, constraints=_cons(Pace.LIGHTER, 9, 18, terms=12))
    _invariants(plan)
    assert all(t.credits <= D(9) for t in plan.terms), _loads(plan)


def test_the_target_is_not_a_minimum(cs) -> None:
    """Terms may be lighter than the target - the final one in particular."""
    student = _student(cs, [*CS_DONE, ("01:198:344", "20269", "in_progress")])
    plan = _plan(cs, student, constraints=_cons(Pace.BALANCED, 15, 18))
    _invariants(plan)
    assert plan.status is PlanStatus.COVERS_ALL_REQUIREMENTS
    assert any(t.credits < D(15) for t in plan.terms)               # nothing was padded


def test_finish_earlier_never_takes_more_terms_than_balanced_or_lighter(cs) -> None:
    student = _student(cs, EARLY)
    terms = {pace: len(_plan(cs, student, constraints=_cons(pace, 9, 15, terms=12)).terms)
             for pace in Pace}
    assert terms[Pace.FINISH_EARLIER] <= terms[Pace.BALANCED] <= terms[Pace.LIGHTER], terms


def test_balanced_is_the_choice_not_the_capability(cs) -> None:
    """A student who COULD finish sooner stays BALANCED: the plan is not
    compressed for them; at most an observation says it is possible."""
    student = _student(cs, EARLY)
    balanced = _plan(cs, student, constraints=_cons(Pace.BALANCED, 9, 15, terms=12))
    earlier = _plan(cs, student, constraints=_cons(Pace.FINISH_EARLIER, 9, 15, terms=12))
    assert balanced.metadata.constraints.pace is Pace.BALANCED
    if len(earlier.terms) < len(balanced.terms) and \
            balanced.status is PlanStatus.COVERS_ALL_REQUIREMENTS:
        obs = [o for o in balanced.observations if o.code == "earlier_completion_possible"]
        assert obs, "capability not surfaced"
        assert "not a graduation date" in obs[0].message
        assert "currently modeled requirements" in obs[0].message
    for o in balanced.observations:
        assert "will graduate" not in o.message.lower()


def test_paced_plans_are_deterministic(cs) -> None:
    student = _student(cs, EARLY)
    a = _plan(cs, student, constraints=_cons(Pace.BALANCED, 12, 15))
    b = _plan(cs, student, constraints=_cons(Pace.BALANCED, 12, 15))
    assert a.canonical_json() == b.canonical_json()


def test_unpersonalized_requests_keep_the_greedy_behaviour(cs) -> None:
    """pace=None is the Phase 6.5 contract: no levelling, no observations."""
    student = _student(cs, EARLY)
    plan = _plan(cs, student, constraints=PlanningConstraints(max_credits_per_term=D(15)))
    _invariants(plan)
    assert plan.observations == []
    assert plan.metadata.constraints.pace is None


def test_levelling_never_strands_a_course_balanced_could_absorb(cs) -> None:
    """BALANCED never ends on a last term at most half the target if the same
    courses fit earlier within the maximum - it either compresses or reports
    why the term is light."""
    student = _student(cs, [*CS_DONE, ("01:198:344", "20269", "in_progress")])
    plan = _plan(cs, student, constraints=_cons(Pace.BALANCED, 15, 18))
    _invariants(plan)
    last = plan.terms[-1]
    if last.credits * 2 <= D(15) and len(plan.terms) > 1:
        assert any(o.code == "light_term" and o.term_code == last.term_code
                   for o in plan.observations), "a stranded term with no explanation"


def test_math_paced_plan_keeps_invariants(math) -> None:
    student = _student(math, [("01:640:151", "20259"), ("01:640:152", "20261")], program_code="640")
    plan = _plan(math, student, constraints=_cons(Pace.BALANCED, 12, 16))
    _invariants(plan)
    _verify_order(math, student, plan)
