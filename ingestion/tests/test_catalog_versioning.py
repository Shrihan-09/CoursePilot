"""Catalog-year isolation (Phase 3, §15).

## Why this file is SYNTHETIC, and clearly labelled so

We measured the real Rutgers CS major in catalog 2025-2026 and 2026-2027 and
the requirement prose was **byte-identical** - along with all 43 course
descriptions, titles, and credits. So real data currently offers no example of
a requirement change between years.

That is precisely why the risk is dangerous: an implementation that silently
overwrites one year's requirements with another's would pass every test built
from real data today, and corrupt a student's audit the first time Rutgers
changes a rule.

So the fixtures below are **SYNTHETIC** - invented to prove the isolation
property, and named so nobody mistakes them for Rutgers data. No synthetic
requirement is ever loaded into the development database.
"""

from __future__ import annotations

from decimal import Decimal

from app.domain.audit import RequirementStatus
from app.models import Program, ProgramVersion, Requirement, Student
from app.services.audit import DegreeAuditEngine
from coursepilot_ingestion.loaders.requirements import RequirementLoader
from sqlalchemy import func, select

# SYNTHETIC: not a real Rutgers requirement. The 2027-2028 variant below
# deliberately differs from the real 2026-2027 rules so isolation is testable.
SYNTHETIC_NEXT_YEAR = {
    "source": {
        "url": "synthetic://coursepilot/test/catalog-isolation",
        "catalog_year": "2027-2028",
        "retrieved_at": "2026-09-13",
        "kind": "manual_curation",
        "curation_status": "synthetic",
    },
    "school": {"code": "SAS", "name": "School of Arts and Sciences", "campus_code": "NB"},
    "program": {"code": "198", "name": "Computer Science", "degree_type": "BA"},
    "program_version": {
        "catalog_year": "2027-2028",
        "total_credits_min": 60,
        "total_credits_max": 64,
        "source_prose": "SYNTHETIC TEST DATA - not a Rutgers requirement.",
    },
    "requirements": [
        {
            "code": "CS_BA",
            "name": "Computer Science B.A. Major (synthetic 2027-2028)",
            "requirement_type": "all_of",
            "sort_order": 0,
            "parent": None,
            "source_prose": "SYNTHETIC",
        },
        {
            "code": "CS_CORE",
            "name": "Required Courses (synthetic)",
            "requirement_type": "all_of",
            "sort_order": 0,
            "parent": "CS_BA",
            "source_prose": "SYNTHETIC",
        },
        # Differs from the real 2026-2027 tree: 111 is NOT required here, and
        # 314 IS. If the loader leaked across years, one of these would break.
        {
            "code": "CS_112",
            "name": "Data Structures",
            "requirement_type": "course",
            "sort_order": 0,
            "parent": "CS_CORE",
            "courses": ["01:198:112"],
        },
        {
            "code": "CS_314",
            "name": "Principles of Programming Languages (synthetic requirement)",
            "requirement_type": "course",
            "sort_order": 1,
            "parent": "CS_CORE",
            "courses": ["01:198:314"],
        },
    ],
}


def _load_synthetic(session) -> ProgramVersion:
    import json

    raw = json.dumps(SYNTHETIC_NEXT_YEAR).encode()
    RequirementLoader(session).load(SYNTHETIC_NEXT_YEAR, raw)
    session.commit()
    return session.scalar(
        select(ProgramVersion).where(ProgramVersion.catalog_year == "2027-2028")
    )


def test_two_versions_of_one_program_coexist(cs_session) -> None:
    _load_synthetic(cs_session)

    programs = cs_session.scalars(select(Program)).all()
    versions = cs_session.scalars(select(ProgramVersion)).all()

    # ONE program, TWO versions - the program is catalog-year independent.
    assert len(programs) == 1
    assert {v.catalog_year for v in versions} == {"2026-2027", "2027-2028"}


def test_loading_a_new_year_does_not_overwrite_the_old(cs_session) -> None:
    """The core isolation property."""
    before = cs_session.scalar(
        select(func.count())
        .select_from(Requirement)
        .join(ProgramVersion, ProgramVersion.id == Requirement.program_version_id)
        .where(ProgramVersion.catalog_year == "2026-2027")
    )
    assert before == 13

    _load_synthetic(cs_session)

    after = cs_session.scalar(
        select(func.count())
        .select_from(Requirement)
        .join(ProgramVersion, ProgramVersion.id == Requirement.program_version_id)
        .where(ProgramVersion.catalog_year == "2026-2027")
    )
    assert after == before, "loading 2027-2028 changed the 2026-2027 requirements"


def test_requirement_codes_repeat_across_versions_without_colliding(cs_session) -> None:
    """`CS_CORE` exists in both years and must be two distinct rows. The UNIQUE
    constraint is (program_version_id, code), not code alone."""
    _load_synthetic(cs_session)

    rows = cs_session.scalars(select(Requirement).where(Requirement.code == "CS_CORE")).all()
    assert len(rows) == 2
    assert len({r.program_version_id for r in rows}) == 2


def test_each_version_audits_against_its_own_rules(cs_session) -> None:
    """Same student record, two catalog years, different verdicts."""
    from tests.test_degree_audit import _enroll, _find

    synthetic = _load_synthetic(cs_session)
    real = cs_session.scalar(
        select(ProgramVersion).where(ProgramVersion.catalog_year == "2026-2027")
    )

    # A record that satisfies the SYNTHETIC core (112 + 314) but not the real
    # one (which also needs 111, 205, 206, 211, 344).
    student_new = Student(
        external_ref="synthetic-2027",
        catalog_year="2027-2028",
        program_version_id=synthetic.id,
    )
    cs_session.add(student_new)
    cs_session.flush()
    _enroll(cs_session, student_new, "01:198:112")
    _enroll(cs_session, student_new, "01:198:314")
    cs_session.commit()

    result_new = DegreeAuditEngine(cs_session).audit(student_new)
    assert _find(result_new, "CS_CORE").status is RequirementStatus.SATISFIED

    student_old = Student(
        external_ref="real-2026",
        catalog_year="2026-2027",
        program_version_id=real.id,
    )
    cs_session.add(student_old)
    cs_session.flush()
    _enroll(cs_session, student_old, "01:198:112")
    _enroll(cs_session, student_old, "01:198:314")
    cs_session.commit()

    result_old = DegreeAuditEngine(cs_session).audit(student_old)
    assert _find(result_old, "CS_CORE").status is RequirementStatus.PARTIALLY_SATISFIED

    # Same two courses, different rules, different answers - which is the
    # entire point of versioning.
    assert result_new.credits_required_min == Decimal(60)
    assert result_old.credits_required_min == Decimal(51)


def test_synthetic_data_is_marked_as_such(cs_session) -> None:
    """A synthetic requirement must never be mistakable for Rutgers data."""
    version = _load_synthetic(cs_session)
    assert version.curation_status == "synthetic"
    for req in cs_session.scalars(
        select(Requirement).where(Requirement.program_version_id == version.id)
    ).all():
        assert req.curation_status == "synthetic"
