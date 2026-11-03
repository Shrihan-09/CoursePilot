"""Task 2 planning questionnaire - pure rules, no database.

Pins: the major-related range table, the commitment-hours mapping and the
budget it lowers (never below the credit floor), per-semester overrides,
the pace/level conflict warning, the validation of every answer, fixed
weekly times and the honors answer as Schedule Engine hard limits, and the
wording (no "easy"/"hard"). Planning behaviour on real data is in
ingestion/tests/test_workload_levels.py.
"""

from __future__ import annotations

import re
from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.domain.planning import (
    ClubLoad,
    Commitments,
    GreekLife,
    Pace,
    PlanningConstraints,
    TermPlanningOverride,
    WorkloadLevel,
)
from app.domain.planning_profile import PlanningProfileInput, to_constraints
from app.domain.schedule import (
    BlockedTime,
    RestrictionEntry,
    RestrictionEvidence,
    RestrictionOutcome,
    SchedulePreferences,
)
from app.services.planning.questionnaire import (
    MAJOR_RANGE,
    TermSettings,
    budget_reduction,
    commitment_hours,
    describe_hours,
    pace_level_conflict,
)
from app.services.planning.workload import LEVELS
from app.services.scheduling.candidates import honors_only
from app.services.scheduling.engine import _domains
from app.services.scheduling.meetings import normalize, overlaps_blocked

EASY_HARD = re.compile(r"\b(easy|easier|hard|harder|difficult)\b", re.IGNORECASE)


def M(day, start, end):  # noqa: N802
    return normalize(day=day, start=start, end=end, mode_code="02", mode_desc="LEC",
                     start_date=None, end_date=None, campus="BUS")


# ---------------------------------------------------------------- mapping table


def test_major_related_range_by_level_is_the_decided_table() -> None:
    assert MAJOR_RANGE == {WorkloadLevel.LIGHT: (1, 2), WorkloadLevel.BALANCED: (2, 3),
                           WorkloadLevel.SEMI_RIGOROUS: (3, 3),
                           WorkloadLevel.CHALLENGING: (3, 4)}


@pytest.mark.parametrize("level", list(WorkloadLevel))
def test_term_settings_default_to_the_level(level) -> None:
    floor, _cap, budget = LEVELS[level]
    st = TermSettings(PlanningConstraints(workload_level=level), "20271", floor, budget)
    assert (st.major_min, st.major_max, st.source) == (*MAJOR_RANGE[level], "level")
    assert st.budget == budget and st.hours == 0 and not st.clamped


def test_commitments_map_deterministically_to_hours() -> None:
    c = Commitments(job_hours_per_week=10, greek_life=GreekLife.NEW_MEMBER,
                    clubs=ClubLoad.HEAVY, athletics_hours_per_week=0, research_hours_per_week=5,
                    recruiting_hours_per_week=2)
    assert commitment_hours(c) == {"job": 10, "greek_life": 10, "clubs": 8, "research": 5,
                                   "recruiting": 2}
    assert commitment_hours(Commitments()) == {}
    assert {g: commitment_hours(Commitments(greek_life=g)).get("greek_life", 0)
            for g in GreekLife} == {GreekLife.NONE: 0, GreekLife.RUSHING: 6,
                                    GreekLife.NEW_MEMBER: 10, GreekLife.ACTIVE: 4}
    assert {c: commitment_hours(Commitments(clubs=c)).get("clubs", 0) for c in ClubLoad} == {
        ClubLoad.NONE: 0, ClubLoad.LIGHT: 3, ClubLoad.HEAVY: 8}


@pytest.mark.parametrize(("hours", "points"), [(0, 0), (1, Decimal("0.5")), (3, 1),
                                                (4, Decimal("1.5")), (15, 5), (20, Decimal("6.5"))])
def test_budget_drops_one_point_per_three_hours_to_the_nearest_half(hours, points) -> None:
    assert budget_reduction(hours) == Decimal(points)


