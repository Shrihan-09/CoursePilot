"""Phase 7.2.4: target-term authority, term discovery, ingestion validation.

The CS planning world (test_planning_engine) loads Spring 2027 PARTIALLY:
only 01:198:314 and 01:198:336, other spring courses come from Spring 2026
history. That is precisely the situation that produced "Spring 2027 isn't
published yet" - so each test flips only the term registry and checks what
the planner may and may not claim. Students are fabricated; course records
are the public SOC samples every planning test uses. No network.
"""

from __future__ import annotations

import hashlib
import json
import types
from datetime import UTC, datetime

import pytest
from app.domain.planning import OfferingEvidenceKind as K
from app.domain.planning import OfferingStatus, PlanningConstraints
from app.models import DataSource, RutgersTerm, StudentPlan
from app.models.terms import INGESTED, INGESTION_FAILED, NOT_PUBLISHED, PUBLISHED
from app.services.planning.offerings import PLACEABLE, OfferingIndex
from app.services.planning.saved import _encode, offering_assumption_reasons
from app.services.planning.service import generate_plan
from app.services.term_authority import TermAuthority, term_states
from sqlalchemy import select

from coursepilot_ingestion.pipelines.courses import CourseIngestionPipeline
from coursepilot_ingestion.pipelines.sections import SectionIngestionPipeline
from coursepilot_ingestion.term_discovery import (
    candidate_terms,
    compare_sources,
    discover,
    ingest_term,
)

from .conftest import FIXTURE_DIR
from .test_planning_engine import CS_DONE, _student, cs  # noqa: F401

NOW = datetime(2026, 10, 19, 12, tzinfo=UTC)
SPRING27, SPRING28 = "20271", "20281"
CONFIRMED_THERE = {"01:198:314", "01:198:336"}


def _registry(session, term, *, published=True, ingested=True, sections=10):
    session.add(RutgersTerm(
        term_code=term, campus="NB", source="test", course_count=10, section_count=sections,
        publication_state=PUBLISHED if published else NOT_PUBLISHED,
        ingestion_state=INGESTED if ingested else INGESTION_FAILED,
        ingested_hash="f" * 64 if ingested else None, content_hash="f" * 64,
        last_checked_at=NOW, availability_supported=True, disagreements={}))
    session.flush()


def _historical_spring_course(index: OfferingIndex) -> str:
    """A course offered in Spring 2026 but absent from the (partial) Spring 2027 load."""
    return next(k for k, terms in sorted(index.terms.items())
                if "20261" in terms and SPRING27 not in terms)


# A. Published target term overrides historical evidence -------------------
def test_published_target_term_overrides_history(cs):  # noqa: F811
    course = _historical_spring_course(OfferingIndex(cs))
    before = OfferingIndex(cs).evidence(course, SPRING27)
    assert before.kind is K.HISTORICAL_SAME_SEASON          # control: today's behaviour
    _registry(cs, SPRING27)
    after = OfferingIndex(cs).evidence(course, SPRING27)
    assert after.kind is K.NOT_OFFERED_IN_TERM and after.kind not in PLACEABLE
    assert after.status is OfferingStatus.NOT_FOUND_IN_PUBLISHED_TERM
    assert after.target_term_authority == "ready"
    # The history is still there as context - it just decides nothing.
    assert "20261" in after.observed_terms


# B. Historically offered but absent from the published term: never confirmed
def test_planner_never_places_history_into_a_published_term(cs):  # noqa: F811
    _registry(cs, SPRING27)
    student = _student(cs, CS_DONE)
    plan = generate_plan(cs, student, start_term=SPRING27)
    in_spring27 = [pc for t in plan.terms if t.term_code == SPRING27 for pc in t.courses]
    assert all(pc.offering.kind is K.CONFIRMED_IN_TERM for pc in in_spring27)
    assert {pc.course for pc in in_spring27} <= CONFIRMED_THERE


