"""Phase 7.3: restriction re-ingest, history-backed evidence, Spring placement, section view.

Public SOC sample records; fabricated students; no network.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from app.domain.planning import OfferingEvidenceKind as K
from app.models import CourseSection, DataSource, SectionRestriction
from app.services.planning.offerings import OfferingIndex
from app.services.planning.service import generate_plan
from app.services.sources.sections import section_facts
from sqlalchemy import func, select

from coursepilot_ingestion.pipelines.courses import CourseIngestionPipeline
from coursepilot_ingestion.pipelines.sections import SectionIngestionPipeline
from coursepilot_ingestion.sources.soc import SocQuery

from .test_planning_engine import CS_DONE, START, _records, _student, cs  # noqa: F401

NOW = datetime(2026, 10, 22, tzinfo=UTC)


def _load_term(session, tmp_path, records, year, term, coverage="complete"):
    folder = tmp_path / f"{year}{term}"
    folder.mkdir(exist_ok=True)
    (folder / f"soc_courses_{year}_{term}_NB.json").write_text(json.dumps(records), "utf-8")
    query = SocQuery(year=year, term=term, campus="NB")
    CourseIngestionPipeline(session, folder).run(query, coverage=coverage)
    SectionIngestionPipeline(session, folder).run(query)
    session.commit()


def _count(session, model):
    return session.scalar(select(func.count()).select_from(model))


def test_restriction_reingest_is_idempotent(session, tmp_path):
    records = _records("soc_cs_courses_sample.json")
    _load_term(session, tmp_path, records, 2026, "9")
    restrictions, sections = _count(session, SectionRestriction), _count(session, CourseSection)
    sources = _count(session, DataSource)
    _load_term(session, tmp_path, records, 2026, "9")              # same archived payload again
    assert _count(session, SectionRestriction) == restrictions
    assert _count(session, CourseSection) == sections
    assert _count(session, DataSource) == sources                  # no new provenance row


def test_more_history_strengthens_offering_evidence(session, tmp_path):
    records = _records("soc_cs_courses_sample.json")
    course = records[0]["courseString"]
    _load_term(session, tmp_path, records, 2026, "9")
    one = OfferingIndex(session, {course}).evidence(course, "20279")
    assert (one.same_season_offered, one.same_season_loaded) == (1, 1)
    for year in (2023, 2024, 2025):                                 # SYNTHETIC: older Falls
        _load_term(session, tmp_path, records, year, "9")
    four = OfferingIndex(session, {course}).evidence(course, "20279")
    assert four.kind is K.HISTORICAL_SAME_SEASON
    assert (four.same_season_offered, four.same_season_loaded) == (4, 4)
    # A loaded Fall WITHOUT the course: offered stays 4, loaded becomes 5.
    _load_term(session, tmp_path, [r for r in records if r["courseString"] != course], 2022, "9")
    five = OfferingIndex(session, {course}).evidence(course, "20279")
    assert (five.same_season_offered, five.same_season_loaded) == (4, 5)


def test_older_terms_never_supply_prerequisite_rules(session, tmp_path):
    # Phase 7.3 finding: with history loaded, a Fall placement was checked
    # against the latest FALL's rules (e.g. 2022) although a newer Spring had
    # published the course's current rules. History is offering evidence only.
    records = _records("soc_cs_courses_sample.json")
    course = records[0]["courseString"]
    for year, term in ((2022, "9"), (2023, "9"), (2026, "1")):     # SYNTHETIC history
        _load_term(session, tmp_path, records, year, term)
    ev = OfferingIndex(session, {course}).evidence(course, "20279")
    assert ev.kind is K.HISTORICAL_SAME_SEASON
    assert ev.same_season_terms == ["20229", "20239"]
    assert ev.rules_term == "20261"


def test_a_complete_spring_2027_places_only_confirmed_courses(cs):  # noqa: F811
    # The CS world loads Spring 2027 partially (314, 336). Once that payload is
    # complete, history may not place anything else there.
    for source in cs.scalars(select(DataSource).where(DataSource.term_code == "20271")):
        source.coverage = "complete"
    cs.flush()
    plan = generate_plan(cs, _student(cs, CS_DONE), start_term=START)
    spring = [pc for t in plan.terms if t.term_code == "20271" for pc in t.courses]
    assert all(pc.offering.kind is K.CONFIRMED_IN_TERM for pc in spring)


def test_section_view_carries_provenance_and_public_names_only(session, tmp_path):
    records = _records("soc_cs_courses_sample.json")
    _load_term(session, tmp_path, records, 2026, "9")
    index = next(s["index"] for c in records for s in c["sections"])
    facts = section_facts(session, "20269", [index], reference_time=NOW)[index]
    source = session.scalar(select(DataSource).where(DataSource.term_code == "20269"))
    assert facts.course_string.provenance.data_source_id == str(source.id)
    assert facts.course_string.provenance.source == "soc_courses_json"
    assert facts.availability.value["status"] in ("open", "closed", "unknown")
    for r in facts.restrictions.value:
        assert (r["name"] is None) == (r["name_source"] is None)   # a name always says its source
        assert r["name_source"] in (None, "soc_subject_description", "soc_school_description")


def test_restriction_without_a_public_name_shows_only_its_code(session):
    from app.services.sources.sections import restriction_names

    row = SectionRestriction(ordinal=1, kind="major", code="006", unit_code=None)
    [name] = restriction_names(session, [row])
    assert (name.code, name.name, name.name_source) == ("006", None, None)


def test_a_full_manual_load_registers_the_term_as_ready(session, tmp_path):
    """Phase 7.3 finding: Spring 2027 loaded by the manual CLI stayed out of the
    term registry, so history placed 01:198:443 there although Rutgers was not
    offering it. A full load is now registered and validated like the watcher's."""
    from app.domain.planning import OfferingEvidenceKind as Kind
    from app.services.term_authority import TermAuthority, authority_of, term_states

    from coursepilot_ingestion.term_discovery import register_full_load

    records = _records("soc_cs_courses_sample.json")
    gone = records[0]["courseString"]
    _load_term(session, tmp_path, records, 2026, "1")                 # history: offered
    spring = [r for r in records if r["courseString"] != gone]       # not offered in 2027
    folder = tmp_path / "20271"
    folder.mkdir()
    archive = folder / "soc_courses_2027_1_NB.json"
    archive.write_text(json.dumps(spring), "utf-8")
    query = SocQuery(year=2027, term="1", campus="NB")
    courses = CourseIngestionPipeline(session, folder).run(query, coverage="partial")
    sections = SectionIngestionPipeline(session, folder).run(query)
    outcome = register_full_load(session, query, archive.read_bytes(), courses, sections)
    assert outcome.ok, outcome.error
    assert authority_of(term_states(session), "20271").authority is TermAuthority.READY
    ev = OfferingIndex(session, {gone}).evidence(gone, "20271")
    assert ev.kind is Kind.NOT_OFFERED_IN_TERM          # history decides nothing now
    source = session.scalar(select(DataSource).where(DataSource.term_code == "20271"))
    assert source.coverage == "partial"                  # validated, never "complete"


