"""Request correlation, the error taxonomy and log privacy (Phase 5.10).

> Observability must not become a second source of sensitive data.

Two things are under test and only one is plumbing. The correlation tests
check that a request can be traced; the privacy tests check that tracing it
did not quietly build a second copy of the academic record in the log
aggregator.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import decimal
import json
import logging
import os
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.metrics import (
    AUTHENTICATION_FAILURES,
    AUTHORIZATION_FAILURES,
    RATE_LIMITED_TOTAL,
    REQUEST_DURATION,
    REQUESTS_2XX,
    REQUESTS_4XX,
    REQUESTS_5XX,
    REQUESTS_TOTAL,
    UNLINKED_ACCOUNT_TOTAL,
    get_metrics,
)
from app.core.observability import (
    REQUEST_ID_HEADER,
    ErrorCode,
    current_request_id,
    new_request_id,
    redact_id,
)
from app.models import (
    Course,
    DataSource,
    Program,
    ProgramVersion,
    Requirement,
    RequirementCourseOption,
    School,
    Student,
    StudentCourse,
    Subject,
    UserAccount,
)

requires_db = pytest.mark.db
ISSUER = "https://idp.example.edu"


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


@pytest.fixture(autouse=True)
def _fresh_metrics():
    get_metrics().reset()
    yield
    get_metrics().reset()


@pytest.fixture
def app():
    from app.main import create_app

    return create_app()


def _client_as(app, account_id):
    from app.api.security import Principal, get_principal

    app.dependency_overrides[get_principal] = lambda: Principal(
        account_id=account_id, subject="subject-a", issuer=ISSUER, provider="oidc"
    )
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


class CapturingHandler(logging.Handler):
    """Captures fully FORMATTED records.

    Formatting matters: a leak can hide in `extra` fields or in lazy `%s`
    arguments that only materialise when the record is rendered. Asserting
    against `record.getMessage()` alone would miss both.
    """

    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []
        # The real handler carries this filter, and it stamps the id at EMIT
        # time - which is the only moment the ContextVar still holds the
        # value, because the middleware resets it when the request ends.
        # Applying it afterwards would read "-" for every record and prove
        # nothing.
        from app.core.observability import RequestIdFilter

        self.addFilter(RequestIdFilter())

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    def request_ids(self) -> list[str]:
        return [getattr(r, "request_id", "-") for r in self.records]

    def rendered(self, only_prefix: str | None = None) -> str:
        parts = []
        for record in self.records:
            if only_prefix and not record.name.startswith(only_prefix):
                continue
            parts.append(record.getMessage())
            for key, value in record.__dict__.items():
                if key not in logging.LogRecord("", 0, "", 0, "", (), None).__dict__:
                    parts.append(f"{key}={value}")
            if record.exc_info:
                import traceback

                parts.append("".join(traceback.format_exception(*record.exc_info)))
        return "\n".join(parts)


@contextlib.contextmanager
def capture_logs(level: int = logging.DEBUG):
    handler = CapturingHandler()
    root = logging.getLogger()
    previous = root.level
    root.addHandler(handler)
    root.setLevel(level)
    try:
        yield handler
    finally:
        root.removeHandler(handler)
        root.setLevel(previous)


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------


def _source(session):
    suffix = uuid.uuid4().hex[:8]
    source = DataSource(
        kind="manual_curation", url=f"synthetic://obs/{suffix}",
        content_hash=suffix, retrieved_at=dt.datetime.now(dt.UTC), version=1,
    )
    session.add(source)
    session.flush()
    return source


def _course(session, *, title="Observability Course"):
    unit, subject, number = "01", uuid.uuid4().hex[:6], uuid.uuid4().hex[:6]
    source = _source(session)
    subject_row = Subject(code=subject, offering_unit_code=unit,
                          description="s", source_id=source.id)
    session.add(subject_row)
    session.flush()
    course = Course(
        offering_unit_code=unit, subject_code=subject, course_number=number,
        supplement_code="", course_string=f"{unit}:{subject}:{number}",
        title=title, credits=decimal.Decimal("4.0"),
        subject_id=subject_row.id, source_id=source.id,
    )
    session.add(course)
    session.flush()
    return course


def _scenario(session):
    source = _source(session)
    suffix = uuid.uuid4().hex[:8]
    school = School(code=f"O{suffix}", name="S", campus_code="NB",
                    source_id=source.id)
    session.add(school)
    session.flush()
    program = Program(school_id=school.id, code=suffix, name="P",
                      degree_type="BA", source_id=source.id)
    session.add(program)
    session.flush()
    version = ProgramVersion(program_id=program.id, catalog_year="2026-2027",
                             source_id=source.id)
    session.add(version)
    session.flush()
    requirement = Requirement(
        program_version_id=version.id, code=f"REQ_{suffix}", name="R",
        requirement_type="choose_n", min_count=1, sort_order=1,
        source_id=source.id,
    )
    session.add(requirement)
    session.flush()
    course = _course(session)
    session.add(RequirementCourseOption(requirement_id=requirement.id,
                                        course_id=course.id, source_id=source.id))
    account = UserAccount(identity_provider="oidc",
                          external_subject=f"obs-{uuid.uuid4()}")
    session.add(account)
    session.flush()
    student = Student(external_ref=f"obs-{uuid.uuid4()}",
                      catalog_year=version.catalog_year,
                      program_version_id=version.id, user_id=account.id)
    session.add(student)
    session.flush()
    session.add(StudentCourse(student_id=student.id, course_id=course.id,
                              term_code="20269", status="completed", grade="A",
                              credits_earned=decimal.Decimal("4.0")))
    session.flush()
    return {"student": student, "account": account, "course": course,
            "requirement": requirement}


# ==========================================================================
# Part 2 - correlation
# ==========================================================================


async def test_two_requests_receive_distinct_ids(client) -> None:
    first = await client.get("/api/v1/health")
    second = await client.get("/api/v1/health")
    assert first.headers[REQUEST_ID_HEADER]
    assert second.headers[REQUEST_ID_HEADER]
    assert first.headers[REQUEST_ID_HEADER] != second.headers[REQUEST_ID_HEADER]


async def test_the_id_is_present_on_successful_responses(client) -> None:
    response = await client.get("/api/v1/health")
    assert response.status_code == 200
    assert len(response.headers[REQUEST_ID_HEADER]) == 16


async def test_the_id_is_present_on_handled_errors(client) -> None:
    """The case that matters most: a failure a user will report."""
    unauthenticated = await client.get("/api/v1/student/audit")
    assert unauthenticated.status_code == 401
    assert unauthenticated.headers[REQUEST_ID_HEADER]

    body = unauthenticated.json()
    assert body["error"]["code"] == ErrorCode.AUTHENTICATION_ERROR.value
    assert body["error"]["request_id"] == unauthenticated.headers[REQUEST_ID_HEADER]


async def test_a_client_supplied_id_is_not_adopted(client) -> None:
    """Honouring it would let a caller forge a shared id across users, or
    write attacker-chosen text into the log stream."""
    forged = "attacker-controlled-id"
    response = await client.get(
        "/api/v1/health", headers={REQUEST_ID_HEADER: forged}
    )
    assert response.headers[REQUEST_ID_HEADER] != forged
    assert forged not in response.text


@pytest.mark.parametrize(
    "header",
    ["X-Request-ID", "X-User-Id", "X-Account-Id", "X-Student-Id",
     "X-Forwarded-User", "X-Authenticated-User"],
)
async def test_identity_like_headers_cannot_override_authentication(
    client, header
) -> None:
    """Rule: authentication is affected by `Authorization` and nothing else."""
    response = await client.get(
        "/api/v1/student/audit", headers={header: str(uuid.uuid4())}
    )
    assert response.status_code == 401


@requires_db
async def test_the_id_survives_the_threadpool_hop(app) -> None:
    """All synchronous audit work runs via `run_in_threadpool`.

    A ContextVar that did not survive that hop would leave every Degree
    Engine and cache log line uncorrelated - which is exactly the code an
    operator needs to trace.
    """
    with _session() as session:
        scenario = _scenario(session)
        session.commit()
        account_id = scenario["account"].id

    with capture_logs(logging.INFO) as logs:
        async with _client_as(app, account_id) as ac:
            response = await ac.get("/api/v1/student/audit")

    assert response.status_code == 200
    request_id = response.headers[REQUEST_ID_HEADER]

    # `audit_cache_miss` is emitted by app.services.audit.cached_audit, which
    # runs inside the worker thread. If the ContextVar did not cross the hop
    # this would be "-" and every Degree Engine and cache line in production
    # would be uncorrelated.
    from_threadpool = [
        r for r in logs.records
        if r.name.startswith("app.services.audit")
    ]
    assert from_threadpool, "expected a log record from inside the threadpool"
    assert all(
        getattr(r, "request_id", "-") == request_id for r in from_threadpool
    ), f"threadpool records not correlated: {[getattr(r, 'request_id', '-') for r in from_threadpool]}"


def test_the_id_is_not_derived_from_identity() -> None:
    """Deriving it from an account would make every log line a disclosure."""
    ids = {new_request_id() for _ in range(200)}
    assert len(ids) == 200
    assert all(len(i) == 16 for i in ids)
    assert current_request_id() == "-"  # no request in flight


def test_redaction_is_stable_and_one_way() -> None:
    value = uuid.uuid4()
    assert redact_id(value) == redact_id(value)
    assert str(value) not in redact_id(value)
    assert redact_id(value) != redact_id(uuid.uuid4())
    assert redact_id(None) == "-"
    assert len(redact_id(value)) == 12


# ==========================================================================
# Part 5 - error taxonomy
# ==========================================================================


async def test_the_detail_contract_is_preserved(client) -> None:
    """Phase 5.4's 409 sentence is a contract. The taxonomy is additive."""
    response = await client.get("/api/v1/student/audit")
    body = response.json()
    assert "detail" in body
    assert body["detail"] == body["error"]["message"]