def test_published_but_unloaded_term_is_pending_not_historical(cs):  # noqa: F811
    _registry(cs, SPRING27, ingested=False)
    course = _historical_spring_course(OfferingIndex(cs))
    ev = OfferingIndex(cs).evidence(course, SPRING27)
    assert ev.kind is K.TERM_DATA_PENDING and ev.status is OfferingStatus.UNKNOWN
    plan = generate_plan(cs, _student(cs, CS_DONE), start_term=SPRING27)
    assert "target_term_data_pending" in {a.code for a in plan.assumptions}
    assert all(pc.offering.kind is not K.HISTORICAL_SAME_SEASON
               for t in plan.terms if t.term_code == SPRING27 for pc in t.courses)


# C. Unpublished future term: history participates, labelled -------------
def test_unpublished_future_term_uses_labelled_history(cs):  # noqa: F811
    course = _historical_spring_course(OfferingIndex(cs))
    unchecked = OfferingIndex(cs).evidence(course, SPRING28)
    assert unchecked.kind is K.HISTORICAL_SAME_SEASON
    assert unchecked.status is OfferingStatus.HISTORICALLY_LIKELY
    assert unchecked.target_term_authority == "unchecked"      # never "Rutgers hasn't published"
    _registry(cs, SPRING28, published=False, ingested=False, sections=0)
    checked = OfferingIndex(cs).evidence(course, SPRING28)
    assert checked.kind is K.HISTORICAL_SAME_SEASON and checked.kind in PLACEABLE
    assert checked.target_term_authority == "not_published"
    assert checked.target_term_checked_at is not None


# D. Newly published term invalidates a historical assumption --------------
def test_saved_plan_goes_stale_when_publication_contradicts_history(cs):  # noqa: F811
    student = _student(cs, CS_DONE)
    plan = generate_plan(cs, student, start_term=SPRING27, constraints=PlanningConstraints())
    assumed = [pc for t in plan.terms if t.term_code == SPRING27 for pc in t.courses
               if pc.offering.kind is K.HISTORICAL_SAME_SEASON]
    assert assumed, "the fixture must place something in Spring 2027 on history"
    row = StudentPlan(result_blob=_encode(plan))
    assert offering_assumption_reasons(cs, row) == []
    _registry(cs, SPRING27, ingested=False)
    assert offering_assumption_reasons(cs, row) == ["target_term_published"]
    cs.query(RutgersTerm).delete()
    _registry(cs, SPRING27)
    assert offering_assumption_reasons(cs, row) == ["offering_assumption_contradicted"]


def test_validated_term_data_is_never_downgraded(cs):  # noqa: F811
    _registry(cs, "20269", published=False, ingested=False, sections=0)   # a later empty answer
    assert term_states(cs)["20269"].authority is TermAuthority.READY


# E. Discovery finds a newly available term (nothing hard-coded) ------------
def _payload(term_records):
    content = json.dumps(term_records).encode()
    return content, hashlib.sha256(content).hexdigest()


def test_discovery_finds_a_newly_published_term(session):
    sample = json.loads((FIXTURE_DIR / "soc_cs_courses_sample.json").read_bytes())
    published = {"20269"}

    def fetch(q):
        return _payload(sample if q.term_code in published else [])

    window = candidate_terms("20269")
    assert window[0] == "20267" and "20271" in window and "20279" in window
    discover(session, fetch, terms=window, pause=0, now=lambda: NOW)
    rows = {r.term_code: r for r in session.scalars(select(RutgersTerm))}
    assert rows["20269"].publication_state == PUBLISHED
    assert rows["20271"].publication_state == NOT_PUBLISHED
    # Later Rutgers publishes Spring 2027: the same run discovers it.
    published.add("20271")
    discover(session, fetch, terms=window, pause=0, now=lambda: NOW)
    spring = session.scalar(select(RutgersTerm).where(RutgersTerm.term_code == "20271"))
    assert spring.publication_state == PUBLISHED and spring.first_published_seen_at is not None
    assert spring.section_count > 0
    # Discovered is not ingested is not ready.
    assert term_states(session)["20271"].authority is TermAuthority.PUBLISHED_PENDING


def test_failed_request_never_means_not_published(session):
    def fetch(q):
        raise OSError("network down")

    discover(session, fetch, terms=["20271"], pause=0, now=lambda: NOW)
    row = session.scalar(select(RutgersTerm))
    assert row.publication_state == "check_failed" and row.last_error == "FETCH_FAILED"
    assert "20271" not in term_states(session)


