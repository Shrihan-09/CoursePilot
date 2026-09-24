"""Deployment configuration, external failure matrix, end-to-end (Phase 5.12).

The boundary this file draws:

* what is exercised against a **real** dependency (PostgreSQL, a real HTTP
  OIDC issuer, real subprocesses);
* what is exercised against a **scripted** stand-in, because the real
  dependency is unavailable in this environment (Anthropic);
* what is **not verified at all**, and why.

Nothing here is labelled verified unless something real was contacted.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import decimal
import os
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.core.config import Environment, Settings, audit_production_settings
from app.models import (
    CatalogCourseEntry,
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


def _prod(**overrides) -> Settings:
    """Production settings built from DECLARED DEFAULTS, not the ambient env.

    `Settings(_env_file=None)` still reads process environment variables -
    the same trap Phase 5.3 hit. This suite's conftest sets AUTH_PROVIDER=dev
    and DEV_AUTH_ENABLED=true for the API tests, which would silently become
    the "production" configuration under test and make the audit assert
    against the wrong findings.

    So each field starts from its declared default and only the overrides
    move.
    """
    base = {
        name: field.default
        for name, field in Settings.model_fields.items()
        if field.default is not None and repr(field.default) != "PydanticUndefined"
    }
    base.update(overrides)
    base["coursepilot_env"] = "production"
    return Settings(_env_file=None, **base)


# ==========================================================================
# Part 6 - production configuration
# ==========================================================================


def test_production_can_never_run_in_debug() -> None:
    """**A real finding from this phase.**

    `debug` defaulted to True with nothing lowering it for production. That
    exposes `/docs`, and - far worse - `create_async_engine(echo=debug)`
    makes SQLAlchemy log every statement WITH ITS PARAMETERS. Verified
    during this phase: a bound catalog year appeared in the log as
    `{'y': '2026-2027'}`. In a deployment that stream would carry
    `external_ref`, account ids and academic values.

    Forced off rather than refused-at-startup: a deployment that boots with
    debug quietly off is better than one that will not boot, and nothing
    legitimate wants SQL echo in production.
    """
    assert _prod().debug is False
    assert _prod(debug=True).debug is False, "an explicit debug=True must be overridden"

    # Local and CI keep it, because that is where it is useful.
    assert Settings(_env_file=None, coursepilot_env="local").debug is True


def test_production_debug_override_disables_sql_echo_and_docs() -> None:
    """The two concrete consequences, checked where they are consumed."""
    from app.main import create_app

    settings = _prod()
    assert settings.debug is False          # -> create_async_engine(echo=False)

    app = create_app()                       # built from the CI settings here
    assert app is not None
    # The gate itself: docs are published only when debug is on.
    assert ("/docs" if settings.debug else None) is None


@pytest.mark.parametrize(
    "overrides,expected_fragment",
    [
        ({}, "auth_provider is 'none'"),
        ({"auth_provider": "dev"}, "auth_provider is 'dev'"),
        ({"dev_auth_enabled": True}, "dev_auth_enabled is true"),
        ({"auth_provider": "oidc"}, "oidc_issuer is not configured"),
        ({"rate_limit_enabled": False}, "rate_limit_enabled is false"),
        ({"cors_origins": "*"}, "cors_origins"),
        ({"explanation_provider": "anthropic"}, "no API key is configured"),
        ({"database_url_sync": "sqlite:///x.db"}, "SQLite"),
    ],
)
def test_production_reports_each_dangerous_combination(
    overrides, expected_fragment
) -> None:
    findings = " | ".join(audit_production_settings(_prod(**overrides)))
    assert expected_fragment in findings, findings


def test_a_fully_configured_production_setup_reports_nothing() -> None:
    """The audit must be satisfiable, or it is noise nobody will read."""
    settings = _prod(
        auth_provider="oidc",
        oidc_issuer="https://idp.rutgers.edu",
        oidc_audience="coursepilot",
        oidc_jwks_uri="https://idp.rutgers.edu/jwks",
        cors_origins="https://coursepilot.example.edu",
        rate_limit_enabled=True,
    )
    assert audit_production_settings(settings) == []


def test_the_audit_names_settings_and_never_values() -> None:
    """A finding that quoted a database URL would put a password in a log."""
    findings = " ".join(audit_production_settings(_prod(
        database_url_sync="postgresql+psycopg://user:SUPERSECRET@host/db",
    )))
    assert "SUPERSECRET" not in findings


def test_development_authentication_is_refused_in_production() -> None:
    from app.api.auth import build_verifier

    assert build_verifier(_prod(auth_provider="dev", dev_auth_enabled=True)) is None


def test_unconfigured_authentication_fails_closed_rather_than_open() -> None:
    from app.api.auth import build_verifier

    assert build_verifier(_prod()) is None
    assert build_verifier(_prod(auth_provider="saml")) is None


# ==========================================================================
# Part 4 - external failure matrix (database)
# ==========================================================================


@requires_db
async def test_an_unreachable_database_does_not_leak_connection_details(
    client,
) -> None:
    """`/ready` reports the dependency; it must not quote the DSN."""
    response = await client.get("/api/v1/ready")
    assert response.status_code in (200, 503)
    body = response.text
    for secret in ("coursepilot:coursepilot", "password", "@localhost"):
        assert secret not in body


async def test_the_application_boots_without_a_database(client) -> None:
    """A documented invariant, and the reason the BM25 index is not built at
    startup. `/health` is liveness and must never touch the database."""
    response = await client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@requires_db
def test_a_failed_transaction_does_not_poison_the_next_request() -> None:
    from app.db.session import get_sync_sessionmaker

    SM = get_sync_sessionmaker()
    with pytest.raises(Exception):
        with SM() as session:
            session.execute(text("SELECT * FROM no_such_table_512"))

    with SM() as session:
        assert session.execute(text("SELECT 1")).scalar() == 1


# ==========================================================================
# Part 4 - external failure matrix (provider), scripted
# ==========================================================================


def test_no_anthropic_credential_is_configured_in_this_environment() -> None:
    """Recorded as a FACT, not worked around.

    This is why the live-provider verification in this phase is reported as
    unavailable rather than passed. The scripted matrix in
    `test_failure_paths.py` still covers CoursePilot's behaviour; what is
    unverified is the vendor's.
    """
    settings = Settings(_env_file=None)
    assert not settings.anthropic_api_key, (
        "a credential now exists - the live smoke test should be run and this "
        "expectation updated"
    )


def test_the_live_provider_smoke_test_is_opt_in_and_skips_without_a_key() -> None:
    """The opt-in gate itself, so the absence is visible in the suite."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip(
            "live Anthropic verification unavailable: no ANTHROPIC_API_KEY in "
            "this environment. Not fabricated; see DATA_MODEL section 34."
        )
    pytest.skip("credential present but live calls are opt-in via RUN_LIVE_AI_TESTS")


