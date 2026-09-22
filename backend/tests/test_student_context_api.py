"""Authenticated student context and audit (Phase 5.6).

The property under test is not "the check works". It is that **there is no
input to check**: both routes are parameterless, so a client has nowhere to
name another student. The isolation tests exist to prove that claim rather
than assume it, including against identifiers that really exist.

`db`-marked because ownership is a foreign key and academic status is a
CHECK constraint - neither is proven by a mock.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import decimal
import os
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.models import (
    Course,
    DataSource,
    Program,
    ProgramVersion,
    School,
    Student,
    StudentCourse,
    Subject,
    UserAccount,
)

requires_db = pytest.mark.db

ISSUER = "https://idp.example.edu"
CONTEXT = "/api/v1/student/context"
AUDIT = "/api/v1/student/audit"


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


# --------------------------------------------------------------------------
# fixtures built by the tests themselves
# --------------------------------------------------------------------------


def _source(session):
    suffix = uuid.uuid4().hex[:8]
    source = DataSource(
        kind="manual_curation",
        url=f"synthetic://coursepilot/test/context-{suffix}",
        content_hash=suffix,
        retrieved_at=dt.datetime.now(dt.UTC),
    )
    session.add(source)
    session.flush()
    return source


def _program_version(session, *, catalog_year="2026-2027"):
    source = _source(session)
    suffix = uuid.uuid4().hex[:8]
    school = School(
        code=f"S{suffix[:4]}", name="Test School", campus_code="NB", source_id=source.id
    )
    session.add(school)
    session.flush()
    program = Program(
        school_id=school.id,
        code=suffix[:4],
        name="Test Program",
        degree_type="BA",
        source_id=source.id,
    )
    session.add(program)
    session.flush()
    version = ProgramVersion(
        program_id=program.id, catalog_year=catalog_year, source_id=source.id
    )
    session.add(version)
    session.flush()
    return version


def _course(session, *, credits="4.0", title="Test Course"):
    """A course with a course_string unique to this call.

    Course identity is (unit, subject, number, supplement) and these rows are
    committed, so reusing a literal like "01:111:101" would collide with the
    previous test run. Tests assert on the returned `course_string`.
    """
    unit = "01"
    subject = uuid.uuid4().hex[:6]
    number = uuid.uuid4().hex[:6]
    course_string = f"{unit}:{subject}:{number}"

    from sqlalchemy import select

    source = _source(session)
    # Subject code is unique, so reuse one when a sibling test already made it.
    subject_row = session.scalar(select(Subject).where(Subject.code == subject))
    if subject_row is None:
        subject_row = Subject(
            code=subject,
            offering_unit_code=unit,
            description=f"Subject {subject}",
            source_id=source.id,
        )
        session.add(subject_row)
        session.flush()
    course = Course(
        offering_unit_code=unit,
        subject_code=subject,
        course_number=number,
        supplement_code="",
        course_string=course_string,
        title=title,
        credits=decimal.Decimal(credits),
        subject_id=subject_row.id,
        source_id=source.id,
    )
    session.add(course)
    session.flush()
    return course


def _account(session, *, admin=False):
    account = UserAccount(
        identity_provider="oidc",
        external_subject=f"sub-{uuid.uuid4()}",
        is_admin=admin,
    )
    session.add(account)
    session.flush()
    return account


def _student(session, version, *, account=None, external_ref=None):
    student = Student(
        external_ref=external_ref or f"ref-{uuid.uuid4()}",
        catalog_year=version.catalog_year,
        program_version_id=version.id,
        user_id=account.id if account else None,
    )
    session.add(student)
    session.flush()
    return student


def _enroll(session, student, course, status, *, term="20269", grade=None, credits="4.0"):
    row = StudentCourse(
        student_id=student.id,
        course_id=course.id,
        term_code=term,
        status=status,
        grade=grade,
        credits_earned=decimal.Decimal(credits) if credits is not None else None,
    )
    session.add(row)
    session.flush()
    return row


def _client_as(app, account_id):
    from app.api.security import Principal, get_principal

    app.dependency_overrides[get_principal] = lambda: Principal(
        account_id=account_id, subject="subject-a", issuer=ISSUER, provider="oidc"
    )
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture
def app():
    """The real app. Only the principal is supplied; ownership is genuine."""
    from app.main import create_app

    return create_app()


@pytest.fixture
def linked_student():
    """One account owning one student with a full three-status record."""
    with _session() as session:
        version = _program_version(session)
        account = _account(session)
        student = _student(session, version, account=account)
        done = _course(session, credits="4.0", title="Completed Course")
        now = _course(session, credits="3.0", title="In Progress Course")
        later = _course(session, credits="4.0", title="Planned Course")
        _enroll(session, student, done, "completed", grade="A", credits="4.0")
        _enroll(session, student, now, "in_progress", credits="3.0")
        _enroll(session, student, later, "planned", credits=None)
        session.commit()
        return {
            "account_id": account.id,
            "student_id": student.id,
            "external_ref": student.external_ref,
            "subject": account.external_subject,
            "completed": done.course_string,
            "in_progress": now.course_string,
            "planned": later.course_string,
        }


# ==========================================================================
# authentication and ownership
# ==========================================================================


async def test_context_requires_authentication(client) -> None:
    """Rule: nothing academic is readable without a verified credential."""
    assert (await client.get(CONTEXT)).status_code == 401
    assert (await client.get(AUDIT)).status_code == 401


async def test_invalid_credential_is_refused(client) -> None:
    for header in ("Bearer nonsense", "Bearer dev:", "Basic abc", "dev:alice"):
        response = await client.get(CONTEXT, headers={"Authorization": header})
        assert response.status_code == 401


@requires_db
async def test_linked_account_receives_its_own_record(app, linked_student) -> None:
    async with _client_as(app, linked_student["account_id"]) as ac:
        response = await ac.get(CONTEXT)

    assert response.status_code == 200
    body = response.json()
    record = body["academic_record"]
    assert [c["course_string"] for c in record["completed"]] == [
        linked_student["completed"]
    ]
    assert [c["course_string"] for c in record["in_progress"]] == [
        linked_student["in_progress"]
    ]
    assert [c["course_string"] for c in record["planned"]] == [
        linked_student["planned"]
    ]
    assert body["program"]["degree_type"] == "BA"


@requires_db
async def test_unlinked_account_gets_409(app) -> None:
    """The established Phase 5.4/5.5 semantics, unchanged."""
    with _session() as session:
        account = _account(session)
        session.commit()
        account_id = account.id

    async with _client_as(app, account_id) as ac:
        context = await ac.get(CONTEXT)
        audit = await ac.get(AUDIT)

    assert context.status_code == audit.status_code == 409
    # No fabricated student, no empty-but-valid record, no list to choose from.
    body = context.json()
    assert "student" not in body
    assert "academic_record" not in body


@requires_db
async def test_account_with_no_local_row_is_indistinguishable_from_unlinked(app) -> None:
    """Authenticated against a verifier, but the account row is gone.

    Reporting this differently would leak that an account once existed.
    """
    async with _client_as(app, uuid.uuid4()) as ac:
        response = await ac.get(CONTEXT)
    assert response.status_code == 409


# ==========================================================================
# cross-user isolation
# ==========================================================================


@pytest.fixture
def two_students():
    with _session() as session:
        version = _program_version(session)
        a_account, b_account = _account(session), _account(session)
        a = _student(session, version, account=a_account, external_ref=f"A-{uuid.uuid4()}")
        b = _student(session, version, account=b_account, external_ref=f"B-{uuid.uuid4()}")
        a_course = _course(session, title="Course A")
        b_course = _course(session, title="Course B")
        _enroll(session, a, a_course, "completed", grade="A")
        _enroll(session, b, b_course, "completed", grade="B")
        session.commit()
        return {
            "a": {
                "account_id": a_account.id,
                "student_id": a.id,
                "external_ref": a.external_ref,
                "subject": a_account.external_subject,
                "course": a_course.course_string,
            },
            "b": {
                "account_id": b_account.id,
                "student_id": b.id,
                "external_ref": b.external_ref,
                "subject": b_account.external_subject,
                "course": b_course.course_string,
            },
        }


@requires_db
async def test_each_user_sees_only_their_own_student(app, two_students) -> None:
    a, b = two_students["a"], two_students["b"]

    async with _client_as(app, a["account_id"]) as ac:
        a_body = (await ac.get(CONTEXT)).json()
    async with _client_as(app, b["account_id"]) as ac:
        b_body = (await ac.get(CONTEXT)).json()

    assert [c["course_string"] for c in a_body["academic_record"]["completed"]] == [
        a["course"]
    ]
    assert [c["course_string"] for c in b_body["academic_record"]["completed"]] == [
        b["course"]
    ]
    assert a["course"] not in str(b_body)
    assert b["course"] not in str(a_body)


@requires_db
async def test_no_identifier_redirects_the_lookup(app, two_students) -> None:
    """Rule 1. Every identifier B actually has, offered to A every way the
    transport allows. None of them may change whose record comes back."""
    a, b = two_students["a"], two_students["b"]

    selectors = {
        "student_id": str(b["student_id"]),
        "student_ref": b["external_ref"],
        "external_ref": b["external_ref"],
        "user_id": str(b["account_id"]),
        "account_id": str(b["account_id"]),
        "external_subject": b["subject"],
        "subject": b["subject"],
        "provider": "oidc",
        "id": str(b["student_id"]),
    }

    async with _client_as(app, a["account_id"]) as ac:
        for name, value in selectors.items():
            response = await ac.get(CONTEXT, params={name: value})
            assert response.status_code == 200, name
            body = response.json()
            assert [
                c["course_string"] for c in body["academic_record"]["completed"]
            ] == [a["course"]], f"{name} redirected the lookup"
            assert b["course"] not in str(body), name
            assert b["external_ref"] not in str(body), name

        # All of them at once, in case one is only ignored when alone.
        response = await ac.get(CONTEXT, params=selectors)
        assert response.status_code == 200
        assert b["course"] not in str(response.json())


@requires_db
async def test_fabricated_identifiers_change_nothing(app, two_students) -> None:
    """Non-existent ids must behave exactly like real ones: ignored."""
    a = two_students["a"]
    async with _client_as(app, a["account_id"]) as ac:
        for value in (str(uuid.uuid4()), "smoke-1", "../../etc/passwd", "1 OR 1=1", ""):
            response = await ac.get(CONTEXT, params={"student_id": value})
            assert response.status_code == 200
            assert [
                c["course_string"]
                for c in response.json()["academic_record"]["completed"]
            ] == [a["course"]]


@requires_db
async def test_a_body_on_a_get_cannot_select_a_student(app, two_students) -> None:
    """There is no request model, so there is nothing to bind a body to."""
    a, b = two_students["a"], two_students["b"]
    async with _client_as(app, a["account_id"]) as ac:
        response = await ac.request(
            "GET", CONTEXT, json={"student_id": str(b["student_id"])}
        )
    assert response.status_code == 200
    assert b["course"] not in str(response.json())


@requires_db
async def test_no_path_parameterised_student_route_exists(app) -> None:
    """Rule: the account is the selector, so no route may take a student."""
    paths = app.openapi()["paths"]
    student_self_service = [
        p for p in paths if p.startswith("/api/v1/student")
    ]
    assert student_self_service == ["/api/v1/student/audit", "/api/v1/student/context"] or sorted(
        student_self_service
    ) == ["/api/v1/student/audit", "/api/v1/student/context"]
    assert not any("{" in p for p in student_self_service)
    # The only parameterised student path in the API is the ADMIN linking one,
    # which Phase 5.5 gates on is_admin.
    assert all(
        p.startswith("/api/v1/admin/") for p in paths if "{student_id}" in p
    )


@requires_db
async def test_context_does_not_reveal_whether_another_student_exists(
    app, two_students
) -> None:
    """Real and fabricated ids produce byte-identical responses."""
    a, b = two_students["a"], two_students["b"]
    async with _client_as(app, a["account_id"]) as ac:
        real = await ac.get(CONTEXT, params={"student_id": str(b["student_id"])})
        fake = await ac.get(CONTEXT, params={"student_id": str(uuid.uuid4())})
    assert real.status_code == fake.status_code == 200
    assert real.json() == fake.json()


# ==========================================================================
# privacy
# ==========================================================================


@requires_db
async def test_response_carries_no_identity_or_security_metadata(
    app, linked_student
) -> None:
    async with _client_as(app, linked_student["account_id"]) as ac:
        response = await ac.get(CONTEXT)
    raw = response.text
    body = response.json()

    for leaked in (
        str(linked_student["account_id"]),
        str(linked_student["student_id"]),
        linked_student["external_ref"],
        linked_student["subject"],
    ):
        assert leaked not in raw

    assert set(body) == {"program", "academic_record"}
    for forbidden in (
        "user_id",
        "external_ref",
        "is_admin",
        "identity_provider",
        "external_subject",
        "student_link_event",
        "disabled_at",
        "issuer",
        "claims",
    ):
        assert forbidden not in raw


@requires_db
async def test_course_fields_are_a_deliberate_contract(app, linked_student) -> None:
    """Not ORM serialization - a column added later must not auto-publish."""
    async with _client_as(app, linked_student["account_id"]) as ac:
        body = (await ac.get(CONTEXT)).json()

    for course in body["academic_record"]["completed"]:
        assert set(course) == {
            "course_string",
            "supplement_code",
            "title",
            "term_code",
            "grade",
            "credits_earned",
            "catalog_credits",
            "source_kind",
        }


@requires_db
async def test_context_exposes_no_credit_totals(app, linked_student) -> None:
    """A total that looks like 'credits toward the degree' but is not would
    be worse than no total. Totals belong to the audit."""
    async with _client_as(app, linked_student["account_id"]) as ac:
        body = (await ac.get(CONTEXT)).json()

    # Checked on KEYS, not substrings: "in_progress" is a legitimate bucket
    # name and "curation_status" a legitimate provenance field.
    keys = set()

    def walk(node):
        if isinstance(node, dict):
            keys.update(node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(body)
    for invented in (
        "credits_total",
        "total_credits_earned",
        "credits_completed",
        "credits_remaining",
        "credits_applicable_to_degree",
        "progress_percent",
        "gpa",
    ):
        assert invented not in keys
    # The only credit fields are per-course and clearly scoped.
    assert {k for k in keys if "credit" in k} == {
        "credits_earned",
        "catalog_credits",
        "total_credits_min",
        "total_credits_max",
    }


# ==========================================================================
# academic semantics
# ==========================================================================


@requires_db
async def test_the_three_statuses_stay_three_things(app, linked_student) -> None:
    """Phase 4.5 baseline semantics: planned coursework is not countable and
    in-progress is not completed."""
    async with _client_as(app, linked_student["account_id"]) as ac:
        record = (await ac.get(CONTEXT)).json()["academic_record"]

    completed = {c["course_string"] for c in record["completed"]}
    in_progress = {c["course_string"] for c in record["in_progress"]}
    planned = {c["course_string"] for c in record["planned"]}

    assert completed & in_progress == set()
    assert completed & planned == set()
    assert in_progress & planned == set()
    # The distinction is visible in the data, not only in the key name.
    assert record["completed"][0]["grade"] == "A"
    assert record["in_progress"][0]["grade"] is None
    assert record["planned"][0]["grade"] is None


@requires_db
async def test_credits_match_the_authoritative_record(app, linked_student) -> None:
    from sqlalchemy import select

    async with _client_as(app, linked_student["account_id"]) as ac:
        record = (await ac.get(CONTEXT)).json()["academic_record"]

    served = {
        c["course_string"]: c["credits_earned"]
        for bucket in record.values()
        for c in bucket
    }
    with _session() as session:
        rows = session.execute(
            select(Course.course_string, StudentCourse.credits_earned)
            .join(StudentCourse, StudentCourse.course_id == Course.id)
            .where(StudentCourse.student_id == linked_student["student_id"])
        ).all()
    stored = {
        cs: (None if credits is None else str(credits)) for cs, credits in rows
    }
    assert {k: (None if v is None else str(decimal.Decimal(v))) for k, v in served.items()} == stored


@requires_db
async def test_catalog_credits_and_earned_credits_are_both_reported(app) -> None:
    """They legitimately differ (variable credit, transfer credit), and
    silently preferring one would hide a discrepancy."""
    with _session() as session:
        version = _program_version(session)
        account = _account(session)
        student = _student(session, version, account=account)
        course = _course(session, credits="4.0")
        _enroll(session, student, course, "completed", grade="B", credits="3.0")
        session.commit()
        account_id = account.id

    async with _client_as(app, account_id) as ac:
        completed = (await ac.get(CONTEXT)).json()["academic_record"]["completed"]

    assert completed[0]["credits_earned"] == "3.0"
    assert completed[0]["catalog_credits"] == "4.0"


@requires_db
async def test_a_retake_is_two_terms_not_a_duplicate(app) -> None:
    """The same course in two terms is two real rows, ordered deterministically."""
    with _session() as session:
        version = _program_version(session)
        account = _account(session)
        student = _student(session, version, account=account)
        course = _course(session)
        _enroll(session, student, course, "completed", term="20239", grade="D")
        _enroll(session, student, course, "completed", term="20249", grade="A")
        session.commit()
        account_id = account.id

    async with _client_as(app, account_id) as ac:
        completed = (await ac.get(CONTEXT)).json()["academic_record"]["completed"]

    assert [c["term_code"] for c in completed] == ["20239", "20249"]
    assert [c["grade"] for c in completed] == ["D", "A"]


@requires_db
async def test_multiple_offerings_do_not_multiply_a_course(app) -> None:
    """A join through CourseOffering would fan one record row into several.
    A duplicated course is a fabricated course."""
    from app.models import CourseOffering

    with _session() as session:
        version = _program_version(session)
        account = _account(session)
        student = _student(session, version, account=account)
        course = _course(session)
        for term in ("20239", "20249", "20269"):
            session.add(
                CourseOffering(
                    course_id=course.id,
                    term_code=term,
                    campus_code="NB",
                    source_id=_source(session).id,
                )
            )
        _enroll(session, student, course, "completed", term="20269", grade="A")
        session.commit()
        account_id = account.id

    async with _client_as(app, account_id) as ac:
        completed = (await ac.get(CONTEXT)).json()["academic_record"]["completed"]

    assert len(completed) == 1


@requires_db
async def test_ordering_is_deterministic(app, linked_student) -> None:
    async with _client_as(app, linked_student["account_id"]) as ac:
        first = (await ac.get(CONTEXT)).json()
        second = (await ac.get(CONTEXT)).json()
    assert first == second


@requires_db
async def test_catalog_year_mismatch_is_reported_not_patched(app) -> None:
    """The student's binding and the version's year are separate facts."""
    with _session() as session:
        version = _program_version(session, catalog_year="2026-2027")
        account = _account(session)
        student = _student(session, version, account=account)
        student.catalog_year = "2024-2025"
        session.commit()
        account_id = account.id

    async with _client_as(app, account_id) as ac:
        program = (await ac.get(CONTEXT)).json()["program"]

    assert program["catalog_year"] == "2024-2025"
    assert program["program_version_catalog_year"] == "2026-2027"


# ==========================================================================
# read-only
# ==========================================================================


@requires_db
async def test_the_context_routes_are_read_only(app, linked_student) -> None:
    """No mutation verb is exposed, so the frontend cannot fabricate a
    completed course, a grade or a credit."""
    async with _client_as(app, linked_student["account_id"]) as ac:
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            for path in (CONTEXT, AUDIT):
                response = await ac.request(
                    method, path, json={"course_string": "01:198:111"}
                )
                assert response.status_code == 405, f"{method} {path}"


@requires_db
async def test_a_get_does_not_change_the_record(app, linked_student) -> None:
    from sqlalchemy import func, select

    def counts():
        with _session() as session:
            return session.execute(
                select(StudentCourse.status, func.count())
                .where(StudentCourse.student_id == linked_student["student_id"])
                .group_by(StudentCourse.status)
            ).all()

    before = counts()
    async with _client_as(app, linked_student["account_id"]) as ac:
        await ac.get(CONTEXT)
        await ac.get(AUDIT)
    assert counts() == before


# ==========================================================================
# Degree Engine integration
# ==========================================================================


@requires_db
async def test_audit_matches_direct_engine_invocation(app, linked_student) -> None:
    """The route must return the engine's result, not its own opinion."""
    from app.services.audit.engine import DegreeAuditEngine

    async with _client_as(app, linked_student["account_id"]) as ac:
        served = (await ac.get(AUDIT)).json()

    with _session() as session:
        student = session.get(Student, linked_student["student_id"])
        direct = DegreeAuditEngine(session).audit(student)

    assert served == direct.model_dump(mode="json")


