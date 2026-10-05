"""Planning Engine building blocks (Phase 6.5) - pure, no database.

Prerequisite expressions use the Phase 6.2 IR; the shapes are those of real
SOC prerequisites (01:198:344's nested AND/OR, 01:640:152's alternatives).
"""

from __future__ import annotations

import random
from decimal import Decimal

import pytest

from app.domain.audit import DegreeAuditResult, RequirementResult, RequirementStatus
from app.domain.planning import PlanningConstraints
from app.domain.prerequisites import AllOf, AnyOf, AtLeast, ConcurrentReq, CourseReq, Unsupported
from app.services.planning.dependencies import UNREADABLE, DependencyPlanner
from app.services.planning.engine import active_leaves, progress
from app.services.planning.terms import label, next_term, planning_terms, season


def C(key):                                         # noqa: N802
    return CourseReq(key)


# ==========================================================================
# terms
# ==========================================================================


def test_term_arithmetic() -> None:
    assert [next_term(t) for t in ("20259", "20260", "20261", "20267")] == \
        ["20260", "20261", "20267", "20269"]
    assert label("20279") == "Fall 2027" and label("20270") == "Winter 2027"
    assert season("20271") == "1"


def test_planning_terms_skip_optional_sessions_by_default() -> None:
    assert planning_terms("20271", 4, include_summer=False, include_winter=False) == \
        ["20271", "20279", "20281", "20289"]
    assert planning_terms("20271", 4, include_summer=True, include_winter=True) == \
        ["20271", "20277", "20279", "20280"]


def test_constraints_are_bounded_settings_not_policy() -> None:
    assert PlanningConstraints().max_credits_per_term == Decimal("15")
    with pytest.raises(ValueError):
        PlanningConstraints(max_credits_per_term=Decimal("30"))
    with pytest.raises(ValueError):
        PlanningConstraints(source="rutgers_policy")             # no such field


# ==========================================================================
# dependency paths: AND/OR preserved, one branch, cycles, depth
# ==========================================================================


def _planner(graph, available=(), contributes=(), plannable=None, **kw):
    return DependencyPlanner(expression_of=lambda k: graph.get(k),
                             available=set(available),
                             contributes=lambda k: k in set(contributes),
                             plannable=plannable or (lambda k: None), **kw)


def test_and_needs_every_child_or_needs_one() -> None:
    graph = {"T": AllOf((C("A"), AnyOf((C("B"), C("D"))))), "B": C("X")}
    path = _planner(graph).path_for("T")
    assert path.feasible and path.courses == ("A", "D")          # D: fewer courses than X, B


def test_and_or_never_unions_alternatives() -> None:
    """(A and B) or C -> {C} (cheaper) - never {A, B, C}."""
    graph = {"T": AnyOf((AllOf((C("A"), C("B"))), C("C")))}
    assert _planner(graph).path_for("T").courses == ("C",)


def test_alternatives_prefer_degree_progress_then_canonical_text() -> None:
    graph = {"T": AnyOf((C("A"), C("B")))}
    assert _planner(graph).path_for("T").courses == ("A",)                  # canonical
    assert _planner(graph, contributes={"B"}).path_for("T").courses == ("B",)


def test_at_least_takes_the_n_cheapest() -> None:
    graph = {"T": AtLeast(2, (C("A"), C("B"), C("D"))), "A": C("Z")}
    assert _planner(graph).path_for("T").courses == ("B", "D")


def test_prerequisites_come_before_the_courses_that_need_them() -> None:
    graph = {"T": C("C"), "C": C("B"), "B": C("A")}
    assert _planner(graph).path_for("T").courses == ("A", "B", "C")


def test_available_courses_cost_nothing() -> None:
    graph = {"T": AllOf((C("A"), C("B")))}
    assert _planner(graph, available={"A"}).path_for("T").courses == ("B",)


def test_a_retake_is_planned_when_the_recorded_grade_does_not_count() -> None:
    graph = {"T": C("A")}
    planner = _planner(graph, available={"A"})
    assert planner.path_for("T", frozenset({"A"})).courses == ("A",)
    assert planner.path_for("T").courses == ()                  # state restored