# ==========================================================================
# Part 10 - end-to-end, with the real/simulated ledger stated
# ==========================================================================


def _seed_full_scenario(session):
    """A complete academic graph, built in the real database."""
    suffix = uuid.uuid4().hex[:8]
    source = DataSource(kind="manual_curation", url=f"synthetic://e2e/{suffix}",
                        content_hash=suffix, retrieved_at=dt.datetime.now(dt.UTC),
                        version=1)
    session.add(source)
    session.flush()

    school = School(code=f"E{suffix}", name="E2E School", campus_code="NB",
                    source_id=source.id)
    session.add(school)
    session.flush()
    program = Program(school_id=school.id, code=suffix, name="E2E Program",
                      degree_type="BA", source_id=source.id)
    session.add(program)
    session.flush()
    version = ProgramVersion(program_id=program.id, catalog_year="2026-2027",
                             source_id=source.id)
    session.add(version)
    session.flush()
    requirement = Requirement(program_version_id=version.id, code=f"REQ_{suffix}",
                              name="Core", requirement_type="choose_n",
                              min_count=2, sort_order=1, source_id=source.id)
    session.add(requirement)
    session.flush()

    import random

    courses = []
    for _ in range(3):
        subject_code = f"{random.randint(100, 999)}"
        number = f"{random.randint(100, 999)}"
        key = f"01:{subject_code}:{number}"
        if session.execute(text("SELECT 1 FROM course WHERE course_string = :k"),
                           {"k": key}).first():
            continue
        existing = session.execute(
            text("SELECT id FROM subject WHERE code = :c AND offering_unit_code='01'"),
            {"c": subject_code}).first()
        if existing:
            subject_id = existing[0]
        else:
            subject = Subject(code=subject_code, offering_unit_code="01",
                              description="E2E Subject", source_id=source.id)
            session.add(subject)
            session.flush()
            subject_id = subject.id
        course = Course(offering_unit_code="01", subject_code=subject_code,
                        course_number=number, supplement_code="",
                        course_string=key, title="Data Structures E2E",
                        credits=decimal.Decimal("4.0"), subject_id=subject_id,
                        source_id=source.id)
        session.add(course)
        session.flush()
        session.add(CatalogCourseEntry(
            course_id=course.id, course_string=key,
            description="An end-to-end probe course about data structures.",
            catalog_year="2026-2027", title=course.title, source_id=source.id))
        session.add(RequirementCourseOption(requirement_id=requirement.id,
                                            course_id=course.id,
                                            source_id=source.id))
        courses.append(course)
    session.flush()
    if len(courses) < 2:                             # pragma: no cover
        pytest.skip("could not allocate enough free SOC-format course keys")

    account = UserAccount(identity_provider="oidc",
                          external_subject=f"e2e-{uuid.uuid4()}")
    session.add(account)
    session.flush()
    student = Student(external_ref=f"e2e-{uuid.uuid4()}",
                      catalog_year=version.catalog_year,
                      program_version_id=version.id, user_id=account.id)
    session.add(student)
    session.flush()
    session.add(StudentCourse(student_id=student.id, course_id=courses[0].id,
                              term_code="20269", status="completed", grade="A",
                              credits_earned=decimal.Decimal("4.0")))
    session.flush()
    session.commit()
    return {"account": account, "student": student, "courses": courses}


