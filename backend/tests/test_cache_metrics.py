"""Audit cache observability (Phase 5.8, Parts 17-19).

Two things are being tested, and only one of them is arithmetic:

  * **semantics** - exactly one of hit/miss per cache-consulting call, never
    both, so a hit rate means something;
  * **privacy** - there is no way to get a student identifier into a metric,
    because there is no label API at all.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import decimal
import os
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.metrics import (
    AUDIT_CACHE_HITS,
    AUDIT_CACHE_INVALIDATIONS,
    AUDIT_CACHE_MISSES,
    AUDIT_CACHE_READ_FAILURES,
    AUDIT_CACHE_STALE,
    AUDIT_CACHE_STALE_ACADEMIC,
    AUDIT_CACHE_STALE_ENGINE,
    AUDIT_CACHE_STALE_RULES,
    AUDIT_CACHE_STALE_RULES_ONLY,
    AUDIT_CACHE_WRITE_FAILURES,
    AUDIT_DURATION,
    AUDIT_FAILURES,
    ENGINE_DURATION,
    get_metrics,
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
    StudentAuditCache,
    StudentCourse,
    Subject,
    UserAccount,
)
from app.services.audit.cache import invalidate_student_audit
from app.services.audit.cached_audit import audit_with_cache

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


def _source(session):
    suffix = uuid.uuid4().hex[:8]
    source = DataSource(
        kind="manual_curation", url=f"synthetic://metrics/{suffix}",
        content_hash=suffix, retrieved_at=dt.datetime.now(dt.UTC),
    )
    session.add(source)
    session.flush()
    return source


def _course(session):
    unit, subject, number = "01", uuid.uuid4().hex[:6], uuid.uuid4().hex[:6]
    source = _source(session)
    subject_row = Subject(code=subject, offering_unit_code=unit,
                          description="s", source_id=source.id)
    session.add(subject_row)
    session.flush()
    course = Course(
        offering_unit_code=unit, subject_code=subject, course_number=number,
        supplement_code="", course_string=f"{unit}:{subject}:{number}",
        title="C", credits=decimal.Decimal("4.0"),
        subject_id=subject_row.id, source_id=source.id,
    )
    session.add(course)
    session.flush()
    return course


def _scenario(session):
    source = _source(session)
    suffix = uuid.uuid4().hex[:8]
    school = School(code=f"S{suffix}", name="S", campus_code="NB",
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
                          external_subject=f"metrics-{uuid.uuid4()}")
    session.add(account)
    session.flush()
    student = Student(external_ref=f"m-{uuid.uuid4()}",
                      catalog_year=version.catalog_year,
                      program_version_id=version.id, user_id=account.id)
    session.add(student)
    session.flush()
    session.add(StudentCourse(student_id=student.id, course_id=course.id,
                              term_code="20269", status="completed", grade="A",
                              credits_earned=decimal.Decimal("4.0")))
    session.flush()
    return {"student": student, "account": account, "requirement": requirement}


# ==========================================================================
# hit / miss semantics
# ==========================================================================


@requires_db
def test_first_request_is_a_miss_second_is_a_hit() -> None:
    metrics = get_metrics()
    with _session() as session:
        scenario = _scenario(session)
        session.commit()
        student = scenario["student"]

        audit_with_cache(session, student)
        assert metrics.counter(AUDIT_CACHE_MISSES) == 1
        assert metrics.counter(AUDIT_CACHE_HITS) == 0

        audit_with_cache(session, student)
        assert metrics.counter(AUDIT_CACHE_MISSES) == 1
        assert metrics.counter(AUDIT_CACHE_HITS) == 1


@requires_db
def test_one_request_is_never_both_a_hit_and_a_miss() -> None:
    """Otherwise the hit rate is meaningless."""
    metrics = get_metrics()
    with _session() as session:
        scenario = _scenario(session)
        session.commit()
        student = scenario["student"]
        for _ in range(5):
            audit_with_cache(session, student)

    assert (
        metrics.counter(AUDIT_CACHE_HITS) + metrics.counter(AUDIT_CACHE_MISSES)
    ) == 5


@requires_db
def test_a_stale_row_counts_as_one_miss_and_one_stale() -> None:
    """A stale miss and a cold miss mean different things operationally, so
    they are distinguishable - but a stale miss is still ONE miss."""
    metrics = get_metrics()
    with _session() as session:
        scenario = _scenario(session)
        session.commit()
        student = scenario["student"]

        audit_with_cache(session, student)          # cold miss
        audit_with_cache(session, student)          # hit
        assert metrics.counter(AUDIT_CACHE_STALE) == 0

        # Change an academic fact.
        session.add(StudentCourse(
            student_id=student.id, course_id=_course(session).id,
            term_code="20271", status="completed", grade="B",
            credits_earned=decimal.Decimal("3.0"),
        ))
        session.commit()

        audit_with_cache(session, student)          # stale miss
        assert metrics.counter(AUDIT_CACHE_MISSES) == 2
        assert metrics.counter(AUDIT_CACHE_HITS) == 1
        assert metrics.counter(AUDIT_CACHE_STALE) == 1


@requires_db
def test_a_rules_change_produces_a_recomputation() -> None:
    metrics = get_metrics()
    with _session() as session:
        scenario = _scenario(session)
        session.commit()
        student = scenario["student"]

        audit_with_cache(session, student)
        audit_with_cache(session, student)
        assert metrics.counter(AUDIT_CACHE_HITS) == 1

        session.get(Requirement, scenario["requirement"].id).min_count = 9
        session.commit()

        _, from_cache = audit_with_cache(session, student)
        assert from_cache is False
        assert metrics.counter(AUDIT_CACHE_MISSES) == 2


@requires_db
def test_engine_version_change_produces_a_recomputation(monkeypatch) -> None:
    metrics = get_metrics()
    with _session() as session:
        scenario = _scenario(session)
        session.commit()
        student = scenario["student"]

        audit_with_cache(session, student)
        audit_with_cache(session, student)
        assert metrics.counter(AUDIT_CACHE_HITS) == 1

        monkeypatch.setattr(
            "app.services.audit.cache.AUDIT_ENGINE_VERSION", "5.8.0-next"
        )
        audit_with_cache(session, student)
        assert metrics.counter(AUDIT_CACHE_MISSES) == 2


@requires_db
def test_a_bypassed_call_counts_as_neither() -> None:
    """`use_cache=False` never consults the cache, so it is not a miss."""
    metrics = get_metrics()
    with _session() as session:
        scenario = _scenario(session)
        session.commit()
        audit_with_cache(session, scenario["student"], use_cache=False)

    assert metrics.counter(AUDIT_CACHE_HITS) == 0
    assert metrics.counter(AUDIT_CACHE_MISSES) == 0
    # The engine still ran, and is still timed.
    assert metrics.histogram(ENGINE_DURATION)["count"] == 1


@requires_db
def test_hit_rate_is_none_before_any_traffic() -> None:
    """A cache with no traffic has no hit rate. Reporting 0% would read as a
    broken cache."""
    assert get_metrics().snapshot()["cache_hit_rate"] is None

    with _session() as session:
        scenario = _scenario(session)
        session.commit()
        audit_with_cache(session, scenario["student"])
        audit_with_cache(session, scenario["student"])

    snapshot = get_metrics().snapshot()
    assert snapshot["cache_requests_total"] == 2
    assert snapshot["cache_hit_rate"] == 0.5


# ==========================================================================
# failure counters
# ==========================================================================


@requires_db
def test_a_corrupt_payload_counts_as_a_read_failure() -> None:
    metrics = get_metrics()
    with _session() as session:
        scenario = _scenario(session)
        session.commit()
        student = scenario["student"]
        audit_with_cache(session, student)

        row = session.get(StudentAuditCache, student.id)
        row.result_blob = b"not zlib at all"
        session.commit()

        _, from_cache = audit_with_cache(session, student)

    assert from_cache is False
    assert metrics.counter(AUDIT_CACHE_READ_FAILURES) == 1


@requires_db
def test_a_write_failure_is_counted_and_does_not_fail_the_audit() -> None:
    from app.services.audit.cache import AuditCacheKey, write_cached_audit
    from app.services.audit.engine import DegreeAuditEngine

    metrics = get_metrics()
    with _session() as session:
        scenario = _scenario(session)
        session.commit()
        student = scenario["student"]
        key = AuditCacheKey.compute(session, student)
        result = DegreeAuditEngine(session).audit(student)

    class Boom:
        def get(self, *a, **k):
            raise RuntimeError("write path broken")

        def rollback(self):
            pass

    assert write_cached_audit(Boom(), student, key, result) is False
    assert metrics.counter(AUDIT_CACHE_WRITE_FAILURES) == 1


@requires_db
def test_invalidation_is_counted() -> None:
    metrics = get_metrics()
    with _session() as session:
        scenario = _scenario(session)
        session.commit()
        student = scenario["student"]
        audit_with_cache(session, student)
        invalidate_student_audit(session, student.id)
        session.commit()

    assert metrics.counter(AUDIT_CACHE_INVALIDATIONS) == 1


# ==========================================================================
# timings
# ==========================================================================


@requires_db
def test_durations_are_recorded_for_both_paths() -> None:
    metrics = get_metrics()
    with _session() as session:
        scenario = _scenario(session)
        session.commit()
        student = scenario["student"]
        audit_with_cache(session, student)
        audit_with_cache(session, student)

    audit = metrics.histogram(AUDIT_DURATION)
    assert audit["count"] == 2
    assert audit["mean"] > 0
    # The engine ran only on the miss.
    assert metrics.histogram(ENGINE_DURATION)["count"] >= 1


# ==========================================================================
# privacy and cardinality
# ==========================================================================


@requires_db
def test_metrics_contain_no_identifiers() -> None:
    with _session() as session:
        scenario = _scenario(session)
        session.commit()
        student = scenario["student"]
        account = scenario["account"]
        audit_with_cache(session, student)
        audit_with_cache(session, student)
        identifiers = [str(student.id), str(account.id), account.external_subject,
                       student.external_ref]

    blob = str(get_metrics().snapshot())
    for identifier in identifiers:
        assert identifier not in blob


def test_there_is_no_api_for_attaching_a_label() -> None:
    """Stronger than a rule saying not to: there is nowhere to put one.

    An unbounded label set is the standard way metrics become an outage, and
    a label holding a student id would make a counter a disclosure.
    """
    import inspect

    from app.core.metrics import MetricsRegistry

    for name in ("increment", "observe"):
        params = list(inspect.signature(getattr(MetricsRegistry, name)).parameters)
        assert "labels" not in params
        assert "tags" not in params


def test_metric_names_are_a_fixed_declared_set() -> None:
    """Cardinality is bounded because the names are constants in one file.

    Asserts the PROPERTY rather than a count: a hardcoded number breaks
    whenever a metric is added and tests nothing about cardinality. What
    matters is that every name is a module-level constant, that they are
    unique, and that no metric name is assembled at runtime from data - the
    latter being how a student identifier would actually get in.

    Uses the AST rather than line matching, so multi-line calls are read
    correctly instead of being silently skipped or wrongly rejected.
    """
    import ast
    import pathlib as _p

    from app.core import metrics as m

    names = [n for n in m.__all__ if isinstance(getattr(m, n), str)]
    declared = [getattr(m, n) for n in names]

    assert declared, "no metric names declared"
    assert len(set(declared)) == len(declared), "duplicate metric name"
    assert all(n.isupper() for n in names), "metric names must be constants"
    assert all(d.replace("_", "").isalnum() for d in declared)

    root = _p.Path(__file__).resolve().parents[1] / "app"
    checked = 0
    for path in (root / "services" / "audit" / "cache.py",
                 root / "services" / "audit" / "cached_audit.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute):
                continue
            if func.attr not in {"increment", "observe"} or not node.args:
                continue
            first = node.args[0]
            # The name must be a bare module constant. An f-string, a
            # concatenation or a subscript would mean a runtime-built metric
            # name, which is exactly the unbounded-cardinality failure.
            assert isinstance(first, ast.Name), (
                f"{path.name}: metric name is not a constant reference: "
                f"{ast.dump(first)[:80]}"
            )
            assert first.id in names, (
                f"{path.name}: undeclared metric constant {first.id}"
            )
            checked += 1

    assert checked > 0, "found no metric calls to check"


@requires_db
def test_the_snapshot_is_json_safe() -> None:
    """It is served over HTTP, so it must serialize without surprises."""
    import json

    with _session() as session:
        scenario = _scenario(session)
        session.commit()
        audit_with_cache(session, scenario["student"])

    json.dumps(get_metrics().snapshot())


# ==========================================================================
# the endpoint
# ==========================================================================


@pytest.fixture
def app():
    from app.main import create_app

    return create_app()


def _client_as(app, account_id):
    from app.api.security import Principal, get_principal

    app.dependency_overrides[get_principal] = lambda: Principal(
        account_id=account_id, subject="s", issuer=ISSUER, provider="oidc"
    )
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_metrics_requires_authentication(client) -> None:
    assert (await client.get("/api/v1/admin/metrics")).status_code == 401


@requires_db
async def test_ordinary_user_cannot_read_metrics(app) -> None:
    with _session() as session:
        account = UserAccount(identity_provider="oidc",
                              external_subject=f"m-{uuid.uuid4()}")
        session.add(account)
        session.commit()
        account_id = account.id

    async with _client_as(app, account_id) as ac:
        assert (await ac.get("/api/v1/admin/metrics")).status_code == 403


@requires_db
async def test_an_admin_reads_metrics_without_spending_the_link_budget(
    app, settings
) -> None:
    """Part 17 detail: a read-only operational endpoint must not consume the
    10/min budget that bounds how many records a compromised admin
    credential can reassign. Different risks, different budgets."""
    from app.api.security import reset_limiters

    reset_limiters()
    with _session() as session:
        admin = UserAccount(identity_provider="oidc",
                            external_subject=f"adm-{uuid.uuid4()}", is_admin=True)
        session.add(admin)
        session.commit()
        admin_id = admin.id

    async with _client_as(app, admin_id) as ac:
        for _ in range(settings.rate_limit_link_operations + 3):
            response = await ac.get("/api/v1/admin/metrics")
            assert response.status_code == 200

        # The linking budget is untouched, so a real link attempt still runs
        # (404 because the student does not exist, not 429).
        link = await ac.post(
            f"/api/v1/admin/students/{uuid.uuid4()}/unlink", json={}
        )
    assert link.status_code == 404


@requires_db
async def test_the_metrics_response_carries_no_identifiers(app) -> None:
    with _session() as session:
        admin = UserAccount(identity_provider="oidc",
                            external_subject=f"adm-{uuid.uuid4()}", is_admin=True)
        session.add(admin)
        scenario = _scenario(session)
        session.commit()
        admin_id = admin.id
        student_id = str(scenario["student"].id)
        subject = scenario["account"].external_subject
        audit_with_cache(session, scenario["student"])

    async with _client_as(app, admin_id) as ac:
        body = (await ac.get("/api/v1/admin/metrics")).text

    assert student_id not in body
    assert subject not in body
    assert "counters" in body


# ==========================================================================
# invalidation cause attribution (Phase 5.9)
# ==========================================================================


@requires_db
def test_an_academic_change_is_attributed_to_academic() -> None:
    metrics = get_metrics()
    with _session() as session:
        scenario = _scenario(session)
        session.commit()
        student = scenario["student"]
        audit_with_cache(session, student)
        audit_with_cache(session, student)

        session.add(StudentCourse(
            student_id=student.id, course_id=_course(session).id,
            term_code="20271", status="completed", grade="B",
            credits_earned=decimal.Decimal("3.0"),
        ))
        session.commit()
        audit_with_cache(session, student)

    assert metrics.counter(AUDIT_CACHE_STALE_ACADEMIC) == 1
    assert metrics.counter(AUDIT_CACHE_STALE_RULES) == 0
    assert metrics.counter(AUDIT_CACHE_STALE_ENGINE) == 0
    # Not a rules-only miss, so it is not a candidate for per-program saving.
    assert metrics.counter(AUDIT_CACHE_STALE_RULES_ONLY) == 0


@requires_db
def test_a_rule_change_is_attributed_to_rules_only() -> None:
    """The counter the per-program decision rests on."""
    metrics = get_metrics()
    with _session() as session:
        scenario = _scenario(session)
        session.commit()
        student = scenario["student"]
        audit_with_cache(session, student)
        audit_with_cache(session, student)

        session.get(Requirement, scenario["requirement"].id).min_count = 7
        session.commit()
        audit_with_cache(session, student)

    assert metrics.counter(AUDIT_CACHE_STALE_RULES) == 1
    assert metrics.counter(AUDIT_CACHE_STALE_RULES_ONLY) == 1
    assert metrics.counter(AUDIT_CACHE_STALE_ACADEMIC) == 0


@requires_db
def test_an_engine_change_is_attributed_to_engine(monkeypatch) -> None:
    metrics = get_metrics()
    with _session() as session:
        scenario = _scenario(session)
        session.commit()
        student = scenario["student"]
        audit_with_cache(session, student)
        audit_with_cache(session, student)

        monkeypatch.setattr(
            "app.services.audit.cache.AUDIT_ENGINE_VERSION", "5.9.0-next"
        )
        audit_with_cache(session, student)

    assert metrics.counter(AUDIT_CACHE_STALE_ENGINE) == 1
    assert metrics.counter(AUDIT_CACHE_STALE_RULES_ONLY) == 0


@requires_db
def test_simultaneous_changes_are_attributed_to_each_component() -> None:
    """Several components can move together, and rules_only must NOT fire.

    This is the case that would inflate the per-program case if counted
    carelessly: the rules did change, but so did the student's record, so the
    recomputation was required regardless of any versioning scheme.
    """
    metrics = get_metrics()
    with _session() as session:
        scenario = _scenario(session)
        session.commit()
        student = scenario["student"]
        audit_with_cache(session, student)
        audit_with_cache(session, student)

        session.get(Requirement, scenario["requirement"].id).min_count = 5
        session.add(StudentCourse(
            student_id=student.id, course_id=_course(session).id,
            term_code="20272", status="completed", grade="A",
            credits_earned=decimal.Decimal("4.0"),
        ))
        session.commit()
        audit_with_cache(session, student)

    assert metrics.counter(AUDIT_CACHE_STALE) == 1
    assert metrics.counter(AUDIT_CACHE_STALE_ACADEMIC) == 1
    assert metrics.counter(AUDIT_CACHE_STALE_RULES) == 1
    assert metrics.counter(AUDIT_CACHE_STALE_RULES_ONLY) == 0, (
        "a miss that was required anyway must not be counted as avoidable"
    )


@requires_db
def test_a_cold_miss_is_not_counted_as_an_invalidation() -> None:
    """A cold cache is not a stale cache, and no versioning scheme avoids it.

    Conflating the two is exactly the error the Phase 5.9 benchmark made on
    its first run - 71 'avoidable' misses where the real figure was 30.
    """
    metrics = get_metrics()
    with _session() as session:
        scenario = _scenario(session)
        session.commit()
        audit_with_cache(session, scenario["student"])

    assert metrics.counter(AUDIT_CACHE_MISSES) == 1
    assert metrics.counter(AUDIT_CACHE_STALE) == 0
    for name in (AUDIT_CACHE_STALE_ACADEMIC, AUDIT_CACHE_STALE_RULES,
                 AUDIT_CACHE_STALE_ENGINE, AUDIT_CACHE_STALE_RULES_ONLY):
        assert metrics.counter(name) == 0


@requires_db
def test_rules_only_is_a_subset_of_rules() -> None:
    """An invariant the benchmark's arithmetic depends on."""
    metrics = get_metrics()
    with _session() as session:
        scenario = _scenario(session)
        session.commit()
        student = scenario["student"]
        audit_with_cache(session, student)

        for index in range(3):
            session.get(Requirement, scenario["requirement"].id).min_count = 3 + index
            session.commit()
            audit_with_cache(session, student)

        session.add(StudentCourse(
            student_id=student.id, course_id=_course(session).id,
            term_code="20273", status="completed", grade="A",
            credits_earned=decimal.Decimal("4.0"),
        ))
        session.get(Requirement, scenario["requirement"].id).min_count = 9
        session.commit()
        audit_with_cache(session, student)

    assert (
        metrics.counter(AUDIT_CACHE_STALE_RULES_ONLY)
        <= metrics.counter(AUDIT_CACHE_STALE_RULES)
    )
    assert (
        metrics.counter(AUDIT_CACHE_STALE)
        <= metrics.counter(AUDIT_CACHE_MISSES)
    )