@requires_db
async def test_each_status_maps_to_a_taxonomy_code(app) -> None:
    with _session() as session:
        unlinked = UserAccount(identity_provider="oidc",
                               external_subject=f"obs-{uuid.uuid4()}")
        session.add(unlinked)
        session.commit()
        unlinked_id = unlinked.id

    async with _client_as(app, unlinked_id) as ac:
        conflict = await ac.get("/api/v1/student/audit")
        forbidden = await ac.get("/api/v1/admin/metrics")

    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == ErrorCode.UNLINKED_ACCOUNT.value
    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == ErrorCode.AUTHORIZATION_ERROR.value


@requires_db
async def test_a_validation_error_does_not_echo_the_input(app) -> None:
    """FastAPI's default 422 body quotes the offending value back.

    A request that put a credential in the wrong field would have it
    reflected, and naming the failing field is also a probing aid.
    """
    with _session() as session:
        scenario = _scenario(session)
        session.commit()
        account_id = scenario["account"].id

    secret = "sk-ant-SECRETVALUE-should-not-be-echoed"
    async with _client_as(app, account_id) as ac:
        response = await ac.post(
            "/api/v1/explanations/recommendation",
            json={"course_key": secret, "explanation_type": "why_recommended"},
        )

    assert response.status_code == 422
    assert secret not in response.text
    assert response.json()["error"]["code"] == ErrorCode.VALIDATION_ERROR.value


