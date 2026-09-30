"""Term-scoped prerequisite persistence through the real ingestion path (Phase 6.2).

Input is the real archived SOC fixture for CS courses, written into a fetcher
cache so `CourseIngestionPipeline` reads it exactly as it reads a real term:
fetch (cache hit) -> parse -> normalize -> validate -> load courses ->
load prerequisites.
"""

from __future__ import annotations

import copy
import json
import pathlib

import pytest
from app.domain.prerequisites import parse
from app.models import Course, CourseOffering, CoursePrerequisite, DataSource, PrerequisiteReference
from sqlalchemy import func, select

from coursepilot_ingestion.pipelines.courses import CourseIngestionPipeline
from coursepilot_ingestion.sources.soc import SocQuery

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "soc_cs_courses_sample.json"
FALL_26 = SocQuery(year=2026, term="9", campus="NB")
FALL_25 = SocQuery(year=2025, term="9", campus="NB")


def _write(cache: pathlib.Path, query: SocQuery, records: list[dict]) -> None:
    name = f"soc_courses_{query.year}_{query.term}_{query.campus}.json"
    (cache / name).write_text(json.dumps(records), encoding="utf-8")


@pytest.fixture
def records() -> list[dict]:
    return json.loads(FIXTURE.read_bytes())


def _load(session, cache, query, **kw):
    return CourseIngestionPipeline(session, cache).run(query, **kw)


def _prereq(session, course_string, term):
    return session.scalar(
        select(CoursePrerequisite).join(Course, Course.id == CoursePrerequisite.course_id)
        .where(Course.course_string == course_string, CoursePrerequisite.term_code == term))


def _snapshot(session):
    prereqs = session.execute(select(
        CoursePrerequisite.offering_id, CoursePrerequisite.term_code, CoursePrerequisite.raw_text,
        CoursePrerequisite.classification, CoursePrerequisite.canonical_text,
        CoursePrerequisite.condition_note, CoursePrerequisite.updated_at,
    ).order_by(CoursePrerequisite.offering_id)).all()
    refs = session.execute(select(
        PrerequisiteReference.prerequisite_id, PrerequisiteReference.course_string,
        PrerequisiteReference.course_id,
    ).order_by(PrerequisiteReference.prerequisite_id, PrerequisiteReference.course_string)).all()
    return prereqs, refs


# ==========================================================================
# what Rutgers published vs what CoursePilot understood
# ==========================================================================


def test_the_raw_soc_text_is_stored_verbatim_with_provenance(session, tmp_path, records) -> None:
    _write(tmp_path, FALL_26, records)
    _load(session, tmp_path, FALL_26, coverage="complete", coverage_note="test fixture")

    published = next(r for r in records if r["courseString"] == "01:198:205")
    row = _prereq(session, "01:198:205", "20269")
    assert row.raw_text == published["preReqNotes"]            # markup and all
    assert "<em>" in row.raw_text or " or " in row.raw_text
    assert row.canonical_text == "(01:198:111 or 14:332:252)"
    assert row.classification == "parsed"
    assert row.parser_version == "1"

    source = session.get(DataSource, row.source_id)
    assert (source.kind, source.term_code) == ("rutgers_official_api", "20269")
    assert source.raw_payload_ref.endswith("soc_courses_2026_9_NB.json")
    assert source.coverage == "complete"


def test_every_row_matches_a_fresh_parse_of_its_raw_text(session, tmp_path, records) -> None:
    """The interpretation is reproducible from the published text alone."""
    _write(tmp_path, FALL_26, records)
    _load(session, tmp_path, FALL_26)
    rows = session.scalars(select(CoursePrerequisite)).all()
    assert rows
    for row in rows:
        if row.raw_text is None:
            continue
        again = parse(row.raw_text)
        assert (again.classification.value, again.canonical_text) == (
            row.classification, row.canonical_text)


def test_a_minimum_grade_note_is_kept_beside_the_expression(session, tmp_path, records) -> None:
    _write(tmp_path, FALL_26, records)
    _load(session, tmp_path, FALL_26)
    row = _prereq(session, "01:198:344", "20269")
    assert row.classification == "parsed"
    assert "minimum_grade" in row.condition_kinds
    assert "C" in row.condition_note


def test_unsupported_forms_are_stored_not_guessed(session, tmp_path, records) -> None:
    _write(tmp_path, FALL_26, records)
    _load(session, tmp_path, FALL_26)
    row = _prereq(session, "01:640:250", "20269")
    assert row.classification == "unsupported_minimum_course_level"
    assert row.expression["unsupported"] == "minimum_course_level"
    assert row.raw_text.startswith("Any Course EQUAL or GREATER Than")


# ==========================================================================
# referenced but unloaded course identities
# ==========================================================================


def test_a_referenced_course_never_loaded_survives_without_fake_metadata(session, tmp_path, records) -> None:
    _write(tmp_path, FALL_26, records)
    stats = _load(session, tmp_path, FALL_26)

    ref = session.scalar(select(PrerequisiteReference)
                         .where(PrerequisiteReference.course_string == "14:332:252"))
    assert ref is not None and ref.course_id is None
    # ...and no Course row was invented for it.
    assert session.scalar(select(Course).where(Course.course_string == "14:332:252")) is None

    resolved = session.scalar(select(PrerequisiteReference)
                              .where(PrerequisiteReference.course_string == "01:198:111"))
    assert resolved.course_id is not None
    assert stats.prerequisites["references_unresolved"] > 0