def test_commitments_lower_the_budget_but_never_below_the_credit_floor() -> None:
    floor, _cap, budget = LEVELS[WorkloadLevel.SEMI_RIGOROUS]            # 16 / 24
    job = PlanningConstraints(workload_level=WorkloadLevel.SEMI_RIGOROUS,
                              commitments=Commitments(job_hours_per_week=15))
    st = TermSettings(job, "20271", floor, budget)
    assert (st.hours, st.budget, st.base_budget, st.clamped) == (15, Decimal(19), budget, False)
    busy = PlanningConstraints(workload_level=WorkloadLevel.SEMI_RIGOROUS,
                               commitments=Commitments(job_hours_per_week=40,
                                                       athletics_hours_per_week=20))
    st = TermSettings(busy, "20271", floor, budget)
    assert st.budget == floor and st.clamped


def test_describe_hours_names_types_never_organizations() -> None:
    assert describe_hours({"job": 10}) == "10 hours a week of job"
    assert describe_hours({"job": 10, "clubs": 3}) == "13 hours a week: job 10, clubs 3"
    # The model has no field for a name at all.
    with pytest.raises(ValidationError):
        Commitments(job_hours_per_week=5, employer="Acme")


# ---------------------------------------------------------------- per-semester override


def test_a_semester_override_replaces_range_and_commitments_there_only() -> None:
    c = PlanningConstraints(
        workload_level=WorkloadLevel.BALANCED, commitments=Commitments(job_hours_per_week=6),
        term_overrides=[TermPlanningOverride(term_code="20279", major_courses_min=1,
                                             major_courses_max=1,
                                             commitments=Commitments())])
    floor, _cap, budget = LEVELS[WorkloadLevel.BALANCED]
    fall = TermSettings(c, "20279", floor, budget)
    spring = TermSettings(c, "20271", floor, budget)
    assert (fall.major_min, fall.major_max, fall.source, fall.hours) == (1, 1, "override", 0)
    assert (spring.major_min, spring.major_max, spring.source, spring.hours) == (2, 3, "level", 6)
    assert spring.budget == budget - 2


@pytest.mark.parametrize("kw", [dict(major_courses_min=3, major_courses_max=2),
                                dict(major_courses_min=2), dict(major_courses_max=2),
                                dict(major_courses_min=-1, major_courses_max=2),
                                dict(major_courses_min=1, major_courses_max=9)])
def test_override_ranges_are_validated(kw) -> None:
    with pytest.raises(ValidationError):
        TermPlanningOverride(term_code="20271", **kw)


def test_one_override_per_semester() -> None:
    o = TermPlanningOverride(term_code="20271", major_courses_min=1, major_courses_max=2)
    with pytest.raises(ValidationError):
        PlanningConstraints(term_overrides=[o, o])
    with pytest.raises(ValidationError):
        PlanningProfileInput.model_validate({"term_overrides": [
            {"term_code": "20271"}, {"term_code": "20271"}]})


def test_profile_answers_reach_the_planner_and_fixed_times_do_not() -> None:
    """Every question is skippable: an empty profile is the defaults. Fixed
    times shape schedules, not plans, so editing one never makes a plan stale."""
    empty = to_constraints(PlanningProfileInput(workload_level=WorkloadLevel.BALANCED))
    assert empty.commitments == Commitments() and empty.honors is None
    assert empty.term_overrides == []
    profile = PlanningProfileInput.model_validate({
        "workload_level": "semi_rigorous", "honors": True,
        "commitments": {"job_hours_per_week": 12, "greek_life": "active"},
        "schedule": {"blocked_times": [{"kind": "job", "days": ["T", "H"],
                                        "start": "13:00", "end": "17:00"}]},
        "term_overrides": [
            {"term_code": "20279", "major_courses_min": 2, "major_courses_max": 2,
             "blocked_times": [{"kind": "club", "days": ["W"], "start": "18:00", "end": "19:00"}]},
            {"term_code": "20281", "blocked_times": []}]})
    c = to_constraints(profile)
    assert c.honors is True and c.commitments.job_hours_per_week == 12
    assert [o.model_dump() for o in c.term_overrides] == [
        {"term_code": "20279", "major_courses_min": 2, "major_courses_max": 2, "credit_min": None,
         "credit_max": None, "commitments": None}]
    moved = profile.model_copy(update={"schedule": profile.schedule.model_copy(
        update={"blocked_times": []})})
    assert to_constraints(moved) == c


# ---------------------------------------------------------------- pace vs level


