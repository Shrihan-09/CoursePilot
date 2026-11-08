"""10a follow-up: the retakes the planner detects, and WHEN to retake each.

The CS world of the planning tests (real SOC samples for Fall 2025, Spring
2026, Fall 2026 and a partial Spring 2027; the real curated CS B.A.). One
Winter 2026 payload lists Calc II, so Winter 2027 is historically offered -
the payload is the REAL Calc II record, loaded for a term it was not taken
from (labelled SYNTHETIC here). Students are fabricated.
"""

from __future__ import annotations

from datetime import date

import pytest
from app.domain.planning import CoursePin, Pace, PlanningConstraints, WorkloadLevel
from app.services.planning.retakes import detect_retakes
from app.services.planning.service import generate_plan

from .test_planning_engine import (  # noqa: F401
    RequirementLoader,
    _load,
    _records,
    _student,
    _terms,
)
from .conftest import CS_REQUIREMENTS

NOV_2026 = date(2026, 11, 7)            # planning starts Spring 2027
CALC2 = "01:640:152"
#: Calc II failed in Spring 2026; everything else of the first year passed.
FAILED_CALC2 = [("01:198:111", "20259"), ("01:640:151", "20259"), ("01:198:112", "20261"),
                (CALC2, "20261", "completed", "F")]


def _world(session, tmp_path, *, winter=False):
    records = _records("soc_cs_courses_sample.json")
    _terms(session, tmp_path, records, spring_omit={"01:198:352", "01:198:416"},
           spring27={"01:198:314", "01:198:336"})
    if winter:   # SYNTHETIC: the real Calc II record, as a Winter 2026 offering
        _load(session, tmp_path, [r for r in records if r["courseString"] == CALC2],
              2026, "0", "partial")
    RequirementLoader(session).load_file(CS_REQUIREMENTS)
    session.commit()
    return session


@pytest.fixture
def cs(session, tmp_path):
    return _world(session, tmp_path)


@pytest.fixture
def cs_winter(session, tmp_path):
    return _world(session, tmp_path, winter=True)


def _cons(*pins, **kw):
    return PlanningConstraints(pace=Pace.BALANCED, workload_level=WorkloadLevel.BALANCED,
                               pins=list(pins), **kw)


def test_a_retake_is_listed_with_why_and_what_it_unlocks(cs):  # noqa: F811
    view = detect_retakes(cs, _student(cs, FAILED_CALC2), NOV_2026)
    (retake,) = view.retakes
    assert (retake.course, retake.reason) == (CALC2, "grade F earns no credit")
    assert "01:198:206" in {u.course for u in retake.unlocks}
    assert view.start_term == "20271"
    assert "billed separately" in view.cost_note and "$" not in view.cost_note


def test_a_blocking_retake_defaults_to_asap(cs):  # noqa: F811
    (retake,) = detect_retakes(cs, _student(cs, FAILED_CALC2), NOV_2026).retakes
    assert retake.blocking and retake.default == "asap"
    asap = next(o for o in retake.options if o.key == "asap")
    assert asap.recommended and asap.term_code == "20279"      # Spring 2027 omits Calc II
    assert asap.evidence == "historical" and "Historically offered" in asap.evidence_label
    nxt = next(o for o in retake.options if o.key == "next_semester")
    assert (nxt.term_code, nxt.evidence) == ("20271", "none")   # always shown, not offered


def test_winter_is_offered_only_when_the_course_is_offered_that_winter(cs):  # noqa: F811
    (retake,) = detect_retakes(cs, _student(cs, FAILED_CALC2), NOV_2026).retakes
    assert {o.key for o in retake.options} == {"asap", "next_semester"}   # no winter/summer data


def test_winter_shows_with_its_evidence_labelled(cs_winter):
    (retake,) = detect_retakes(cs_winter, _student(cs_winter, FAILED_CALC2), NOV_2026).retakes
    winter = next(o for o in retake.options if o.key == "winter")
    assert winter.term_code == "20270" and winter.evidence == "historical"
    assert "not yet confirmed for Winter 2027" in winter.evidence_label
    assert all(i.message.startswith("This keeps") for i in winter.impacts)


class _Index:
    """Offering evidence stub: course -> terms it is offered in (confirmed)."""

    def __init__(self, offered):
        self.offered = offered

    def evidence(self, course, term):
        from app.domain.planning import OfferingEvidence, OfferingEvidenceKind
        kind = (OfferingEvidenceKind.CONFIRMED_IN_TERM if term in self.offered.get(course, ())
                else OfferingEvidenceKind.NOT_OFFERED_IN_TERM)
        return OfferingEvidence(kind=kind, target_term=term)


def test_summer_shows_a_delay_warning_when_it_pushes_back_an_unlocked_course():
    """Retaken in Spring 2027, Math 251 fits Summer 2027 (the student plans
    summers); retaken IN Summer 2027, the earliest is Fall 2027."""
    from app.services.planning.retakes import Unlock, _impacts
    index = _Index({"01:640:251": {"20277", "20279", "20281"}})
    unlock = (Unlock("01:640:251", "CALC III", planned_term="20277", direct=True),)
    (summer,) = _impacts(index, "20277", unlock, "Summer")
    assert summer.delayed and summer.message == "Summer delays 01:640:251 to Fall 2027"
    (asap,) = _impacts(index, "20271", unlock, "ASAP")
    assert not asap.delayed and asap.message == "This keeps 01:640:251 on track for Summer 2027"


def test_the_choice_is_a_term_pin_the_planner_respects(cs_winter):
    """Winter session, with the student's winter preference OFF: the pin opens
    Winter 2027 for Calc II only."""
    student = _student(cs_winter, FAILED_CALC2)
    pin = CoursePin(course=CALC2, term_code="20270", retake=True)
    plan = generate_plan(cs_winter, student, start_term="20271", constraints=_cons(pin))
    winter = [t for t in plan.terms if t.term_code[4] in "07"]
    assert [(t.term_code, [c.course for c in t.courses]) for t in winter] == [("20270", [CALC2])]
    assert winter[0].courses[0].retake == "grade F earns no credit"
    assert not any(i.code == "PIN_NOT_POSSIBLE" for i in plan.issues)
    # Without the pin, no winter term is planned at all.
    plain = generate_plan(cs_winter, student, start_term="20271", constraints=_cons())
    assert not any(t.term_code[4] in "07" for t in plain.terms)


def test_a_retake_pin_may_place_a_course_passed_with_too_low_a_grade(cs_winter):
    """A D in Calc II earns credit but not the C 01:198:314 needs. Pinned to
    the plan's FIRST term (Winter 2027) - before the planner has met the course
    that needs the C - only a pin marked `retake` may place it again."""
    student = _student(cs_winter, [*FAILED_CALC2[:3], (CALC2, "20261", "completed", "D")])
    pin = dict(course=CALC2, term_code="20270")
    plan = generate_plan(cs_winter, student, start_term="20271",
                         constraints=_cons(CoursePin(**pin, retake=True)))
    placed = {c.course: c for t in plan.terms for c in t.courses}
    assert placed[CALC2].term_code == "20270"
    assert placed[CALC2].retake == "you chose to retake it (grade D)"
    refused = generate_plan(cs_winter, student, start_term="20271",
                            constraints=_cons(CoursePin(**pin)))
    assert any(i.code == "PIN_NOT_POSSIBLE" and i.course == CALC2
               and "already completed" in i.message for i in refused.issues)
