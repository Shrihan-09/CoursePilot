"""Phase 7.0 correctness fixes (real PostgreSQL, SYNTHETIC catalog).

  * graduate courses never enter a QUERY-defined pool - so neither the audit
    nor the planner can count or recommend one (generic: the course's level);
  * every audit of a non-published program version carries
    `program_pending_review`, the student's own program included;
  * every StudentCourse column is either an audit input (fingerprinted) or
    explicitly declared not to be one.
"""

from __future__ import annotations

import decimal

import pytest
from sqlalchemy import select

from app.models import (
    Course,
    CourseOffering,
    DataSource,
    Requirement,
    RequirementCourseOption,
    StudentCourse,
    Subject,
)
from app.services.eligibility import reconcile

from .support import onboarding_world
from .test_multi_program import _client, _session, requires_db


@pytest.fixture
def grad_world():
    """PUB gets REQ_UPPER: choose 2 of query {subject, >= 300}. Candidates:
    301 (U, offered), 302 (U, never offered), 501 (GRADUATE, offered)."""
    with _session() as s:
        world = onboarding_world.build(s)
        a = s.get(Course, world["course_id"]["A"])
        source = s.get(DataSource, a.source_id)
        subject_id = s.scalar(select(Subject.id).where(Subject.offering_unit_code == "98",
                                                       Subject.code == a.subject_code))
        made = {}
        for number, level in (("301", "U"), ("302", "U"), ("501", "G")):
            c = Course(offering_unit_code="98", subject_code=a.subject_code,
                       course_number=number, supplement_code="",
                       course_string=f"98:{a.subject_code}:{number}", title=f"UPPER {number}",
                       credits=decimal.Decimal("3.0"), level=level, subject_id=subject_id,
                       source_id=source.id)
            s.add(c)
            s.flush()
            made[number] = c.course_string
            if number != "302":
                s.add(CourseOffering(course_id=c.id, term_code="20189", campus_code="NB",
                                     source_id=source.id))
        req = Requirement(program_version_id=world["v_pub"], code="REQ_UPPER", name="Upper",
                          requirement_type="choose_n", min_count=2, requirement_system="major",
                          source_prose="prose upper", curation_status="curated_from_prose",
                          source_id=source.id,
                          eligibility_rule={"query": {"subject_code": a.subject_code,
                                                      "min_course_number": 300}})
        s.add(req)
        s.flush()
        reconcile(s, [req.id])
        s.commit()
        world["upper"] = made
        world["req_upper"] = req.id
        world["account"] = onboarding_world.new_account(s)
        yield world


@requires_db
def test_a_graduate_course_never_enters_a_query_pool(grad_world) -> None:
    with _session() as s:
        eligible = set(s.scalars(
            select(Course.course_string)
            .join(RequirementCourseOption, RequirementCourseOption.course_id == Course.id)
            .where(RequirementCourseOption.requirement_id == grad_world["req_upper"])))
    up = grad_world["upper"]
    assert eligible == {up["301"], up["302"]}


@requires_db
async def test_neither_the_audit_nor_the_planner_offers_a_graduate_course(grad_world) -> None:
    from app.main import create_app

    up = grad_world["upper"]
    async with _client(create_app(), grad_world["account"]) as ac:
        r = await ac.post("/api/v1/student/profile",
                          json={"program_key": grad_world["keys"]["pub"],
                                "catalog_year": grad_world["year"]})
        assert r.status_code == 201
        audit = (await ac.get("/api/v1/student/audit")).text
        assert up["501"] not in audit
        plan = await ac.post("/api/v1/student/plans/generate", json={"start_term": "21009"})
        assert plan.status_code == 200, plan.text
        body = plan.json()
        placed = {c["course"] for t in body["terms"] for c in t["courses"]}
        assert up["301"] in placed
        assert up["501"] not in plan.text           # not placed, not even suggested
        # The rule still cannot be met by an unofficial shortcut: 302 is never
        # offered, so the planner reports the gap instead of using 501.
        assert up["302"] not in placed


@requires_db
async def test_every_audit_of_a_pending_program_says_so(grad_world) -> None:
    from app.main import create_app

    async with _client(create_app(), grad_world["account"]) as ac:
        await ac.post("/api/v1/student/profile",
                      json={"program_key": grad_world["keys"]["pend"],
                            "catalog_year": grad_world["year"]})
        own = (await ac.get("/api/v1/student/audit")).json()
        finding = next(f for f in own["findings"] if f["code"] == "program_pending_review")
        assert finding["severity"] == "warning"
        assert own["status"] != "insufficient_data"
        # A published program carries no such finding.
        hypothetical = await ac.post("/api/v1/student/scenarios/audit",
                                     json={"program_key": grad_world["keys"]["pub"]})
        assert hypothetical.status_code == 200, hypothetical.text
        assert "program_pending_review" not in {
            f["code"] for f in hypothetical.json()["audit"]["findings"]}
        plan = (await ac.post("/api/v1/student/plans/generate",
                              json={"start_term": "21009"})).json()
        # The planner states it as its own issue (scenario assumptions are
        # reserved for OTHER programs there).
        assert "PROGRAM_PENDING_REVIEW" in {i["code"] for i in plan["issues"]}


def test_every_student_course_column_is_fingerprinted_or_declared_non_audit() -> None:
    import inspect

    from app.services.audit.cache import (
        STUDENT_COURSE_NON_AUDIT_COLUMNS,
        academic_fingerprint,
    )

    source = inspect.getsource(academic_fingerprint)
    for column in StudentCourse.__table__.columns:
        if column.name in STUDENT_COURSE_NON_AUDIT_COLUMNS:
            continue
        assert f"StudentCourse.{column.name}" in source, (
            f"student_course.{column.name} is neither fingerprinted nor declared non-audit")