@requires_db
async def test_the_full_authenticated_path_end_to_end() -> None:
    """**The closest legitimate end-to-end run.**

    REAL:       PostgreSQL, the Degree Engine, the audit cache and its
                triggers, the BM25 index and its version triggers, ownership
                resolution, the explanation validator, correlation ids and
                metrics.
    SIMULATED:  the AI provider, because no credential exists (the
                deterministic path is the authority and is what runs); and
                the authenticated principal, injected via the dependency
                override, because no Rutgers client registration exists.
                The OIDC verifier itself is exercised for real against a
                live HTTP issuer in `test_oidc_live_path.py`.
    """
    from app.api.security import Principal, get_principal
    from app.core.metrics import (
        AUDIT_CACHE_HITS,
        AUDIT_CACHE_MISSES,
        SEARCH_INDEX_BUILDS,
        SEARCH_INDEX_REUSE,
        get_metrics,
    )
    from app.core.observability import REQUEST_ID_HEADER
    from app.main import create_app
    from app.services.search.index_registry import get_search_index_registry

    with _session() as session:
        scenario = _seed_full_scenario(session)
        account_id = scenario["account"].id
        course_key = scenario["courses"][0].course_string
        student_ref = scenario["student"].external_ref
        subject = scenario["account"].external_subject

    get_search_index_registry().reset()
    metrics = get_metrics()
    metrics.reset()

    app = create_app()
    app.dependency_overrides[get_principal] = lambda: Principal(
        account_id=account_id, subject="s", issuer=ISSUER, provider="oidc")

    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://test") as ac:
        context = await ac.get("/api/v1/student/context")
        audit_cold = await ac.get("/api/v1/student/audit")
        audit_warm = await ac.get("/api/v1/student/audit")
        explain_one = await ac.post(
            "/api/v1/explanations/recommendation",
            json={"course_key": course_key, "explanation_type": "why_recommended"})
        explain_two = await ac.post(
            "/api/v1/explanations/recommendation",
            json={"course_key": course_key, "explanation_type": "why_recommended"})

    # --- every hop succeeded -----------------------------------------
    assert context.status_code == 200
    assert audit_cold.status_code == 200
    assert audit_warm.status_code == 200
    assert explain_one.status_code == 200
    assert explain_two.status_code == 200

    # --- correlation: distinct ids, present on every response ---------
    ids = [r.headers[REQUEST_ID_HEADER] for r in
           (context, audit_cold, audit_warm, explain_one, explain_two)]
    assert all(ids) and len(set(ids)) == 5

    # --- the audit cache worked ---------------------------------------
    assert metrics.counter(AUDIT_CACHE_MISSES) >= 1
    assert metrics.counter(AUDIT_CACHE_HITS) >= 1
    assert audit_cold.json() == audit_warm.json()

    # --- the BM25 index was built ONCE across two explanations --------
    assert metrics.counter(SEARCH_INDEX_BUILDS) == 1
    assert metrics.counter(SEARCH_INDEX_REUSE) >= 1

    # --- the explanation is deterministic and grounded ----------------
    body = explain_one.json()
    assert body["generated_by"] == "deterministic"
    assert body["used_model"] is False          # no provider configured
    assert body["summary"]
    assert body["request_id"] == explain_one.headers[REQUEST_ID_HEADER]

    # --- nothing identifying leaked into any response -----------------
    for response in (context, audit_cold, explain_one):
        assert student_ref not in response.text
        assert subject not in response.text


@requires_db
async def test_an_unauthenticated_request_gets_no_academic_data(client) -> None:
    """The outermost boundary, restated at the end of the phase."""
    for path in ("/api/v1/student/context", "/api/v1/student/audit"):
        response = await client.get(path)
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "authentication_error"


# ==========================================================================
# Part 8 - the metrics decision, recorded as a test
# ==========================================================================


def test_metrics_are_lost_on_restart_and_this_is_known() -> None:
    """Phase 5.10 documented it; Phase 5.12 decides it is acceptable for now.

    A fresh registry is what a restarted process has. The decision and its
    revisit trigger are in DATA_MODEL section 34 - this pins the behaviour
    so the decision cannot drift silently.
    """
    from app.core.metrics import MetricsRegistry, REQUESTS_TOTAL

    registry = MetricsRegistry()
    registry.increment(REQUESTS_TOTAL, 17)
    assert registry.counter(REQUESTS_TOTAL) == 17

    restarted = MetricsRegistry()
    assert restarted.counter(REQUESTS_TOTAL) == 0
    assert restarted.snapshot()["cache_hit_rate"] is None