def test_pace_stays_separate_but_a_conflict_is_warned() -> None:
    warn = pace_level_conflict(PlanningConstraints(workload_level=WorkloadLevel.LIGHT,
                                                   pace=Pace.FINISH_EARLIER))
    assert warn and "early" in warn and "Light" in warn
    assert pace_level_conflict(PlanningConstraints(workload_level=WorkloadLevel.CHALLENGING,
                                                   pace=Pace.LIGHTER))
    for level in WorkloadLevel:
        assert pace_level_conflict(PlanningConstraints(workload_level=level,
                                                       pace=Pace.BALANCED)) is None
    assert pace_level_conflict(PlanningConstraints(pace=Pace.FINISH_EARLIER)) is None


# ---------------------------------------------------------------- fixed times, honors


def test_blocked_times_are_validated() -> None:
    BlockedTime(kind="job", days=["M"], start="09:00", end="12:00")
    for bad in (dict(days=[], start="09:00", end="10:00"),
                dict(days=["X"], start="09:00", end="10:00"),
                dict(days=["M"], start="12:00", end="09:00"),
                dict(days=["M"], start="9am", end="10:00"),
                dict(kind="Starbucks", days=["M"], start="09:00", end="10:00")):
        with pytest.raises(ValidationError):
            BlockedTime(**bad)


def test_a_meeting_during_a_fixed_block_is_blocked_touching_is_not() -> None:
    shift = [("T", 13 * 60, 17 * 60)]
    assert overlaps_blocked([M("T", "1600", "1720")], shift) == "blocked_times"
    assert overlaps_blocked([M("T", "1200", "1300")], shift) is None        # ends as it starts
    assert overlaps_blocked([M("T", "1700", "1820")], shift) is None
    assert overlaps_blocked([M("W", "1400", "1520")], shift) is None        # another day
    untimed = normalize(day=None, start=None, end=None, mode_code="90",
                        mode_desc="ONLINE INSTRUCTION(INTERNET)")
    assert overlaps_blocked([untimed], shift) is None


def _evidence(*kinds):
    return RestrictionEvidence(
        outcome=RestrictionOutcome.UNKNOWN if kinds else RestrictionOutcome.UNRESTRICTED,
        entries=[RestrictionEntry(kind=k, code="A") for k in kinds])


def test_honors_only_means_every_entry_is_an_honors_program() -> None:
    assert honors_only(_evidence("honor_program"))
    assert honors_only(_evidence("honor_program", "honor_program"))
    assert not honors_only(_evidence())                                  # unrestricted
    assert not honors_only(_evidence("honor_program", "major"))          # also open to a major


def _candidate(index, meetings, *kinds):
    return SimpleNamespace(index_number=index, meetings=meetings, restriction=_evidence(*kinds),
                           time_verified=True,
                           availability=SimpleNamespace(freshness=None, state=None))


def test_schedule_engine_applies_fixed_times_and_the_honors_answer() -> None:
    term = SimpleNamespace(candidates={"01:830:101": [
        _candidate("10001", [M("T", "1400", "1520")]),                     # during the shift
        _candidate("10002", [M("M", "1000", "1120")], "honor_program"),    # honors-only
        _candidate("10003", [M("W", "1000", "1120")]),
    ]})
    shift = BlockedTime(kind="job", days=["T"], start="13:00", end="17:00")
    domains, excluded = _domains(term, SchedulePreferences(blocked_times=[shift], honors=False))
    assert [c.index_number for c in domains["01:830:101"]] == ["10003"]
    assert excluded["01:830:101"] == {"blocked_times": ["10001"], "honors_only": ["10002"]}
    # In an honors program, or not asked: the honors section stays (needs confirmation).
    for honors in (True, None):
        domains, _ = _domains(term, SchedulePreferences(blocked_times=[shift], honors=honors))
        assert {c.index_number for c in domains["01:830:101"]} == {"10002", "10003"}
    prefs = SchedulePreferences(blocked_times=[shift], honors=False)
    assert {"blocked_times", "honors"} <= set(prefs.hard_constraints())
    assert prefs.without("blocked_times").blocked_times == []


# ---------------------------------------------------------------- wording