async def test_an_unhandled_error_is_correlated_and_opaque(app) -> None:
    """Previously the least diagnosable path: no id, no metric, bare 500."""
    from app.api.v1.routes import health as health_route

    @app.get("/api/v1/_boom")
    async def _boom():
        raise RuntimeError("internal detail that must not escape")

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/api/v1/_boom")

    assert response.status_code == 500
    assert response.headers[REQUEST_ID_HEADER]
    assert "internal detail" not in response.text
    assert response.json()["error"]["code"] == ErrorCode.INTERNAL_ERROR.value
    assert get_metrics().counter(REQUESTS_5XX) == 1


# ==========================================================================
# Part 3 - request metrics
# ==========================================================================


async def test_request_metrics_count_by_status_class(client) -> None:
    await client.get("/api/v1/health")
    await client.get("/api/v1/student/audit")  # 401

    metrics = get_metrics()
    assert metrics.counter(REQUESTS_TOTAL) == 2
    assert metrics.counter(REQUESTS_2XX) == 1
    assert metrics.counter(REQUESTS_4XX) == 1
    assert metrics.counter(AUTHENTICATION_FAILURES) == 1
    assert metrics.histogram(REQUEST_DURATION)["count"] == 2


@requires_db
async def test_unlinked_and_authorization_failures_are_counted(app) -> None:
    with _session() as session:
        unlinked = UserAccount(identity_provider="oidc",
                               external_subject=f"obs-{uuid.uuid4()}")
        session.add(unlinked)
        session.commit()
        unlinked_id = unlinked.id

    async with _client_as(app, unlinked_id) as ac:
        await ac.get("/api/v1/student/audit")
        await ac.get("/api/v1/admin/metrics")

    metrics = get_metrics()
    assert metrics.counter(UNLINKED_ACCOUNT_TOTAL) == 1
    assert metrics.counter(AUTHORIZATION_FAILURES) == 1


