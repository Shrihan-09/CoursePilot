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
    ClubLoad,
    Commitments,
    GreekLife,
    CoursePin,
    FillerPreference,
    Pace,
    PlanningConstraints,
    TermPlanningOverride,
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
    """Task 2: Linear Algebra counts toward the major now, so it is no longer
    a filler; the in-person Core candidate (01:190:207) is over this term's
    budget, so the term's one filler is the course that fits - the preference
    orders fillers, it never breaks the budget or the credit floor."""
    student = _student(world)
    in_person = _term(_plan(world, student, filler_preference=FillerPreference.IN_PERSON))
    flexible = _term(_plan(world, student, filler_preference=FillerPreference.FLEXIBLE))
    keep = {pc.course for pc in in_person.courses if pc.role != "filler"}
    assert keep == {pc.course for pc in flexible.courses if pc.role != "filler"}
    assert "01:640:152" in keep
    assert all("async" in pc.workload.delivery for pc in flexible.courses if pc.role == "filler")
    fillers = [pc for pc in in_person.courses if pc.role == "filler"]
    assert fillers and in_person.credits >= in_person.workload.credit_floor


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
        "heavier term" in n or "credit minimum comes first" in n for n in term.workload.notes)


def test_a_level_that_misses_the_graduation_target_is_said_not_hidden(world):
    # Entered Fall 2025; Early = 7 semesters -> Fall 2028 is the last one.
    plan = _plan(world, _student(world), workload_level=WorkloadLevel.LIGHT,
                 pace=Pace.FINISH_EARLIER)
    issue = next(i for i in plan.issues if i.code == "GRADUATION_TARGET_NOT_MET")
    assert "has not been changed" in issue.message and "summer" in issue.message
    assert "light workload" in issue.message
    assert all(t.term_code <= "20289" for t in plan.terms)   # never pushed later


def test_without_a_level_nothing_changes(world):
    plan = generate_plan(world, _student(world), start_term=SPRING27)
    assert all(t.workload is None for t in plan.terms)
    assert all(pc.role is None and pc.workload is None for t in plan.terms for pc in t.courses)


# ------------------------------------------------------------------ Task 2
# Major-related courses per term, commitments, per-semester overrides, the
# honors gate and "Why this fits you". A major-related course counts toward
# the major's tree - Calc II and the B.S. physics included, not only 01:198.

RANGE = {WorkloadLevel.LIGHT: (1, 2), WorkloadLevel.BALANCED: (2, 3),
         WorkloadLevel.SEMI_RIGOROUS: (3, 3), WorkloadLevel.CHALLENGING: (3, 4)}


def _explained(w):
    words = " ".join(w.notes)
    return "above your range" in words


@pytest.mark.parametrize("level", list(WorkloadLevel))
def test_each_level_sets_the_major_related_range_and_says_why(world, level):
    plan = _plan(world, _student(world), workload_level=level)
    for term in plan.terms:
        w = term.workload
        assert (w.major_min, w.major_max, w.major_range_source) == (*RANGE[level], "level")
        assert w.major_related <= w.major_max or _explained(w)
        # The credit minimum is a hard floor: below it only with a reason.
        assert term.credits >= w.credit_floor or any("credit floor" in s for s in w.shortfalls)
        assert w.fit.startswith("Why this fits you: ")
        assert f"{w.major_related} major-related course" in w.fit
        assert not EASY_HARD.search(" ".join([w.fit, *w.notes, *w.shortfalls]))
    spring = _term(plan).workload
    # Calc II (a retake) and Gen Physics with its lab count; the lab once.
    assert spring.major_related >= min(2, RANGE[level][1])


def test_the_scenario_counts_calc_physics_and_one_cs_course_as_three(world):
    w = _term(_plan(world, _student(world))).workload
    assert (w.major_related, w.major_min, w.major_max) == (3, 3, 3)
    assert w.fit.startswith("Why this fits you: Semi-rigorous → ")
    assert "3 major-related courses (your range: 3), async filler" in w.fit


def test_a_semester_override_sets_the_exact_range_there(world):
    plan = _plan(world, _student(world), workload_level=WorkloadLevel.LIGHT,
                 term_overrides=[TermPlanningOverride(term_code=SPRING27, major_courses_min=1,
                                                      major_courses_max=1)])
    w = _term(plan).workload
    assert (w.major_min, w.major_max, w.major_range_source) == (1, 1, "override")
    assert "your range for this semester: 1" in w.fit
    # Calc II is the retake (an anchor); nothing else major-related joins it
    # unless the credit floor needs it, and then the term says so.
    assert w.major_related == 1 or _explained(w)
    later = [t.workload for t in plan.terms if t.term_code != SPRING27]
    assert all(x.major_range_source == "level" and (x.major_min, x.major_max) == (1, 2)
               for x in later)


