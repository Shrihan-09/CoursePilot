"""Phase 7.3: a plan never holds the same class twice; topics courses are labelled and capped.

Worlds are the planning tests' real CS/Math SOC samples. Where a scenario
needs data the samples lack, a copy of a real record is changed and labelled
SYNTHETIC with the real Rutgers shape it imitates:

  * 01:198:417 / 16:198:545 are cross-listed in Fall 2026 (real); here
    01:198:323 and 01:198:345 are cross-listed the same way.
  * 01:198:442 "TOPICS IN COMP SCI" publishes its topic as the section
    subtitle ("Introduction to Cryptography", Spring 2026/2027); 01:198:443
    "TPCS IN COMPUTER SCI" published none in Spring 2026.

Fabricated students only; no network.
"""

from __future__ import annotations

import collections
import copy
from decimal import Decimal

from app.domain.planning import PlanningConstraints
from app.models import SectionCrossListing
from sqlalchemy import func, select

from coursepilot_ingestion.loaders.requirements import RequirementLoader
from coursepilot_ingestion.pipelines.sections import SectionIngestionPipeline
from coursepilot_ingestion.sources.soc import SocQuery

from .conftest import CS_REQUIREMENTS
from .test_planning_engine import (
    CS_DONE,
    START,
    _placed,
    _plan,
    _records,
    _student,
    cs,  # noqa: F401 - fixture
    math,  # noqa: F401 - fixture
)

#: Room for a fourth course in Spring 2027 (314, 336, 344 already fill 12 of
#: the default 15 credits), so only the TOPICS cap decides.
ROOMY = {"max_credits_per_term": Decimal(20), "max_courses_per_term": 6}

ELECTIVES_LEFT = [c for c in CS_DONE if c[0] not in ("01:198:314", "01:198:336")]
#: Removed from the synthetic worlds so the courses under test are NEEDED and
#: REACHABLE (416 needs 01:198:214, absent from the sample) - otherwise the
#: planner (rightly) never gets to them.
CROSS_DROP = {"01:198:334", "01:198:352", "01:198:405", "01:198:416"}
TOPICS_DROP = CROSS_DROP | {"01:198:323", "01:198:345"}


def assert_no_duplicates(session, plan) -> None:
    """Fails on ANY duplicate: a course string twice (no retake is planned
    here), or two courses Rutgers cross-lists (one class, two numbers)."""
    keys = [pc.course for t in plan.terms for pc in t.courses]
    twice = [k for k, n in collections.Counter(keys).items() if n > 1]
    assert not twice, f"planned twice: {twice}"
    partner = func.concat(SectionCrossListing.offering_unit_code, ":",
                          SectionCrossListing.subject_code, ":",
                          SectionCrossListing.course_number)
    from app.models import Course, CourseOffering, CourseSection

    pairs = set(session.execute(
        select(Course.course_string, partner)
        .join(CourseOffering, CourseOffering.course_id == Course.id)
        .join(CourseSection, CourseSection.offering_id == CourseOffering.id)
        .join(SectionCrossListing, SectionCrossListing.section_id == CourseSection.id)
        .where(Course.course_string.in_(keys))).all())
    both = sorted((a, b) for a, b in pairs if a != b and b in keys)
    assert not both, f"cross-listed twins both planned: {both}"


def test_no_plan_in_the_sample_worlds_holds_a_duplicate(cs, math):  # noqa: F811
    for program, done in (("198", []), ("198", CS_DONE), ("198", ELECTIVES_LEFT),
                          ("640", []), ("640", CS_DONE)):
        student = _student(cs, done, program_code=program)
        for start in (START, "20279"):
            assert_no_duplicates(cs, _plan(cs, student, start_term=start))


def _terms(session, tmp_path, records, *, spring27=()):
    """The planning tests' four terms - WITH sections (cross-listings and
    subtitles live on sections)."""
    from .test_planning_engine import _terms as course_terms

    course_terms(session, tmp_path, records, spring27=spring27)
    for year, term in ((2025, "9"), (2026, "1"), (2026, "9")) + (((2027, "1"),) if spring27 else ()):
        SectionIngestionPipeline(session, tmp_path / f"{year}{term}").run(
            SocQuery(year=year, term=term, campus="NB"))


def _cross_list(by_key, a, b):
    """SYNTHETIC: every section of `a` lists `b` as its cross-listing and vice versa."""
    for x, y in ((a, b), (b, a)):
        unit, subject, number = y.split(":")
        for i, section in enumerate(by_key[x]["sections"]):
            section["crossListedSections"] = [{
                "courseNumber": number, "supplementCode": "  ", "sectionNumber": "01",
                "offeringUnitCampus": "NB", "primaryRegistrationIndex": f"9{number}{i}",
                "offeringUnitCode": unit, "registrationIndex": f"9{number}{i}",
                "subjectCode": subject}]