def test_unsupported_or_unreadable_or_unplannable_is_infeasible() -> None:
    assert not _planner({"T": Unsupported("placement", "PLACEMENT")}).path_for("T").feasible
    assert not _planner({"T": C("A"), "A": UNREADABLE}).path_for("T").feasible
    blocked = _planner({"T": C("A")}, plannable=lambda k: "no_offering_evidence")
    result = blocked.path_for("T")
    assert not result.feasible and result.reason == "no_offering_evidence:A"


def test_an_unplannable_alternative_is_skipped_not_fatal() -> None:
    """Newark's 21:640:113+114 OR New Brunswick's 01:640:112: plan 112."""
    graph = {"T": AnyOf((AllOf((C("21:640:113"), C("21:640:114"))), C("01:640:112")))}
    planner = _planner(graph, plannable=lambda k: "not_in_catalog" if k.startswith("21:") else None)
    assert planner.path_for("T").courses == ("01:640:112",)


def test_corequisite_leaves_are_dependencies_too() -> None:
    graph = {"T": AnyOf((C("A"), ConcurrentReq("B", True)))}
    assert _planner(graph, available={"B"}).path_for("T").courses == ()


def test_cycles_are_reported_not_followed() -> None:
    graph = {"T": C("A"), "A": C("B"), "B": C("A")}
    result = _planner(graph).path_for("T")
    assert not result.feasible and result.reason == "dependency_cycle"
    assert result.cycle == ("A", "B", "A")


def test_depth_is_bounded() -> None:
    graph = {f"K{i}": C(f"K{i + 1}") for i in range(20)}
    result = _planner(graph, max_depth=5).path_for("K0")
    assert not result.feasible and result.reason == "depth_limit"


def test_paths_do_not_depend_on_dictionary_order() -> None:
    children = [C(k) for k in "QWERTY"]
    seen = set()
    for seed in range(10):
        random.Random(seed).shuffle(children)
        graph = {"T": AnyOf(tuple(children))}
        seen.add(_planner(graph).path_for("T").courses)
    assert seen == {("E",)}


# ==========================================================================
# reading the Degree Engine
# ==========================================================================


def _node(code, rtype, status, children=(), **kw):
    return RequirementResult(requirement_code=code, requirement_name=code, requirement_type=rtype,
                             status=status, children=list(children), reason="r", **kw)


S, U, P = (RequirementStatus.SATISFIED, RequirementStatus.UNSATISFIED,
           RequirementStatus.PARTIALLY_SATISFIED)


def test_active_leaves_plan_only_the_alternatives_still_needed() -> None:
    tree = _node("ROOT", "all_of", U, [
        _node("DONE", "course", S, needed_count=1, satisfied_count=1),
        _node("EITHER", "any_of", U, [
            _node("ALT_B", "choose_n", U, needed_count=2),
            _node("ALT_A", "choose_n", P, needed_count=2, satisfied_count=1),
        ], needed_count=1),
        _node("GPA", "course", RequirementStatus.NOT_EVALUABLE),
    ])
    assert [n.requirement_code for n in active_leaves([tree])] == ["ALT_A"]


def test_progress_counts_only_useful_slots() -> None:
    """Six electives, three at the 300 level: a fourth 200-level course is not
    progress; a 300-level course is."""
    class Req:
        max_outside_subject = None
        constraint_subject_code = "920"
        min_at_level, min_at_level_count = 300, 3

    def result(numbers):
        from app.domain.audit import CourseRef

        refs = [CourseRef(course_id="x", course_string=f"01:920:{n}") for n in numbers]
        node = _node("E", "choose_n", P, needed_count=6, satisfied_count=len(refs),
                     allocated_courses=refs)
        return DegreeAuditResult(program_name="p", program_code="c", degree_type="BA",
                                 catalog_year="2026-2027", status="in_progress",
                                 requirements=[node])

    reqs = {"E": Req()}
    three = progress(result([201, 202, 203]), reqs)
    assert progress(result([201, 202, 203, 204]), reqs) == three
    assert progress(result([201, 202, 203, 301]), reqs) > three