def test_anchors_are_never_dropped_and_exceeding_the_range_is_said(world):
    # Retaking Calc II + two pins, all major-related: three against Light's 1-2.
    pins = [CoursePin(course="01:198:211", term_code=SPRING27),
            CoursePin(course="01:750:203", term_code=SPRING27)]
    # No graduation target (no pace): nothing but the anchors can raise the range.
    plan = _plan(world, _student(world), workload_level=WorkloadLevel.LIGHT, pins=pins, pace=None)
    assert plan.graduation_target is None
    term = _term(plan)
    placed = {pc.course for pc in term.courses}
    assert {"01:640:152", "01:198:211", "01:750:203"} <= placed      # retake + both pins
    w = term.workload
    assert w.major_related >= 3 > w.major_max == 2
    assert any("always stay" in n and "above your range of 1-2" in n for n in w.notes)


def test_a_graduation_target_that_needs_more_is_met_and_said(world):
    # Graduating this very semester: everything left is needed now.
    plan = _plan(world, _student(world), expected_graduation_term=SPRING27)
    w = _term(plan).workload
    assert w.major_max == 3 and w.major_related > 3
    assert any(n.startswith("To graduate by Spring 2027") and "above your range of 3" in n
               for n in w.notes)
    assert all(t.term_code <= SPRING27 for t in plan.terms)          # never delayed


def test_commitments_lower_the_budget_and_the_term_says_so(world):
    plan = _plan(world, _student(world),
                 commitments=Commitments(job_hours_per_week=12, clubs=ClubLoad.LIGHT))
    term = _term(plan)
    w = term.workload
    assert (w.base_budget, w.budget, w.commitment_hours) == (Decimal(24), Decimal(19), 15)
    assert "Semi-rigorous + job 12 h/week + clubs → " in w.fit
    assert "budget 19 instead of 24 for your commitments" in w.fit
    assert any("15 hours a week: job 12, clubs 3" in n and "45 of 60 weekly hours" in n
               and "from 24 to 19" in n for n in w.notes)
    assert w.hours_available == 45
    assert w.total <= w.budget or any("credit minimum comes first" in n for n in w.notes)
    assert term.credits >= w.credit_floor or w.shortfalls
    assert "01:640:152" in {pc.course for pc in term.courses}         # the retake stays


def test_an_honors_only_course_is_planned_only_for_an_honors_student(world):
    from app.models import CourseOffering, CourseSection, SectionRestriction
    sections = world.scalars(
        select(CourseSection).join(CourseOffering).join(Course)
        .where(Course.course_string == "01:198:211", Course.supplement_code == "",
               CourseSection.term_code == SPRING27)).all()
    assert sections
    for s in sections:
        world.add(SectionRestriction(section_id=s.id, ordinal=90, kind="honor_program", code="A"))
    world.commit()
    student = _student(world)
    skipped = _plan(world, student)
    assert "01:198:211" not in {pc.course for pc in _term(skipped).courses}
    issue = next(i for i in skipped.issues if i.code == "HONORS_ONLY_NOT_PLANNED")
    assert issue.course == "01:198:211" and "honors" in issue.message
    no = _plan(world, student, honors=False)
    assert "01:198:211" not in {pc.course for pc in _term(no).courses}
    yes = _plan(world, student, honors=True)
    assert "01:198:211" in {pc.course for pc in _term(yes).courses}
    assert not any(i.code == "HONORS_ONLY_NOT_PLANNED" for i in yes.issues)
    pinned = _plan(world, student, pins=[CoursePin(course="01:198:211", term_code=SPRING27)])
    pin_issue = next(i for i in pinned.issues if i.code == "PIN_NOT_POSSIBLE")
    assert "honors programs" in pin_issue.message


def test_pace_and_level_stay_separate_with_a_conflict_warning(world):
    student = _student(world)
    plan = _plan(world, student, workload_level=WorkloadLevel.LIGHT, pace=Pace.FINISH_EARLIER)
    warning = next(i for i in plan.issues if i.code == "WORKLOAD_VS_GRADUATION_TARGET")
    assert warning.severity.value == "warning" and not EASY_HARD.search(warning.message)
    assert all(t.workload.credit_floor == Decimal(12) for t in plan.terms)   # the level's load
    calm = _plan(world, student, workload_level=WorkloadLevel.LIGHT, pace=Pace.BALANCED)
    assert not any(i.code == "WORKLOAD_VS_GRADUATION_TARGET" for i in calm.issues)


