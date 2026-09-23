"""Compressed cache payload: round trip and equivalence (Parts 5-6).

The public contract is unchanged - `GET /api/v1/student/audit` still returns
a `DegreeAuditResult` - so the only thing that may change is the internal
storage representation. The invariant:

> fresh Degree Engine result == cache-hit reconstructed result

Tested at two levels, because they can fail independently: the serializer in
isolation over hand-built edge cases, and a real audit end to end over
representative academic shapes.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import decimal
import os
import uuid
import zlib

import pytest

from app.domain.audit import (
    Allocation,
    AuditFinding,
    AuditStatus,
    CourseRef,
    DegreeAuditResult,
    RequirementResult,
    RequirementStatus,
    RuleResult,
    Severity,
)
from app.models import (
    Course,
    DataSource,
    Program,
    ProgramRule,
    ProgramVersion,
    Requirement,
    RequirementCourseOption,
    School,
    Student,
    StudentCourse,
    Subject,
    UserAccount,
)
from app.services.audit.cache import decode_result, encode_result
from app.services.audit.cached_audit import audit_with_cache
from app.services.audit.engine import DegreeAuditEngine

requires_db = pytest.mark.db


@contextlib.contextmanager
def _session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import NullPool

    url = os.environ.get(
        "DATABASE_URL_SYNC",
        "postgresql+psycopg://coursepilot:coursepilot@localhost:5432/coursepilot_test",
    )
    engine = create_engine(url, poolclass=NullPool)
    try:
        with sessionmaker(engine)() as session:
            yield session
    finally:
        engine.dispose()


# ==========================================================================
# serializer, in isolation
# ==========================================================================


def _round_trip(result: DegreeAuditResult) -> DegreeAuditResult:
    return decode_result(encode_result(result))


def test_an_empty_result_round_trips() -> None:
    """Empty lists must stay empty lists, not become null."""
    result = DegreeAuditResult(
        program_name="P", program_code="C", degree_type="BA",
        catalog_year="2026-2027", status=AuditStatus.INSUFFICIENT_DATA,
    )
    restored = _round_trip(result)
    assert restored.model_dump_json() == result.model_dump_json()
    assert restored.requirements == []
    assert restored.findings == []
    assert restored.allocation == []


def test_decimals_survive_as_decimals() -> None:
    """Credits are Numeric(4,1). A float round trip would silently change
    academic values."""
    result = DegreeAuditResult(
        program_name="P", program_code="C", degree_type="BA",
        catalog_year="2026-2027", status=AuditStatus.INCOMPLETE,
        credits_completed=decimal.Decimal("38.5"),
        credits_applicable_to_degree=decimal.Decimal("34.5"),
        credits_excluded=decimal.Decimal("4.0"),
        credits_in_progress=decimal.Decimal("3.0"),
        credits_required_min=decimal.Decimal("120.0"),
        credits_remaining=decimal.Decimal("85.5"),
    )
    restored = _round_trip(result)
    assert restored.credits_completed == decimal.Decimal("38.5")
    assert isinstance(restored.credits_completed, decimal.Decimal)
    assert restored.model_dump_json() == result.model_dump_json()


def test_the_preexisting_int_decimal_wart_round_trips_unchanged() -> None:
    """Phase 5.6/5.7 left an engine-side `int` in a Decimal field alone.

    It must survive the cache untouched: this phase optimizes storage, and
    silencing an engine wart by normalizing values during serialization
    would be exactly the 'silently convert semantically meaningful values'
    that is forbidden.
    """
    result = DegreeAuditResult(
        program_name="P", program_code="C", degree_type="BA",
        catalog_year="2026-2027", status=AuditStatus.INCOMPLETE,
        requirements=[
            RequirementResult(
                requirement_code="R", requirement_name="R",
                requirement_type="choose_n", status=RequirementStatus.UNSATISFIED,
                satisfied_credits=0,  # an int in a Decimal field, on purpose
                reason="none",
            )
        ],
    )
    payload = result.model_dump_json()
    assert _round_trip(result).model_dump_json() == payload


def test_optional_and_none_fields_round_trip() -> None:
    result = DegreeAuditResult(
        program_name="P", program_code="C", degree_type="BA",
        catalog_year="2026-2027", status=AuditStatus.INCOMPLETE,
        credits_required_min=None, credits_remaining=None,
        requirements=[
            RequirementResult(
                requirement_code="R", requirement_name="R",
                requirement_type="all_of", status=RequirementStatus.SATISFIED,
                needed_count=None, needed_credits=None,
                needed_distinct_categories=None,
                source_prose=None, curation_status=None, reason="ok",
            )
        ],
    )
    assert _round_trip(result).model_dump_json() == result.model_dump_json()


def test_a_fully_populated_nested_result_round_trips() -> None:
    """Every currently represented field, including nested children."""
    course = CourseRef(
        course_id=str(uuid.uuid4()), course_string="01:198:112",
        supplement_code="LB", title="Data Structures",
        credits=decimal.Decimal("4.0"),
    )
    child = RequirementResult(
        requirement_code="CHILD", requirement_name="Child",
        requirement_type="course", status=RequirementStatus.SATISFIED,
        needed_count=1, satisfied_count=1,
        needed_credits=decimal.Decimal("4.0"),
        satisfied_credits=decimal.Decimal("4.0"),
        needed_distinct_categories=2, distinct_categories=2,
        allocated_courses=[course], eligible_not_allocated=[course],
        reason="satisfied", source_prose="prose",
        curation_status="curated_from_prose",
    )
    result = DegreeAuditResult(
        program_name="Computer Science", program_code="198", degree_type="BA",
        catalog_year="2026-2027", status=AuditStatus.INCOMPLETE,
        credits_completed=decimal.Decimal("38.0"),
        credits_applicable_to_degree=decimal.Decimal("34.0"),
        credits_excluded=decimal.Decimal("4.0"),
        credits_in_progress=decimal.Decimal("4.0"),
        credits_required_min=decimal.Decimal("120.0"),
        credits_remaining=decimal.Decimal("86.0"),
        requirements=[
            RequirementResult(
                requirement_code="ROOT", requirement_name="Root",
                requirement_type="all_of", status=RequirementStatus.PARTIALLY_SATISFIED,
                children=[child], reason="partial",
            )
        ],
        rules=[
            RuleResult(
                rule_code="RULE", rule_name="Rule", rule_type="course_exclusion",
                status=RequirementStatus.SATISFIED, observed_count=1,
                allowed_count=2, affected_courses=[course], reason="ok",
                source_prose="p", curation_status="unverified",
            )
        ],
        allocation=[
            Allocation(
                course=course, requirement_code="CHILD",
                requirement_name="Child", requirement_system="major",
                shared_with_systems=["core"], term_code="20269",
                status="completed", credits_applied=decimal.Decimal("4.0"),
                reason="allocated",
            )
        ],
        sharing_policy="share_across_systems",
        findings=[
            AuditFinding(severity=Severity.BLOCKING, code="c", message="m",
                         requirement_code="ROOT", remediation="r")
        ],
        excluded_courses=[course],
        unallocated_courses=[course],
        disclaimers=["advisory only"],
    )
    restored = _round_trip(result)
    assert restored.model_dump_json() == result.model_dump_json()
    assert restored.requirements[0].children[0].allocated_courses[0].supplement_code == "LB"
    assert restored.allocation[0].shared_with_systems == ["core"]


def test_no_field_is_silently_dropped() -> None:
    """A field added to DegreeAuditResult later must survive the cache."""
    result = DegreeAuditResult(
        program_name="P", program_code="C", degree_type="BA",
        catalog_year="2026-2027", status=AuditStatus.INCOMPLETE,
    )
    restored = _round_trip(result)
    assert set(restored.model_dump()) == set(result.model_dump())


def test_decoding_rejects_anything_it_does_not_recognise() -> None:
    """A corrupt row must raise here so the caller recomputes, rather than
    returning a half-built result."""
    for junk in (b"", b"not zlib", zlib.compress(b"{not json}"), b"\x00\x01\x02"):
        with pytest.raises(Exception):
            decode_result(junk)


def test_the_payload_is_actually_smaller() -> None:
    result = DegreeAuditResult(
        program_name="Computer Science", program_code="198", degree_type="BA",
        catalog_year="2026-2027", status=AuditStatus.INCOMPLETE,
        requirements=[
            RequirementResult(
                requirement_code=f"R{i}", requirement_name="Requirement",
                requirement_type="choose_n", status=RequirementStatus.UNSATISFIED,
                reason="not satisfied", curation_status="curated_from_prose",
            )
            for i in range(40)
        ],
    )
    raw = len(result.model_dump_json().encode())
    compressed = len(encode_result(result))
    assert compressed < raw / 3, f"{compressed} vs {raw}"


def test_the_cache_never_uses_pickle_for_the_payload() -> None:
    import pathlib

    source = (
        pathlib.Path(__file__).resolve().parents[1]
        / "app" / "services" / "audit" / "cache.py"
    ).read_text(encoding="utf-8")
    code = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("#")
    )
    assert "pickle" not in code
    assert "marshal" not in code


# ==========================================================================
# real audits (Part 6)
# ==========================================================================


def _source(session):
    suffix = uuid.uuid4().hex[:8]
    source = DataSource(
        kind="manual_curation", url=f"synthetic://payload/{suffix}",
        content_hash=suffix, retrieved_at=dt.datetime.now(dt.UTC),
    )
    session.add(source)
    session.flush()
    return source


def _course(session, *, credits="4.0"):
    unit, subject, number = "01", uuid.uuid4().hex[:6], uuid.uuid4().hex[:6]
    source = _source(session)
    subject_row = Subject(code=subject, offering_unit_code=unit,
                          description="s", source_id=source.id)
    session.add(subject_row)
    session.flush()
    course = Course(
        offering_unit_code=unit, subject_code=subject, course_number=number,
        supplement_code="", course_string=f"{unit}:{subject}:{number}",
        title="Course", credits=decimal.Decimal(credits),
        subject_id=subject_row.id, source_id=source.id,
    )
    session.add(course)
    session.flush()
    return course


def _build(session, *, shape: str):
    """Representative academic shapes, one per Part 6 bullet."""
    source = _source(session)
    suffix = uuid.uuid4().hex[:8]
    school = School(code=f"S{suffix}", name="S", campus_code="NB",
                    source_id=source.id)
    session.add(school)
    session.flush()
    program = Program(school_id=school.id, code=suffix, name="Prog",
                      degree_type="BA", source_id=source.id)
    session.add(program)
    session.flush()
    version = ProgramVersion(
        program_id=program.id, catalog_year="2026-2027", source_id=source.id,
        sharing_policy=("share_across_systems" if shape == "shared" else "exclusive"),
        total_credits_min=decimal.Decimal("24.0"),
    )
    session.add(version)
    session.flush()

    requirement = Requirement(
        program_version_id=version.id, code=f"REQ_{suffix}", name="Requirement",
        requirement_type="choose_n", min_count=(3 if shape == "partial" else 1),
        sort_order=1, source_id=source.id,
        requirement_system=("core" if shape == "shared" else "major"),
        min_distinct_categories=(2 if shape == "category" else None),
    )
    session.add(requirement)
    session.flush()

    courses = [_course(session) for _ in range(3)]
    for index, course in enumerate(courses):
        session.add(RequirementCourseOption(
            requirement_id=requirement.id, course_id=course.id,
            category=(f"CAT{index}" if shape == "category" else None),
            source_id=source.id,
        ))
    session.flush()

    if shape == "excluded":
        session.add(ProgramRule(
            program_version_id=version.id, code=f"RULE_{suffix}",
            name="Exclusion", rule_type="course_exclusion", is_evaluable=True,
            excluded_course_strings=courses[0].course_string,
            source_id=source.id,
        ))
        session.flush()

    account = UserAccount(identity_provider="oidc",
                          external_subject=f"pay-{uuid.uuid4()}")
    session.add(account)
    session.flush()
    student = Student(external_ref=f"p-{uuid.uuid4()}",
                      catalog_year=version.catalog_year,
                      program_version_id=version.id, user_id=account.id)
    session.add(student)
    session.flush()

    taken = courses if shape != "partial" else courses[:1]
    for index, course in enumerate(taken):
        status = "in_progress" if (shape == "baseline" and index == 0) else "completed"
        session.add(StudentCourse(
            student_id=student.id, course_id=course.id,
            term_code=f"2026{index}", status=status,
            grade=(None if status == "in_progress" else "A"),
            credits_earned=decimal.Decimal("4.0"),
        ))
    session.flush()
    return student


@requires_db
@pytest.mark.parametrize(
    "shape",
    ["ordinary", "category", "shared", "baseline", "partial", "excluded"],
)
def test_a_cache_hit_matches_a_fresh_audit_for_real_shapes(shape) -> None:
    """Part 6. The cache must not subtly change academic output."""
    with _session() as session:
        student = _build(session, shape=shape)
        session.commit()

        fresh = DegreeAuditEngine(session).audit(student)
        first, from_cache = audit_with_cache(session, student)
        assert from_cache is False

        cached, from_cache = audit_with_cache(session, student)
        assert from_cache is True

        assert cached.model_dump_json() == fresh.model_dump_json(), shape
        assert first.model_dump_json() == fresh.model_dump_json(), shape


@requires_db
def test_multiple_allocations_survive_the_round_trip() -> None:
    with _session() as session:
        student = _build(session, shape="ordinary")
        session.commit()
        fresh = DegreeAuditEngine(session).audit(student)
        assert len(fresh.allocation) >= 1

        audit_with_cache(session, student)
        cached, hit = audit_with_cache(session, student)

    assert hit is True
    assert [a.model_dump() for a in cached.allocation] == [
        a.model_dump() for a in fresh.allocation
    ]


@requires_db
def test_the_stored_row_is_compressed_and_small() -> None:
    from app.models import StudentAuditCache

    with _session() as session:
        student = _build(session, shape="ordinary")
        session.commit()
        audit_with_cache(session, student)
        row = session.get(StudentAuditCache, student.id)
        stored = len(row.result_blob)
        raw = len(DegreeAuditEngine(session).audit(student).model_dump_json())

    assert stored < raw
    # It really is zlib, not accidentally stored raw.
    assert zlib.decompress(row.result_blob)