async def test_histograms_report_percentiles(client) -> None:
    """Phase 5.9 named the absence of percentiles as a limitation."""
    for _ in range(20):
        await client.get("/api/v1/health")
    snapshot = get_metrics().histogram(REQUEST_DURATION)
    assert snapshot["count"] == 20
    assert snapshot["sample"] == 20
    assert snapshot["p50"] <= snapshot["p95"] <= snapshot["p99"] <= snapshot["max"]


def test_the_reservoir_is_bounded() -> None:
    """An unbounded sample list is a slow memory leak."""
    from app.core.metrics import _RESERVOIR

    metrics = get_metrics()
    for value in range(_RESERVOIR * 3):
        metrics.observe(REQUEST_DURATION, float(value))
    snapshot = metrics.histogram(REQUEST_DURATION)
    assert snapshot["count"] == _RESERVOIR * 3
    assert snapshot["sample"] == _RESERVOIR


# ==========================================================================
# Part 8 - security regression: logs and metrics must not leak
# ==========================================================================


@requires_db
async def test_no_identifier_or_academic_content_reaches_the_logs(app) -> None:
    """Inject real values, then assert none of them appear in any record."""
    with _session() as session:
        scenario = _scenario(session)
        session.commit()
        account = scenario["account"]
        student = scenario["student"]
        course = scenario["course"]
        forbidden = {
            "account_id": str(account.id),
            "student_id": str(student.id),
            "external_ref": student.external_ref,
            "provider_subject": account.external_subject,
            "course_key": course.course_string,
            "course_title": course.title,
        }
        account_id = account.id

    with capture_logs() as logs:
        async with _client_as(app, account_id) as ac:
            assert (await ac.get("/api/v1/student/context")).status_code == 200
            assert (await ac.get("/api/v1/student/audit")).status_code == 200
            await ac.post(
                "/api/v1/explanations/recommendation",
                json={"course_key": course.course_string,
                      "explanation_type": "why_recommended"},
            )

    rendered = logs.rendered()
    for label, value in forbidden.items():
        assert value not in rendered, f"{label} leaked into logs: {value}"


@requires_db
async def test_no_credential_reaches_the_logs(client) -> None:
    """A bearer token in a log is a credential in a log aggregator."""
    token = "eyJhbGciOiJSUzI1NiJ9.SECRET-PAYLOAD-VALUE.SIGNATURE"
    with capture_logs() as logs:
        await client.get(
            "/api/v1/student/audit", headers={"Authorization": f"Bearer {token}"}
        )
        await client.post(
            "/api/v1/explanations/recommendation",
            headers={"Authorization": f"Bearer {token}"},
            json={"course_key": "01:198:112", "explanation_type": "why_recommended"},
        )

    rendered = logs.rendered()
    assert token not in rendered
    assert "SECRET-PAYLOAD-VALUE" not in rendered
    assert "Bearer" not in rendered


@requires_db
async def test_metrics_output_contains_no_identifier_or_academic_content(app) -> None:
    with _session() as session:
        admin = UserAccount(identity_provider="oidc",
                            external_subject=f"adm-{uuid.uuid4()}", is_admin=True)
        session.add(admin)
        scenario = _scenario(session)
        session.commit()
        admin_id = admin.id
        student_account_id = scenario["account"].id
        forbidden = [
            str(scenario["student"].id),
            str(scenario["account"].id),
            scenario["student"].external_ref,
            scenario["account"].external_subject,
            scenario["course"].course_string,
            scenario["course"].title,
        ]

    async with _client_as(app, student_account_id) as ac:
        await ac.get("/api/v1/student/audit")

    async with _client_as(app, admin_id) as ac:
        body = (await ac.get("/api/v1/admin/metrics")).text

    for value in forbidden:
        assert value not in body, f"leaked into metrics: {value}"


@requires_db
async def test_error_responses_contain_no_identifier(app) -> None:
    with _session() as session:
        ordinary = UserAccount(identity_provider="oidc",
                               external_subject=f"obs-{uuid.uuid4()}")
        session.add(ordinary)
        scenario = _scenario(session)
        session.commit()
        ordinary_id = ordinary.id
        forbidden = [str(scenario["student"].id),
                     scenario["student"].external_ref,
                     scenario["account"].external_subject]

    async with _client_as(app, ordinary_id) as ac:
        responses = [
            await ac.get("/api/v1/student/audit"),
            await ac.get("/api/v1/admin/metrics"),
            await ac.post(
                f"/api/v1/admin/students/{scenario['student'].id}/unlink", json={}
            ),
        ]

    for response in responses:
        for value in forbidden:
            assert value not in response.text