def test_a_course_missing_from_a_published_term_is_never_placed_there(cs):  # noqa: F811
    """Phase 7.3 rule: once Rutgers has published a term (CoursePilot holds a
    non-empty SOC payload for it), only courses it LISTS are placed there -
    whatever the coverage, registry row or not. The CS world's Spring 2027 is a
    PARTIAL payload listing only 314 and 336, with no registry row (the shape
    that let history place 01:198:443 in the real Spring 2027)."""
    student = _student(cs, [c for c in CS_DONE if c[0] not in ("01:198:314", "01:198:336")]
                       + [])
    plan = generate_plan(cs, student, start_term=START)
    spring = [pc for t in plan.terms if t.term_code == START for pc in t.courses]
    assert spring and {pc.course for pc in spring} <= {"01:198:314", "01:198:336"}
    assert all(pc.offering.kind is K.CONFIRMED_IN_TERM for pc in spring)
    # Not listed for Spring 2027 yet - but still plannable LATER, on labelled history.
    later = [pc for t in plan.terms if t.term_code > START for pc in t.courses]
    assert any(pc.offering.kind is K.HISTORICAL_SAME_SEASON for pc in later)
    absent = OfferingIndex(cs, {"01:198:344"}).evidence("01:198:344", START)
    assert absent.kind is K.NOT_OFFERED_IN_TERM