# ==========================================================================
# audit failure
# ==========================================================================


@requires_db
def test_an_engine_failure_is_counted_and_re_raised(monkeypatch) -> None:
    """A caching phase must not hide a rising engine failure rate behind a
    healthy hit rate - and must not swallow the exception either."""
    from app.services.audit.engine import DegreeAuditEngine

    metrics = get_metrics()
    with _session() as session:
        scenario = _scenario(session)
        session.commit()

        def boom(self, student, **kwargs):
            raise RuntimeError("engine exploded")

        monkeypatch.setattr(DegreeAuditEngine, "audit", boom)
        with pytest.raises(RuntimeError):
            audit_with_cache(session, scenario["student"])

    assert metrics.counter(AUDIT_FAILURES) == 1


@requires_db
def test_a_successful_audit_counts_no_failure() -> None:
    metrics = get_metrics()
    with _session() as session:
        scenario = _scenario(session)
        session.commit()
        audit_with_cache(session, scenario["student"])
        audit_with_cache(session, scenario["student"])

    assert metrics.counter(AUDIT_FAILURES) == 0
    assert metrics.histogram(ENGINE_DURATION)["count"] >= 1


@requires_db
async def test_the_metrics_endpoint_exposes_the_new_counters(app) -> None:
    with _session() as session:
        admin = UserAccount(identity_provider="oidc",
                            external_subject=f"adm-{uuid.uuid4()}", is_admin=True)
        session.add(admin)
        scenario = _scenario(session)
        session.commit()
        admin_id = admin.id
        audit_with_cache(session, scenario["student"])
        audit_with_cache(session, scenario["student"])

    async with _client_as(app, admin_id) as ac:
        body = (await ac.get("/api/v1/admin/metrics")).json()

    assert "counters" in body
    assert body["cache_hit_rate"] is not None
