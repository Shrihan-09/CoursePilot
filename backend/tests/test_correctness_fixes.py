"""Correctness fixes from the owner's plan review - pure rules (no database).

Item 6: one credit range; item 8: school-only restrictions; item 10: the
"Consider Light" suggestion; item 12: free electives as course slots.
Fabricated data only.
"""

from __future__ import annotations

import importlib.util
import pathlib
from decimal import Decimal
from types import SimpleNamespace as NS

from app.domain.planning import Commitments, GreekLife, PlanningConstraints, WorkloadLevel
from app.domain.planning_profile import PlanningProfileInput, credit_range, to_constraints
from app.services.planning.workload import LEVELS

# ---- item 6 ---------------------------------------------------------------


def test_a_workload_level_range_wins_over_the_old_target_and_maximum():
    profile = PlanningProfileInput(target_credits_per_term=15, max_credits_per_term=18,
                                   workload_level=WorkloadLevel.SEMI_RIGOROUS)
    c = to_constraints(profile)
    assert (c.target_credits_per_term, c.max_credits_per_term) == (Decimal(16), Decimal(19))
    assert credit_range(profile) == (Decimal(16), Decimal(19))


def test_the_students_own_range_wins_over_the_level():
    profile = PlanningProfileInput(workload_level=WorkloadLevel.SEMI_RIGOROUS,
                                   credit_min=13, credit_max=15)
    c = to_constraints(profile)
    assert (c.target_credits_per_term, c.max_credits_per_term) == (Decimal(13), Decimal(15))


def test_without_a_level_the_target_and_maximum_are_the_range():
    c = to_constraints(PlanningProfileInput(target_credits_per_term=15, max_credits_per_term=18))
    assert (c.target_credits_per_term, c.max_credits_per_term) == (Decimal(15), Decimal(18))
    assert credit_range(PlanningProfileInput()) is None


def test_the_migration_copy_of_the_level_ranges_matches_the_planner():
    path = next((pathlib.Path(__file__).resolve().parents[1] / "app" / "db" / "migrations"
                 / "versions").glob("*_cf01_level_range_and_major_subject.py"))
    spec = importlib.util.spec_from_file_location("cf01", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    expected = {level.value: (int(f), int(c)) for level, (f, c, _) in LEVELS.items()}
    assert expected == module.LEVEL_RANGES


def test_the_own_subject_preference_defaults_on_and_reaches_the_planner():
    assert to_constraints(PlanningProfileInput()).major_subject_each_term is True
    off = PlanningProfileInput(major_subject_each_term=False)
    assert to_constraints(off).major_subject_each_term is False


# ---- item 8 ---------------------------------------------------------------


def _engine(units):
    from app.services.planning.engine import PlanningEngine

    engine = PlanningEngine.__new__(PlanningEngine)
    engine.attributes = NS(unit_codes=frozenset(units))
    return engine


def _r(kind, code, unit=None):
    return NS(kind=kind, code=code, unit_code=unit)


def test_a_school_only_restriction_is_closed_to_a_student_of_another_school():
    sections = {"s1": [_r("unit", "14"), _r("unit_major", "332", "14")], "s2": [_r("unit", "14")]}
    assert _engine({"01"})._school_closed(sections) == ["14"]


def test_one_open_section_or_a_matching_school_keeps_it_open():
    assert _engine({"01"})._school_closed({"s1": [_r("unit", "14")], "s2": []}) is None
    assert _engine({"14"})._school_closed({"s1": [_r("unit", "14")]}) is None


def test_a_major_restriction_is_never_decided_as_school_closed():
    """A student may declare by then - majors are never borrowed from history."""
    assert _engine({"01"})._school_closed({"s1": [_r("major", "198")]}) is None
    assert _engine(set())._school_closed({"s1": [_r("unit", "14")]}) is None


# ---- item 10 --------------------------------------------------------------


def _term(code, total, budget, base, hours=19):
    return NS(term_code=code, workload=NS(total=Decimal(total), budget=Decimal(budget),
                                          base_budget=Decimal(base), commitment_hours=hours))


RUSH = PlanningConstraints(workload_level=WorkloadLevel.SEMI_RIGOROUS,
                           commitments=Commitments(job_hours_per_week=8,
                                                   greek_life=GreekLife.RUSHING))


def test_consider_light_when_every_over_budget_term_is_over_because_of_commitments():
    from app.services.planning.service import lighter_term_suggestion

    plan = NS(terms=[_term("20271", "22", "17.5", "24"), _term("20279", "20.5", "17.5", "24"),
                     _term("20281", "17", "17.5", "24")])
    o = lighter_term_suggestion(plan, RUSH)
    assert o.code == "consider_light_term" and o.term_code == "20271"
    assert o.message.startswith("Consider Light for your rush semester (Spring 2027)")
    floor, cap, _ = LEVELS[WorkloadLevel.LIGHT]
    assert (o.details["credit_min"], o.details["credit_max"]) == (int(floor), int(cap))
    assert o.details["over_budget_terms"] == ["20271", "20279"]


def test_no_suggestion_when_a_term_is_over_its_own_level_budget_too():
    from app.services.planning.service import lighter_term_suggestion

    plan = NS(terms=[_term("20271", "26", "17.5", "24")])
    assert lighter_term_suggestion(plan, RUSH) is None


def test_no_suggestion_without_commitments_or_already_light_or_answered():
    from app.domain.planning import TermPlanningOverride
    from app.services.planning.service import lighter_term_suggestion

    over = NS(terms=[_term("20271", "22", "17.5", "24")])
    assert lighter_term_suggestion(NS(terms=[_term("20271", "22", "17.5", "24", hours=0)]),
                                   RUSH) is None
    assert lighter_term_suggestion(over, RUSH.model_copy(
        update={"workload_level": WorkloadLevel.LIGHT})) is None
    answered = RUSH.model_copy(update={"term_overrides": [
        TermPlanningOverride(term_code="20271", credit_min=12, credit_max=14)]})
    assert lighter_term_suggestion(over, answered) is None
    assert lighter_term_suggestion(NS(terms=[]), RUSH) is None


# ---- item 12 --------------------------------------------------------------


def test_free_elective_credits_are_course_slots_with_correct_grammar():
    from app.services.planning.engine import elective_slots

    assert elective_slots(Decimal(1)) == "one 1-credit elective"
    assert elective_slots(Decimal(3)) == "one ~3-credit elective"
    assert elective_slots(Decimal(4)) == "one 4-credit elective"
    assert elective_slots(Decimal(6)) == "two electives (about 6 credits)"
    assert elective_slots(Decimal(0)) == "no elective"