def test_cross_listed_twins_are_never_both_planned(session, tmp_path):
    records = [r for r in _records("soc_cs_courses_sample.json")
               if r["courseString"] not in CROSS_DROP]
    _cross_list({r["courseString"]: r for r in records}, "01:198:323", "01:198:345")
    _terms(session, tmp_path, records)
    RequirementLoader(session).load_file(CS_REQUIREMENTS)
    session.commit()
    plan = _plan(session, _student(session, ELECTIVES_LEFT))
    placed = set(_placed(plan))
    assert not {"01:198:323", "01:198:345"} <= placed
    assert {"01:198:323", "01:198:345"} & placed          # one of them still counts
    assert_no_duplicates(session, plan)


def _topics_world(session, tmp_path, numbers=("442", "443")):
    every = _records("soc_cs_courses_sample.json")
    records = [r for r in every if r["courseString"] not in TOPICS_DROP]
    base = next(r for r in every if r["courseString"] == "01:198:345")     # prereq: 112

    def clone(number, title, subtitles):
        r = copy.deepcopy(base)
        r.update(courseString=f"01:198:{number}", courseNumber=number, title=title,
                 expandedTitle="")
        r["sections"] = []
        for i, subtitle in enumerate(subtitles):
            section = copy.deepcopy(base["sections"][0])
            section.update(index=f"96{number}{i}", number=f"0{i + 1}", subtitle=subtitle,
                           crossListedSections=[])
            r["sections"].append(section)
        return r

    first, second = numbers
    records += [clone(first, "TOPICS IN COMP SCI", ["Introduction to Cryptography",
                                                    "Deep Learning", "Deep Learning"]),
                clone(second, "TPCS IN COMPUTER SCI", [""])]
    _terms(session, tmp_path, records,
           spring27={"01:198:314", "01:198:336", f"01:198:{first}", f"01:198:{second}"})
    RequirementLoader(session).load_file(CS_REQUIREMENTS)
    session.commit()
    return session


def test_at_most_one_topics_course_a_term_with_its_announced_topics(session, tmp_path):
    s = _topics_world(session, tmp_path)
    plan = _plan(s, _student(s, ELECTIVES_LEFT), constraints=PlanningConstraints(**ROOMY))
    assert_no_duplicates(s, plan)
    for term in plan.terms:
        assert sum(pc.is_topics_course for pc in term.courses) <= 1, term.term_code
    topics = [pc for t in plan.terms for pc in t.courses if pc.is_topics_course]
    assert topics
    for pc in topics:
        if pc.offering.kind.value == "confirmed_in_term":
            # This term's sections, distinct, from the SOC subtitle.
            assert pc.topics in (["Deep Learning", "Introduction to Cryptography"], [])
        else:
            assert pc.topics == []                # history never announces a topic
    spring = [pc for t in plan.terms if t.term_code == START for pc in t.courses]
    assert [pc.course for pc in spring if pc.is_topics_course] == ["01:198:442"]
    assert next(pc for pc in spring if pc.is_topics_course).topics == [
        "Deep Learning", "Introduction to Cryptography"]


def test_the_cap_is_a_preference_the_student_can_raise(session, tmp_path):
    s = _topics_world(session, tmp_path)
    plan = _plan(s, _student(s, ELECTIVES_LEFT),
                 constraints=PlanningConstraints(max_topics_courses_per_term=2, **ROOMY))
    spring = {pc.course for t in plan.terms if t.term_code == START for pc in t.courses}
    assert {"01:198:442", "01:198:443"} <= spring
    unannounced = next(pc for t in plan.terms for pc in t.courses if pc.course == "01:198:443")
    assert unannounced.is_topics_course and unannounced.topics == []


def test_fixed_content_electives_come_before_topics_courses(session, tmp_path):
    # SYNTHETIC numbers 300/301 sort BEFORE 314/336, so only the topics
    # ordering (not the canonical tie-break) can put the fixed courses first.
    # One course a term makes the choice order visible as the term order.
    s = _topics_world(session, tmp_path, numbers=("300", "301"))
    plan = _plan(s, _student(s, ELECTIVES_LEFT), start_term="20279",
                 constraints=PlanningConstraints(max_courses_per_term=1, max_terms=10))
    placed = _placed(plan)
    fixed = [placed[k].term_code for k in ("01:198:314", "01:198:336") if k in placed]
    topics = [pc.term_code for pc in placed.values() if pc.is_topics_course]
    assert fixed and topics
    assert max(fixed) < min(topics)