# ------------------------------------------------------------------ Prompt 09


def test_scenario_light_rushing_with_a_15_hour_job(world):
    """Prompt 09 scenario 2: a lower budget, 2 major-related courses, no heavy
    stacking - and the term says the rush semester lowered it."""
    plan = _plan(world, _student(world), workload_level=WorkloadLevel.LIGHT,
                 commitments=Commitments(job_hours_per_week=15, greek_life=GreekLife.RUSHING))
    term = _term(plan)
    w = term.workload
    assert w.base_budget == Decimal(16) and w.budget == Decimal(12)       # clamped at the floor
    assert w.commitment_hours == 21 and w.hours_available == 39
    assert w.major_related <= 2 or _explained(w)
    assert term.credits <= w.credit_max == Decimal(14)                     # no heavy stacking
    assert any(n.startswith("Your rush semester") for n in w.notes)
    assert "Light + rushing + job 15 h/week → " in w.fit
    assert "01:640:152" in {pc.course for pc in term.courses}             # the retake stays


def test_scenario_challenging_without_commitments(world):
    """Prompt 09 scenario 3: a high load and 3-4 major-related courses."""
    term = _term(_plan(world, _student(world), workload_level=WorkloadLevel.CHALLENGING))
    w = term.workload
    assert term.credits >= Decimal(17) and w.budget == Decimal(28)
    assert 3 <= w.major_related <= 4
    assert w.fit.startswith("Why this fits you: Challenging → ")


def test_the_credit_minimum_is_a_hard_floor_and_the_conflict_is_said(world):
    """A 19-credit minimum on a Light level (range 1-2, budget 16): the
    floor is reached by passing the range and the budget, each said, and the
    student is told how to choose the budget instead."""
    plan = _plan(world, _student(world), workload_level=WorkloadLevel.LIGHT,
                 credit_min=19, credit_max=21)
    term = _term(plan)
    w = term.workload
    assert (w.credit_floor, w.credit_max) == (Decimal(19), Decimal(21))
    assert term.credits >= Decimal(19)
    assert w.total > w.budget and any("the credit minimum comes first" in n for n in w.notes)
    issue = next(i for i in plan.issues if i.code == "CREDIT_FLOOR_OVER_BUDGET"
                 and i.details["term"] == SPRING27)
    assert "lower this semester's credit minimum" in issue.message


def test_a_profile_wide_major_range_and_credit_range(world):
    plan = _plan(world, _student(world), workload_level=WorkloadLevel.CHALLENGING,
                 major_courses_min=1, major_courses_max=1, credit_min=15, credit_max=16)
    w = _term(plan).workload
    assert (w.major_min, w.major_max, w.major_range_source) == (1, 1, "profile")
    assert (w.credit_floor, w.credit_max) == (Decimal(15), Decimal(16))
    assert _term(plan).credits <= Decimal(16)


def test_an_excluded_course_never_counts_as_major_related(world):
    """Prompt 09 s1.3: CS gives no credit for 01:198:142; pinned, it is
    placed, warned about, and never counted as a major course."""
    plan = _plan(world, _student(world), pins=[CoursePin(course="01:198:142",
                                                        term_code=SPRING27)])
    term = _term(plan)
    data101 = next(pc for pc in term.courses if pc.course == "01:198:142")
    assert data101.role == "anchor" and not data101.requirements
    assert any(i.code == "PIN_WILL_NOT_COUNT" and i.course == "01:198:142" for i in plan.issues)
    counted = {pc.course for pc in term.courses
               if pc.requirements and any(r.requirement_system == "major"
                                          for r in pc.requirements)}
    assert "01:198:142" not in counted
    others = [pc for pc in term.courses if pc.course != "01:198:142"]
    assert term.workload.major_related == len(
        {frozenset(pc.corequisite_group or [pc.course]) for pc in others
         if any(r.requirement_system == "major" for r in pc.requirements)})