# ==========================================================================
# idempotency and reload semantics
# ==========================================================================


def test_reloading_the_same_archive_changes_nothing(session, tmp_path, records) -> None:
    _write(tmp_path, FALL_26, records)
    first = _load(session, tmp_path, FALL_26)
    before = _snapshot(session)
    second = _load(session, tmp_path, FALL_26)

    assert _snapshot(session) == before
    assert second.prerequisites.get("inserted", 0) == 0
    assert second.prerequisites.get("updated", 0) == 0
    assert second.prerequisites["unchanged"] == first.prerequisites["inserted"]


def test_a_changed_prerequisite_in_the_same_term_is_updated_in_place(session, tmp_path, records) -> None:
    _write(tmp_path, FALL_26, records)
    _load(session, tmp_path, FALL_26)

    changed = copy.deepcopy(records)
    target = next(r for r in changed if r["courseString"] == "01:198:205")
    target["preReqNotes"] = "(01:198:111 INTRO TO COMPUTER SCIENCE )"
    gone = next(r for r in changed if r["courseString"] == "01:198:345")
    gone["preReqNotes"] = ""
    _write(tmp_path, FALL_26, changed)
    stats = _load(session, tmp_path, FALL_26)

    assert _prereq(session, "01:198:205", "20269").canonical_text == "01:198:111"
    assert _prereq(session, "01:198:345", "20269") is None
    assert stats.prerequisites["updated"] >= 1 and stats.prerequisites["deleted"] == 1
    count = session.scalar(select(func.count()).select_from(CoursePrerequisite)
                           .join(Course, Course.id == CoursePrerequisite.course_id)
                           .where(Course.course_string == "01:198:205"))
    assert count == 1


# ==========================================================================
# term scoping
# ==========================================================================


def test_each_term_keeps_its_own_prerequisite(session, tmp_path, records) -> None:
    """Fall 2025: A. Fall 2026: A or B. Neither overwrites the other."""
    older = copy.deepcopy(records)
    course = next(r for r in older if r["courseString"] == "01:198:205")
    course["preReqNotes"] = "(01:198:111 INTRO TO COMPUTER SCIENCE )"
    _write(tmp_path, FALL_25, older)
    _write(tmp_path, FALL_26, records)

    _load(session, tmp_path, FALL_26)
    _load(session, tmp_path, FALL_25)

    assert _prereq(session, "01:198:205", "20259").canonical_text == "01:198:111"
    assert _prereq(session, "01:198:205", "20269").canonical_text == "(01:198:111 or 14:332:252)"
    terms = session.scalars(select(CourseOffering.term_code)
                            .join(Course, Course.id == CourseOffering.course_id)
                            .where(Course.course_string == "01:198:205")).all()
    assert sorted(terms) == ["20259", "20269"]


def test_loading_an_older_term_does_not_overwrite_newer_course_fields(session, tmp_path, records) -> None:
    older = copy.deepcopy(records)
    for record in older:
        record["title"] = "OLD TITLE " + record["title"]
    _write(tmp_path, FALL_25, older)
    _write(tmp_path, FALL_26, records)

    _load(session, tmp_path, FALL_26)
    stats = _load(session, tmp_path, FALL_25)

    course = session.scalar(select(Course).where(Course.course_string == "01:198:205"))
    assert not course.title.startswith("OLD TITLE")
    assert stats.courses_kept_newer > 0


# ==========================================================================
# coverage
# ==========================================================================


def test_a_limited_load_cannot_claim_a_complete_term(session, tmp_path, records) -> None:
    _write(tmp_path, FALL_26, records)
    with pytest.raises(ValueError, match="cannot be marked complete"):
        _load(session, tmp_path, FALL_26, limit=3, coverage="complete")
    with pytest.raises(ValueError, match="complete, partial or unknown"):
        _load(session, tmp_path, FALL_26, coverage="probably")


def test_partial_coverage_is_recorded_with_its_evidence(session, tmp_path, records) -> None:
    _write(tmp_path, FALL_26, records)
    _load(session, tmp_path, FALL_26, coverage="partial", coverage_note="pre-publication snapshot")
    source = session.scalar(select(DataSource).where(DataSource.term_code == "20269"))
    assert (source.coverage, source.coverage_note) == ("partial", "pre-publication snapshot")


def test_reference_resolution_does_not_depend_on_load_order(session, tmp_path, records) -> None:
    """Load a term whose prerequisite names a course nobody has loaded yet,
    THEN a term that introduces that course: the earlier edge must resolve.

    Reproduces what loading the real archives oldest-first exposed."""
    newcomer = next(r for r in records if r["courseString"] == "01:198:111")
    older = [r for r in records if r["courseString"] != "01:198:111"]
    _write(tmp_path, FALL_25, older)
    _write(tmp_path, FALL_26, [newcomer])

    _load(session, tmp_path, FALL_25)
    ref = session.scalar(select(PrerequisiteReference).join(CoursePrerequisite)
                         .where(PrerequisiteReference.course_string == "01:198:111",
                                CoursePrerequisite.term_code == "20259"))
    assert ref.course_id is None                        # unknown when first seen

    stats = _load(session, tmp_path, FALL_26)
    session.refresh(ref)
    assert ref.course_id is not None                    # resolved once it exists
    assert stats.prerequisites["references_resolved_late"] >= 1