def _identifier_leaks_in_logging_calls() -> list[str]:
    """Every `logger.*(...)` call that passes a raw identifier.

    Static, because a log line only leaks on the code path that produces it -
    and a dynamic test that never drives that path proves nothing about it.
    The admin link/unlink lines are exactly such a path: they only run for an
    authorized administrator performing a mutation.

    Two shapes are flagged, and both had to be, because the first version of
    this guard only caught one and a mutation restoring `str(student_id)`
    sailed through it:

        logger.info(..., extra={"x": str(account.id)})     Attribute  .id
        logger.info(..., extra={"x": str(student_id)})     Name       *_id

    A call is exonerated only by `redact_id`, which is one-way.
    """
    import ast
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1] / "app"
    suspicious: list[str] = []

    # The correlation id is EXCLUDED on purpose. It is server-minted,
    # opaque and deliberately unrelated to any identity - putting it in
    # logs is the entire point of Phase 5.10. The guard is about ENTITY
    # identifiers, which are join keys into the academic tables.
    non_identifying = {"request_id", "correlation_id", "trace_id"}

    def names_identifier(node: ast.AST) -> bool:
        if isinstance(node, ast.Attribute) and node.attr in {"id", "external_ref",
                                                             "external_subject"}:
            return True
        if isinstance(node, ast.Name):
            if node.id in non_identifying:
                return False
            if node.id.endswith("_id") or node.id.endswith("_ref"):
                return True
        return False

    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in str(path):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute):
                continue
            if func.attr not in {"info", "warning", "error", "exception", "debug"}:
                continue
            if not (isinstance(func.value, ast.Name) and func.value.id == "logger"):
                continue

            # Collect every expression passed to the logging call, including
            # inside `extra={...}` and format arguments.
            redacted = {
                id(arg)
                for sub in ast.walk(node)
                if isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Name)
                and sub.func.id == "redact_id"
                for arg in ast.walk(sub)
            }
            for sub in ast.walk(node):
                if id(sub) in redacted:
                    continue
                if names_identifier(sub):
                    suspicious.append(
                        f"{path.relative_to(root)}:{node.lineno}"
                    )
                    break
    return suspicious


def test_redaction_is_used_instead_of_raw_ids_in_service_logs() -> None:
    """Static guard: the places Phase 5.10 fixed must not regress."""
    assert _identifier_leaks_in_logging_calls() == [], (
        f"raw identifier in a log call: {_identifier_leaks_in_logging_calls()}"
    )


@requires_db
async def test_the_admin_mutation_path_logs_no_raw_identifier(app) -> None:
    """The dynamic counterpart, driving the code path the static guard covers.

    A static check can be fooled by an indirection; a dynamic one only sees
    the paths it exercises. Both, for the line that records who was granted
    access to whose transcript.
    """
    with _session() as session:
        admin = UserAccount(identity_provider="oidc",
                            external_subject=f"adm-{uuid.uuid4()}", is_admin=True)
        session.add(admin)
        scenario = _scenario(session)
        session.commit()
        admin_id = admin.id
        student_id = str(scenario["student"].id)
        target_subject = scenario["account"].external_subject
        target_account_id = str(scenario["account"].id)

    with capture_logs() as logs:
        async with _client_as(app, admin_id) as ac:
            unlink = await ac.post(
                f"/api/v1/admin/students/{student_id}/unlink",
                json={"reason": "observability test"},
            )
            link = await ac.post(
                f"/api/v1/admin/students/{student_id}/link",
                json={"identity_provider": "oidc",
                      "external_subject": target_subject},
            )

    assert unlink.status_code == 200
    assert link.status_code == 200

    # Only CoursePilot's own loggers. The httpx client in this test logs the
    # request URL, which for an admin route contains the student id by
    # design - see the URL-path limitation recorded in DATA_MODEL. That is a
    # real exposure surface in server access logs, and it is not something
    # this application's logging can fix.
    rendered = logs.rendered(only_prefix="app.")
    for label, value in (("student id", student_id),
                         ("account id", target_account_id),
                         ("provider subject", target_subject)):
        assert value not in rendered, f"{label} leaked into application logs"