def test_a_title_only_honors_course_stays_plannable_and_needs_confirmation(world):
    """Task 2 review: a title naming an honors course ("HONORS GENERAL CHEM",
    01:160:163/164) with no SOC honors restriction is NOT blocked - it is
    planned, flagged as needing confirmation. Only SOC data hard-blocks."""
    course = world.scalar(select(Course).where(Course.course_string == "01:198:211",
                                               Course.supplement_code == ""))
    course.title = "HONORS COMPUTER ARCHITECTURE"
    world.commit()
    student = _student(world)
    for honors in (None, False, True):
        plan = _plan(world, student, honors=honors)
        pc = next(pc for pc in _term(plan).courses if pc.course == "01:198:211")
        assert "Needs confirmation: may require an honors program or permission." in pc.notes
        issue = next(i for i in plan.issues if i.code == "HONORS_TITLE_NEEDS_CONFIRMATION")
        assert issue.course == "01:198:211" and issue.severity.value == "needs_confirmation"
        assert not any(i.code == "HONORS_ONLY_NOT_PLANNED" for i in plan.issues)


def test_a_course_needing_special_permission_is_only_placed_when_pinned(world):
    """Prompt 09 s1.2: every section needs special permission to add
    (admission or consent) - no planning answer meets that."""
    from app.models import CourseOffering, CourseSection
    for s in world.scalars(select(CourseSection).join(CourseOffering).join(Course)
                           .where(Course.course_string == "01:198:211",
                                  Course.supplement_code == "")):
        s.special_permission_add_code = "A"
    world.commit()
    student = _student(world)
    plan = _plan(world, student, honors=True)
    assert "01:198:211" not in {pc.course for t in plan.terms for pc in t.courses}
    assert any(i.code == "PERMISSION_REQUIRED_NOT_PLANNED" for i in plan.issues)
    pinned = _plan(world, student, pins=[CoursePin(course="01:198:211", term_code=SPRING27)])
    assert "01:198:211" in {pc.course for pc in _term(pinned).courses}
    assert any(i.code == "PIN_NEEDS_PERMISSION" for i in pinned.issues)


def test_retakes_and_pins_are_anchors_and_a_major_double_counter_keeps_its_role(world):
    """Order (decided in review): anchors, then major-related courses - one
    that also fills another requirement stays "counts twice" - then other
    double-counters, then fillers."""
    plan = _plan(world, _student(world), pins=[CoursePin(course="01:198:211", term_code=SPRING27)])
    roles = {pc.course: pc.role for pc in _term(plan).courses}
    assert roles["01:640:152"] == roles["01:198:211"] == "anchor"
    assert roles["01:750:203"] == "multi_requirement"


def test_major_courses_come_before_core_only_double_counters():
    from app.services.planning import questionnaire
    assert questionnaire.MULTI_BEFORE_MAJOR is False


def test_commitments_below_the_credit_minimum_give_one_plan_message(world):
    student = _student(world)
    plan = _plan(world, student, workload_level=WorkloadLevel.LIGHT,
                 commitments=Commitments(job_hours_per_week=15, greek_life=GreekLife.RUSHING))
    [issue] = [i for i in plan.issues if i.code == "COMMITMENTS_VS_CREDIT_MINIMUM"]
    assert issue.message == ("Light + your commitments don't fit a 12-credit minimum; consider "
                             "lowering commitments, the minimum, or adding a term.")
    calm = _plan(world, student, workload_level=WorkloadLevel.LIGHT)
    assert not any(i.code == "COMMITMENTS_VS_CREDIT_MINIMUM" for i in calm.issues)


def test_no_stranded_final_term_at_a_workload_level(world):
    """A last term at most half its credit floor is folded into earlier
    terms when they have room (the existing all-or-nothing compression)."""
    student = _student(world)
    for level in WorkloadLevel:
        plan = _plan(world, student, workload_level=level)
        if len(plan.terms) > 1:
            last = plan.terms[-1]
            if last.credits * 2 > last.workload.credit_floor:
                continue
            # Still stranded only when it could not move: each course is held
            # there by a prerequisite the plan places in an earlier term.
            assert all(any(d.source == "planned" for d in pc.prerequisite.conditional_on)
                       for pc in last.courses), [pc.course for pc in last.courses]


def test_no_api_output_calls_a_course_easy_or_hard(world):
    """Prompt 09 s6: a grep over the whole plan as the API returns it."""
    plan = _plan(world, _student(world), workload_level=WorkloadLevel.LIGHT,
                 commitments=Commitments(job_hours_per_week=25, greek_life=GreekLife.NEW_MEMBER),
                 credit_min=19, credit_max=21)
    assert not EASY_HARD.search(plan.model_dump_json())
