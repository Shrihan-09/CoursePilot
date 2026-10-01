"""Requirement eligibility is reconciled, not appended (Phase 6.3).

Eligibility (`requirement_course_option`) is DERIVED data: a curated rule
("CS courses numbered 300+", "01:640:244 or 01:640:252") applied to the
current canonical course dataset. Two defects, both reproduced here BEFORE
the fix:

  A. STALE ADDITIONS - eligibility was materialized once, when the
     requirement definition loaded. A course first seen in a later SOC term
     matched the rule but never became eligible. On the development database
     six real CS courses (01:198:415, 431, 442, 443, 452, 494) were missing
     from CS_ELECTIVES after Phase 6.2 loaded four more terms.
  B. STALE REMOVALS - reloading a changed definition only ever ADDED rows, so
     a course removed from a curated list stayed eligible forever.

The invariant:

    given the curated definition and the current course table, the
    eligibility set is exactly what the definition implies - no more, no less.

"Current course table" means course IDENTITIES across every loaded term,
not the courses offered in any one term: a requirement names courses, and
whether a course is offered next semester is a planning question, not a
degree-requirement one.
"""

from __future__ import annotations

import copy
import json
import pathlib

import pytest
from app.models import Course, Requirement, RequirementCourseOption
from sqlalchemy import select

from coursepilot_ingestion.loaders.requirements import RequirementLoader
from coursepilot_ingestion.pipelines.courses import CourseIngestionPipeline
from coursepilot_ingestion.sources.soc import SocQuery

from .conftest import CS_REQUIREMENTS, FIXTURE_DIR, MATH_REQUIREMENTS

CS_SOC = FIXTURE_DIR / "soc_cs_courses_sample.json"
MATH_SOC = FIXTURE_DIR / "soc_math_courses_sample.json"
FALL_25 = SocQuery(year=2025, term="9", campus="NB")
FALL_26 = SocQuery(year=2026, term="9", campus="NB")


def _write(cache: pathlib.Path, query: SocQuery, records: list[dict]) -> None:
    (cache / f"soc_courses_{query.year}_{query.term}_{query.campus}.json").write_text(
        json.dumps(records), encoding="utf-8")


def _eligible(session, requirement_code: str, category: str | None = None) -> set[str]:
    stmt = (select(Course.course_string)
            .join(RequirementCourseOption, RequirementCourseOption.course_id == Course.id)
            .join(Requirement, Requirement.id == RequirementCourseOption.requirement_id)
            .where(Requirement.code == requirement_code))
    if category is not None:
        stmt = stmt.where(RequirementCourseOption.category == category)
    return set(session.scalars(stmt).all())


def test_a_course_first_seen_in_a_later_term_becomes_eligible(session, tmp_path) -> None:
    """A: the requirement loads before the course exists; the course arrives
    with a later term; it must become eligible without reloading anything."""
    records = json.loads(CS_SOC.read_bytes())
    later = [r for r in records if r["courseString"] == "01:198:336"]
    earlier = [r for r in records if r["courseString"] != "01:198:336"]
    _write(tmp_path, FALL_25, earlier)
    _write(tmp_path, FALL_26, later)

    CourseIngestionPipeline(session, tmp_path).run(FALL_25)
    RequirementLoader(session).load_file(CS_REQUIREMENTS)
    session.commit()
    assert "01:198:336" not in _eligible(session, "CS_ELECTIVES")

    CourseIngestionPipeline(session, tmp_path).run(FALL_26)
    assert "01:198:336" in _eligible(session, "CS_ELECTIVES")


def test_a_course_removed_from_a_curated_list_stops_being_eligible(session, tmp_path) -> None:
    """B: reloading a changed definition must REMOVE what it no longer names."""
    _write(tmp_path, FALL_26, json.loads(MATH_SOC.read_bytes()))
    CourseIngestionPipeline(session, tmp_path).run(FALL_26)
    definition = json.loads(MATH_REQUIREMENTS.read_bytes())
    RequirementLoader(session).load(definition, json.dumps(definition).encode())
    session.commit()
    assert "01:640:244" in _eligible(session, "MATH_DIFFEQ")
    assert "01:640:361" in _eligible(session, "MATH_UPPER")

    changed = copy.deepcopy(definition)
    diffeq = next(r for r in changed["requirements"] if r["code"] == "MATH_DIFFEQ")
    diffeq["courses"] = ["01:640:252"]                      # 244 dropped
    upper = next(r for r in changed["requirements"] if r["code"] == "MATH_UPPER")
    upper["eligible_course_query"]["max_course_number"] = 399    # 4xx dropped
    RequirementLoader(session).load(changed, json.dumps(changed).encode())
    session.commit()

    assert _eligible(session, "MATH_DIFFEQ") == {"01:640:252"}
    # The query-derived (uncategorized) rows lose every 4xx course; 411/412
    # and 451/452 stay as ANALYSIS/ALGEBRA rows because the definition still
    # names them in its categories.
    upper_now = _eligible(session, "MATH_UPPER", category="")
    assert "01:640:361" in upper_now
    assert not any(int(c.split(":")[2]) >= 400 for c in upper_now)
    assert "01:640:411" in _eligible(session, "MATH_UPPER", category="ANALYSIS")


def test_a_query_narrowed_by_offering_unit_drops_graduate_courses(session, tmp_path) -> None:
    """The query supports an offering unit, so "undergraduate CS 300+" can
    exclude 16:198 graduate courses - a real defect in the current CS data."""
    records = json.loads(CS_SOC.read_bytes())
    grad = copy.deepcopy(next(r for r in records if r["courseString"] == "01:198:336"))
    grad.update(courseString="16:198:536", offeringUnitCode="16", courseNumber="536")
    _write(tmp_path, FALL_26, [*records, grad])
    CourseIngestionPipeline(session, tmp_path).run(FALL_26)

    definition = json.loads(CS_REQUIREMENTS.read_bytes())
    electives = next(r for r in definition["requirements"] if r["code"] == "CS_ELECTIVES")
    RequirementLoader(session).load(definition, json.dumps(definition).encode())
    session.commit()
    assert "16:198:536" in _eligible(session, "CS_ELECTIVES")       # as curated today

    electives["eligible_course_query"]["offering_unit_code"] = "01"
    RequirementLoader(session).load(definition, json.dumps(definition).encode())
    session.commit()
    assert "16:198:536" not in _eligible(session, "CS_ELECTIVES")
    assert "01:198:336" in _eligible(session, "CS_ELECTIVES")