def test_questionnaire_wording_never_calls_a_course_easy_or_hard() -> None:
    from app.api.v1.routes import personalization as routes
    texts = [routes.COMMITMENTS_TEXT, routes.MAJOR_RELATED_TEXT,
             *(w["description"] for w in routes.WORKLOAD_LEVELS),
             *(g["label"] for g in routes.GREEK_LIFE), *(c["label"] for c in routes.CLUBS)]
    for c in (PlanningConstraints(workload_level=WorkloadLevel.LIGHT, pace=Pace.FINISH_EARLIER),
              PlanningConstraints(workload_level=WorkloadLevel.CHALLENGING, pace=Pace.LIGHTER)):
        texts.append(pace_level_conflict(c))
    assert not [t for t in texts if EASY_HARD.search(t)]


# ---------------------------------------------------------------- Prompt 09


@pytest.mark.parametrize(("answers", "expected"), [
    # (profile answers, (credit floor, cap, major min, max, budget, hours available))
    ({}, (Decimal(16), Decimal(19), 3, 3, Decimal(24), 60)),
    ({"credit_min": 15, "credit_max": 18}, (Decimal(15), Decimal(18), 3, 3, Decimal(24), 60)),
    ({"major_courses_min": 2, "major_courses_max": 3}, (Decimal(16), Decimal(19), 2, 3,
                                                        Decimal(24), 60)),
    ({"commitments": {"job_hours_per_week": 15}}, (Decimal(16), Decimal(19), 3, 3,
                                                   Decimal(19), 45)),
    ({"commitments": {"greek_life": "rushing", "clubs": "heavy"}},
     (Decimal(16), Decimal(19), 3, 3, Decimal("19.5"), 46)),
    ({"commitments": {"job_hours_per_week": 25, "athletics_hours_per_week": 20}},
     (Decimal(16), Decimal(19), 3, 3, Decimal(16), 15)),                  # never below the floor
])
def test_answers_map_to_settings(answers, expected) -> None:
    """Table-driven: questionnaire answers -> the numbers a Semi-rigorous term uses."""
    c = to_constraints(PlanningProfileInput.model_validate(
        {"workload_level": "semi_rigorous", **answers}))
    st = TermSettings(c, "20271")
    assert (st.floor, st.cap, st.major_min, st.major_max, st.budget,
            st.hours_available) == expected


@pytest.mark.parametrize("question", ["credit_min", "major_courses_min", "commitments", "honors",
                                      "term_overrides", "pins", "filler_preference"])
def test_every_question_has_a_default_when_skipped(question) -> None:
    profile = PlanningProfileInput(workload_level=WorkloadLevel.BALANCED)
    defaults = {"credit_min": None, "major_courses_min": None, "commitments": Commitments(),
                "honors": None, "term_overrides": [], "pins": [],
                "filler_preference": "no_preference"}
    assert getattr(profile, question) == defaults[question]
    st = TermSettings(to_constraints(profile), "20271")
    assert (st.floor, st.cap, st.major_min, st.major_max, st.hours) == (
        Decimal(14), Decimal(17), 2, 3, 0)


def test_precedence_semester_over_profile_over_level() -> None:
    c = to_constraints(PlanningProfileInput.model_validate({
        "workload_level": "light", "credit_min": 15, "credit_max": 17,
        "major_courses_min": 2, "major_courses_max": 2,
        "term_overrides": [{"term_code": "20279", "credit_min": 12, "credit_max": 13,
                            "major_courses_min": 1, "major_courses_max": 1}]}))
    fall, spring = TermSettings(c, "20279"), TermSettings(c, "20271")
    assert (fall.floor, fall.major_max, fall.source, fall.credit_source) == (
        Decimal(12), 1, "override", "override")
    assert (spring.floor, spring.major_max, spring.source, spring.credit_source) == (
        Decimal(15), 2, "profile", "profile")
    with pytest.raises(ValidationError):
        PlanningProfileInput.model_validate({"credit_min": 18, "credit_max": 15})
    with pytest.raises(ValidationError):
        PlanningProfileInput.model_validate({"major_courses_min": 2})


def test_honors_titles_are_read_from_the_catalog() -> None:
    from app.services.planning.engine import HONORS_TITLE
    for title in ("COMPUTER SCIENCE HONORS CAPSTONE        PROGRAM", "HONOR SEM COMP SCI",
                  "SENIOR HONORS THESIS", "HONORS GENERAL CHEM"):
        assert HONORS_TITLE.search(title)
    for title in ("PHONORS", "HONORABLE", "INTRO TO COMPUTER SCIENCE"):
        assert not HONORS_TITLE.search(title)