@requires_db
async def test_audit_is_deterministic_across_requests(app, linked_student) -> None:
    async with _client_as(app, linked_student["account_id"]) as ac:
        first = (await ac.get(AUDIT)).json()
        second = (await ac.get(AUDIT)).json()
    assert first == second


@requires_db
async def test_audit_carries_the_advisory_disclaimer(app, linked_student) -> None:
    """Rutgers states only an advisor can certify degree requirements."""
    async with _client_as(app, linked_student["account_id"]) as ac:
        body = (await ac.get(AUDIT)).json()
    assert any("not an official degree audit" in d for d in body["disclaimers"])


@requires_db
async def test_audit_cannot_be_redirected_either(app, two_students) -> None:
    a, b = two_students["a"], two_students["b"]
    async with _client_as(app, a["account_id"]) as ac:
        response = await ac.get(
            AUDIT,
            params={"student_id": str(b["student_id"]), "student_ref": b["external_ref"]},
        )
    assert response.status_code == 200
    assert b["course"] not in str(response.json())


@requires_db
async def test_context_does_not_duplicate_engine_judgement(app, linked_student) -> None:
    """The API is orchestration. Satisfaction words belong to the audit.

    Checked on keys: `curation_status` is provenance about the CATALOG, not a
    judgement about the student, and `in_progress` is a recorded status
    rather than an interpretation of one.
    """
    async with _client_as(app, linked_student["account_id"]) as ac:
        body = (await ac.get(CONTEXT)).json()

    keys = set()

    def walk(node):
        if isinstance(node, dict):
            keys.update(node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(body)
    for judgement in (
        "satisfied",
        "satisfied_credits",
        "requirements",
        "requirement_code",
        "remaining",
        "eligible",
        "eligible_not_allocated",
        "allocation",
        "findings",
        "audit_status",
    ):
        assert judgement not in keys


def test_the_api_route_contains_no_requirement_logic() -> None:
    """Rule: never duplicate Degree Engine logic in the API."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1] / "app"
    for path in (
        root / "api" / "v1" / "routes" / "student.py",
        root / "services" / "student_context.py",
    ):
        source = path.read_text(encoding="utf-8")
        # Strip docstrings and comments - they discuss the boundary, which is
        # the point; it is executable logic that must be absent.
        code = "\n".join(
            line
            for line in source.splitlines()
            if not line.lstrip().startswith("#")
        )
        for forbidden in (
            "RequirementCourseOption",
            "allocate(",
            "evaluate_rule",
            "RequirementStatus",
            "AllocationPlan",
            "build_bm25",
            "build_course_documents",
            "ExplanationModel",
        ):
            assert forbidden not in code, f"{path.name} contains {forbidden}"


def test_the_context_endpoint_does_not_rebuild_the_search_index() -> None:
    """The explanation endpoint legitimately pays a ~230 ms BM25 rebuild.
    Returning a student's own record must not."""
    import pathlib

    source = (
        pathlib.Path(__file__).resolve().parents[1]
        / "app"
        / "api"
        / "v1"
        / "routes"
        / "student.py"
    ).read_text(encoding="utf-8")
    assert "build_bm25" not in source
    assert "build_course_documents" not in source


# ==========================================================================
# Phase 5.3-5.5 security preservation
# ==========================================================================


def test_jwt_validation_is_unchanged() -> None:
    """Rule 10 from Phase 5.5, restated: Phase 5.6 weakens nothing."""
    from app.api.auth import ALLOWED_ALGORITHMS, CLOCK_SKEW_SECONDS

    assert "none" not in ALLOWED_ALGORITHMS
    assert not any(a.startswith("HS") for a in ALLOWED_ALGORITHMS)
    assert CLOCK_SKEW_SECONDS == 60


def test_expired_and_wrong_audience_tokens_are_still_rejected() -> None:
    import datetime as _dt

    import jwt
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    from app.api.auth import AuthenticationError, StaticKeyVerifier

    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    verifier = StaticKeyVerifier(
        public_key=private.public_key(), issuer=ISSUER, audience="coursepilot"
    )
    now = _dt.datetime.now(_dt.UTC)

    def token(**over):
        payload = {
            "iss": ISSUER,
            "aud": "coursepilot",
            "sub": "s",
            "exp": now + _dt.timedelta(seconds=300),
            **over,
        }
        return jwt.encode(payload, pem, algorithm="RS256")

    assert verifier.verify(token()).subject == "s"
    for bad in (
        {"exp": now - _dt.timedelta(hours=1)},
        {"aud": "other-app"},
        {"iss": "https://evil.example.com"},
    ):
        with pytest.raises(AuthenticationError):
            verifier.verify(token(**bad))


@requires_db
async def test_ordinary_user_still_cannot_reach_admin_linking(app, linked_student) -> None:
    """Phase 5.5 authorization is untouched by the new read surface."""
    async with _client_as(app, linked_student["account_id"]) as ac:
        response = await ac.post(
            f"/api/v1/admin/students/{linked_student['student_id']}/link",
            json={"identity_provider": "oidc", "external_subject": "whoever"},
        )
    assert response.status_code == 403


@requires_db
async def test_external_ref_is_still_not_an_identity(app, two_students) -> None:
    """Rule: never treat external_ref as identity proof, and never publish it."""
    a, b = two_students["a"], two_students["b"]
    async with _client_as(app, a["account_id"]) as ac:
        body = (await ac.get(CONTEXT)).json()
        redirected = await ac.get(CONTEXT, params={"external_ref": b["external_ref"]})

    assert a["external_ref"] not in str(body)
    assert b["external_ref"] not in str(redirected.json())


@requires_db
async def test_context_uses_the_existing_authenticated_budget(app, settings, linked_student) -> None:
    """Part 13: no new limiter. The shared request budget already keys on the
    stable account id."""
    from app.api.security import reset_limiters

    reset_limiters()
    limit = settings.rate_limit_requests
    async with _client_as(app, linked_student["account_id"]) as ac:
        statuses = [(await ac.get(CONTEXT)).status_code for _ in range(limit + 1)]
    assert statuses[:limit] == [200] * limit
    assert statuses[limit] == 429


@requires_db
async def test_one_users_reads_do_not_spend_anothers_budget(app, two_students) -> None:
    from app.api.security import reset_limiters

    reset_limiters()
    a, b = two_students["a"], two_students["b"]
    async with _client_as(app, a["account_id"]) as ac:
        for _ in range(30):
            await ac.get(CONTEXT)
    async with _client_as(app, b["account_id"]) as ac:
        assert (await ac.get(CONTEXT)).status_code == 200