# F. Failed / incomplete ingestion never makes a term usable ---------------
def _discovered(session, tmp_path, term="20279"):
    sample = json.loads((FIXTURE_DIR / "soc_cs_courses_sample.json").read_bytes())
    content, digest = _payload(sample)
    (tmp_path / f"soc_courses_{term[:4]}_{term[4]}_NB.json").write_bytes(content)
    discover(session, lambda q: (content, digest), terms=[term], pause=0, now=lambda: NOW)
    return session.scalar(select(RutgersTerm).where(RutgersTerm.term_code == term))


def _runners(session, tmp_path):
    return {
        "run_courses": lambda q: CourseIngestionPipeline(session, tmp_path).run(
            q, coverage="unknown"),
        "run_sections": lambda q: SectionIngestionPipeline(session, tmp_path).run(q),
    }


def test_validated_ingestion_makes_a_term_ready(session, tmp_path):
    row = _discovered(session, tmp_path)
    outcome = ingest_term(session, row, **_runners(session, tmp_path), now=lambda: NOW)
    assert outcome.ok, outcome
    assert term_states(session)["20279"].authority is TermAuthority.READY
    source = session.scalar(select(DataSource).where(DataSource.term_code == "20279"))
    # READY (authoritative for what it lists) is not COMPLETE: completeness needs
    # the Phase 7.3 watcher's stability evidence, never validation alone.
    assert source.coverage == "partial"


@pytest.mark.parametrize("break_it,code", [
    ("sections_raise", "PIPELINE_ERROR"),
    ("rejected", "RECORDS_REJECTED"),
    ("other_payload", "PAYLOAD_CHANGED"),
])
def test_failed_ingestion_never_marks_a_term_usable(session, tmp_path, break_it, code):
    row = _discovered(session, tmp_path)
    runners = _runners(session, tmp_path)
    real_sections = runners["run_sections"]
    if break_it == "sections_raise":
        runners["run_sections"] = lambda q: (_ for _ in ()).throw(RuntimeError("boom"))
    elif break_it == "rejected":
        runners["run_sections"] = lambda q: types.SimpleNamespace(
            **{**real_sections(q).model_dump(), "validation_failed": 3})
    else:
        runners["run_sections"] = lambda q: types.SimpleNamespace(
            **{**real_sections(q).model_dump(), "source_content_hash": "0" * 64})
    outcome = ingest_term(session, row, **runners, now=lambda: NOW)
    assert not outcome.ok and outcome.error == code
    assert row.ingestion_state == INGESTION_FAILED
    assert term_states(session)["20279"].authority is TermAuthority.PUBLISHED_PENDING
    coverage = session.scalar(select(DataSource.coverage).where(DataSource.term_code == "20279"))
    assert coverage != "complete"


# N. Source disagreement is surfaced, not silently overwritten ------------
def test_source_disagreement_is_recorded_not_resolved():
    courses = [{"sections": [{"index": "11111", "openStatus": True},
                             {"index": "22222", "openStatus": False},
                             {"index": "33333", "openStatus": True}]}]
    listed = {"11111", "22222", "99999"}
    assert compare_sources(courses, listed) == {
        "sections": 3, "open_sections_listed": 3,
        "listed_open_but_unknown_section": 1,
        "open_in_courses_not_listed": 1,
        "listed_open_but_closed_in_courses": 1,
    }
    # Neither input was modified to agree with the other.
    assert courses[0]["sections"][1]["openStatus"] is False and "99999" in listed


def test_discovery_records_the_disagreement_on_the_term(session):
    sample = json.loads((FIXTURE_DIR / "soc_cs_courses_sample.json").read_bytes())
    indexes = [s["index"] for c in sample for s in c["sections"]]
    listed = json.dumps(indexes[1:] + ["99999"]).encode()       # one dropped, one unknown
    discover(session, lambda q: _payload(sample), fetch_open=lambda q: listed,
             terms=["20279"], pause=0, now=lambda: NOW)
    row = session.scalar(select(RutgersTerm))
    assert row.availability_supported is True
    assert row.disagreements["listed_open_but_unknown_section"] == 1
    assert row.disagreements["sections"] == len(indexes)
