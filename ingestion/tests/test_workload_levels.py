"""Phase 7.3: workload levels, workload weights, filler preference and pins.

World: REAL public SOC records (fixtures/soc_workload_terms_sample.json - CS
core, Calc I/II, Linear Algebra, Gen Physics 203/204 with labs 205/206,
Sociology 101/311, Data 101) for Spring 2026, Fall 2026 and Spring 2027, a
few sections per course, cross-listings dropped. The real curated CS B.S.
(pending review) with SAS Core attached from the same records' coreCodes.

The scenario student is FABRICATED: a CS B.S. student who passed CS 111/112/
205, Calc I and Intro to Sociology, and FAILED Calc II in Fall 2026.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal

import pytest
from app.domain.planning import (
    CoursePin,
    FillerPreference,
    Pace,
    PlanningConstraints,
    WorkloadLevel,
)
from app.models import Course, Program, ProgramVersion, Student, StudentCourse
from app.services.planning.service import generate_plan
from sqlalchemy import select

from coursepilot_ingestion.loaders.requirements import RequirementLoader
from coursepilot_ingestion.pipelines.core import CoreIngestionPipeline
from coursepilot_ingestion.pipelines.courses import CourseIngestionPipeline
from coursepilot_ingestion.pipelines.sections import SectionIngestionPipeline
from coursepilot_ingestion.sources.soc import SocQuery

from .conftest import CURATED_DIR, FIXTURE_DIR, SAS_CORE

SPRING27 = "20271"
CS_BS = CURATED_DIR / "sas-198-bs.json"
HISTORY = [("01:198:111", "20259", "A"), ("01:198:112", "20261", "A"),
           ("01:198:205", "20261", "B"), ("01:640:151", "20259", "A"),
           ("01:920:101", "20259", "A"), ("01:640:152", "20269", "F")]
EASY_HARD = re.compile(r"\b(easy|easier|hard|harder|difficult)\b", re.IGNORECASE)


@pytest.fixture
def world(session, tmp_path):
    terms = json.loads((FIXTURE_DIR / "soc_workload_terms_sample.json").read_bytes())
    combined = {}
    for term, records in terms.items():
        year, season = term.split("_")
        folder = tmp_path / term
        folder.mkdir()
        (folder / f"soc_courses_{year}_{season}_NB.json").write_text(json.dumps(records), "utf-8")
        query = SocQuery(year=int(year), term=season, campus="NB")
        CourseIngestionPipeline(session, folder).run(query, coverage="complete")
        SectionIngestionPipeline(session, folder).run(query)
        combined.update({r["courseString"]: r for r in records})
    RequirementLoader(session).load_file(CS_BS)
    session.commit()
    archive = tmp_path / "combined.json"
    archive.write_text(json.dumps(list(combined.values())), "utf-8")
    CoreIngestionPipeline(session).run(SAS_CORE, archive, observed_term_code=SPRING27)
    session.commit()
    return session


def _student(session, history=HISTORY):
    version = session.scalar(select(ProgramVersion).join(Program)
                             .where(Program.code == "198", Program.degree_type == "BS"))
    student = Student(external_ref=f"wl-{len(history)}", catalog_year=version.catalog_year,
                      program_version_id=version.id)
    session.add(student)
    session.flush()
    for key, term, grade in history:
        course = session.scalar(select(Course).where(Course.course_string == key,
                                                     Course.supplement_code == ""))
        session.add(StudentCourse(student_id=student.id, course_id=course.id, term_code=term,
                                  status="completed", grade=grade,
                                  credits_earned=course.credits if grade != "F" else 0))
    session.commit()
    return student


def _plan(session, student, **kw):
    constraints = PlanningConstraints(**{
        "workload_level": WorkloadLevel.SEMI_RIGOROUS,
        "filler_preference": FillerPreference.FLEXIBLE,
        "max_courses_per_term": 6, "pace": Pace.BALANCED, **kw})
    return generate_plan(session, student, start_term=SPRING27, constraints=constraints)


def _term(plan, code=SPRING27):
    return next(t for t in plan.terms if t.term_code == code)


def test_the_scenario_spring_2027_shape(world):
    """Retake Calc II + Gen Physics with lab (Core NS AND the B.S. science) +
    ONE counting CS course + an asynchronous course completing a requirement,
    at 16-19 credits, within the semi-rigorous budget."""
    term = _term(_plan(world, _student(world)))
    by = {pc.course: pc for pc in term.courses}
    assert by["01:640:152"].role == "anchor" and "retake" in by["01:640:152"].role_reason
    assert {"01:750:203", "01:750:205"} <= set(by)
    assert by["01:750:203"].role == "multi_requirement"
    assert "Natural Sciences" in by["01:750:203"].role_reason
    cs = [pc for pc in term.courses if pc.course.startswith("01:198:")]
    assert len(cs) == 1 and cs[0].role == "major"
    fillers = [pc for pc in term.courses if pc.role == "filler"]
    assert len(fillers) == 1
    assert "async" in fillers[0].workload.delivery and "completes" in fillers[0].role_reason
    lab = next(pc for pc in term.courses if pc.course == "01:750:205")
    assert lab.role == "multi_requirement" and "together with 01:750:203" in lab.role_reason
    assert Decimal(16) <= term.credits <= Decimal(19)
    w = term.workload
    assert w.level is WorkloadLevel.SEMI_RIGOROUS and w.total <= w.budget == Decimal(24)
    assert w.contact_hours > 0
    words = " ".join([*(pc.role_reason or "" for pc in term.courses), *w.notes])
    assert not EASY_HARD.search(words)


def test_the_weight_shows_every_published_input(world):
    term = _term(_plan(world, _student(world)))
    lab = next(pc for pc in term.courses if pc.course == "01:750:205").workload
    assert lab.has_lab and lab.components["lab"] == Decimal(1)
    assert lab.contact_hours and lab.contact_hours > lab.credits
    calc = next(pc for pc in term.courses if pc.course == "01:640:152").workload
    assert calc.technical == "mathematics (01:640)" and calc.components["technical"] == 1
    for pc in term.courses:
        wl = pc.workload
        assert wl.weight == sum(wl.components.values())       # nothing hidden
        assert wl.basis_term == SPRING27                        # this term's own sections


def test_in_person_preference_changes_only_the_filler(world):
    term = _term(_plan(world, _student(world), filler_preference=FillerPreference.IN_PERSON))
    fillers = [pc for pc in term.courses if pc.role == "filler"]
    assert fillers and all("in_person" in pc.workload.delivery for pc in fillers)
    assert "01:640:152" in {pc.course for pc in term.courses}


def test_a_pinned_course_the_program_excludes_is_placed_with_a_warning(world):
    plan = _plan(world, _student(world), pins=[CoursePin(course="01:198:142", term_code=SPRING27)])
    assert "01:198:142" in {pc.course for pc in _term(plan).courses}
    assert any(i.code == "PIN_WILL_NOT_COUNT" and i.course == "01:198:142" for i in plan.issues)


@pytest.mark.parametrize(("pin", "reason", "history"), [
    # not offered then (a student who has not taken 111 yet)
    ("01:198:111", "not listed for Spring 2027", [h for h in HISTORY if h[0] != "01:198:111"]),
    ("01:198:344", "prerequisites are not met", HISTORY),            # needs 206/211 first
    ("01:198:205", "already completed", HISTORY),
])
def test_an_impossible_pin_is_explained(world, pin, reason, history):
    plan = _plan(world, _student(world, history),
                 pins=[CoursePin(course=pin, term_code=SPRING27)])
    assert pin not in {pc.course for pc in _term(plan).courses}
    issue = next(i for i in plan.issues if i.code == "PIN_NOT_POSSIBLE" and i.course == pin)
    assert reason in issue.message


def test_pins_hold_their_term_and_heavy_anchors_are_said(world):
    plan = _plan(world, _student(world), workload_level=WorkloadLevel.LIGHT,
                 pins=[CoursePin(course="01:750:203", term_code=SPRING27)])
    term = _term(plan)
    assert {"01:640:152", "01:750:203"} <= {pc.course for pc in term.courses}
    assert all(pc.course != "01:750:203" for t in plan.terms if t.term_code != SPRING27
               for pc in t.courses)
    assert term.workload.total <= term.workload.budget or any(
        "heavier term" in n for n in term.workload.notes)


def test_a_level_that_misses_the_graduation_target_is_said_not_hidden(world):
    plan = _plan(world, _student(world), workload_level=WorkloadLevel.LIGHT,
                 pace=Pace.FINISH_EARLIER, max_terms=2)
    issue = next(i for i in plan.issues if i.code == "WORKLOAD_VS_GRADUATION_TARGET")
    assert "has not been changed" in issue.message and "summer" in issue.message
    assert len(plan.terms) <= 2                       # graduation is never pushed later


def test_without_a_level_nothing_changes(world):
    plan = generate_plan(world, _student(world), start_term=SPRING27)
    assert all(t.workload is None for t in plan.terms)
    assert all(pc.role is None and pc.workload is None for t in plan.terms for pc in t.courses)
