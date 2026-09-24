"""Cache and provider failure matrices (Phase 5.10, Parts 6-7).

Two invariants, restated because everything here exists to defend them:

> A cache failure can never change the correctness of the Degree Engine
> result.

> A Degree Engine failure must never become a confident AI explanation, and
> a model failure must never fabricate an academic result.

Where practical the dependency is broken **for real** - the table is renamed,
the payload is corrupted in the database - rather than mocked to return
`None`. Phase 5.7 found a defect precisely because a real failure left the
PostgreSQL transaction aborted, which a mock would never have reproduced.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import decimal
import os
import uuid
import zlib

import pytest
from sqlalchemy import text

from app.core.metrics import (
    AUDIT_CACHE_READ_FAILURES,
    AUDIT_CACHE_WRITE_FAILURES,
    EXPLANATION_DETERMINISTIC,
    EXPLANATION_MODEL_ATTEMPTED,
    EXPLANATION_MODEL_FAILED,
    EXPLANATION_MODEL_REJECTED,
    EXPLANATION_MODEL_SUCCEEDED,
    EXPLANATION_PROVIDER_UNAVAILABLE,
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


@pytest.fixture(autouse=True)
def _fresh_metrics():
    get_metrics().reset()
    yield
    get_metrics().reset()


def _source(session):
    suffix = uuid.uuid4().hex[:8]
    source = DataSource(kind="manual_curation", url=f"synthetic://fail/{suffix}",
                        content_hash=suffix, retrieved_at=dt.datetime.now(dt.UTC),
                        version=1)
    session.add(source)
    session.flush()
    return source


def _course(session):
    unit, subject, number = "01", uuid.uuid4().hex[:6], uuid.uuid4().hex[:6]
    source = _source(session)
    subject_row = Subject(code=subject, offering_unit_code=unit, description="s",
                          source_id=source.id)
    session.add(subject_row)
    session.flush()
    course = Course(offering_unit_code=unit, subject_code=subject,
                    course_number=number, supplement_code="",
                    course_string=f"{unit}:{subject}:{number}", title="C",
                    credits=decimal.Decimal("4.0"), subject_id=subject_row.id,
                    source_id=source.id)
    session.add(course)
    session.flush()
    return course


def _scenario(session):
    source = _source(session)
    suffix = uuid.uuid4().hex[:8]
    school = School(code=f"F{suffix}", name="S", campus_code="NB", source_id=source.id)
    session.add(school)
    session.flush()
    program = Program(school_id=school.id, code=suffix, name="P", degree_type="BA",
                      source_id=source.id)
    session.add(program)
    session.flush()
    version = ProgramVersion(program_id=program.id, catalog_year="2026-2027",
                             source_id=source.id)
    session.add(version)
    session.flush()
    requirement = Requirement(program_version_id=version.id, code=f"REQ_{suffix}",
                              name="R", requirement_type="choose_n", min_count=1,
                              sort_order=1, source_id=source.id)
    session.add(requirement)
    session.flush()
    course = _course(session)
    session.add(RequirementCourseOption(requirement_id=requirement.id,
                                        course_id=course.id, source_id=source.id))
    account = UserAccount(identity_provider="oidc",
                          external_subject=f"fail-{uuid.uuid4()}")
    session.add(account)
    session.flush()
    student = Student(external_ref=f"f-{uuid.uuid4()}",
                      catalog_year=version.catalog_year,
                      program_version_id=version.id, user_id=account.id)
    session.add(student)
    session.flush()
    session.add(StudentCourse(student_id=student.id, course_id=course.id,
                              term_code="20269", status="completed", grade="A",
                              credits_earned=decimal.Decimal("4.0")))
    session.flush()
    return {"student": student, "account": account, "course": course}


# ==========================================================================
# Part 6 - the cache failure matrix
# ==========================================================================


@requires_db
def test_1_cache_read_failure_recomputes() -> None:
    """Real failure: the table is renamed out from under a live request."""
    with _session() as session:
        scenario = _scenario(session)
        session.commit()
        expected = DegreeAuditEngine(session).audit(scenario["student"]).model_dump_json()
        student_id = scenario["student"].id

    with _session() as admin:
        admin.execute(text("ALTER TABLE student_audit_cache RENAME TO sac_fail"))
        admin.commit()
    try:
        with _session() as session:
            student = session.get(Student, student_id)
            result, from_cache = audit_with_cache(session, student)
            assert from_cache is False
            assert result.model_dump_json() == expected
    finally:
        with _session() as admin:
            admin.execute(text("ALTER TABLE sac_fail RENAME TO student_audit_cache"))
            admin.commit()

    assert get_metrics().counter(AUDIT_CACHE_READ_FAILURES) >= 1


@requires_db
def test_2_cache_write_failure_still_returns_a_correct_audit() -> None:
    from app.services.audit.cache import AuditCacheKey, write_cached_audit

    with _session() as session:
        scenario = _scenario(session)
        session.commit()
        student = scenario["student"]
        key = AuditCacheKey.compute(session, student)
        result = DegreeAuditEngine(session).audit(student)

    class Broken:
        def get(self, *a, **k):
            raise RuntimeError("write path broken")

        def rollback(self):
            pass

    assert write_cached_audit(Broken(), student, key, result) is False
    assert get_metrics().counter(AUDIT_CACHE_WRITE_FAILURES) == 1


@requires_db
@pytest.mark.parametrize(
    "payload,label",
    [
        (b"not zlib at all", "3 corrupted payload"),
        (b"\\x78\\x9c\\x00\\x00", "4 decompression failure"),
        (zlib.compress(b'{"not": "an audit"'), "5 malformed cached JSON"),
        (b"", "6 empty payload"),
    ],
    ids=["corrupt", "bad-zlib", "malformed-json", "empty"],
)
def test_3_to_6_a_bad_payload_recomputes_rather_than_returning_garbage(
    payload, label
) -> None:
    """Each returns 200 with a FRESH audit. Never a partial result."""
    with _session() as session:
        scenario = _scenario(session)
        session.commit()
        student = scenario["student"]
        fresh = DegreeAuditEngine(session).audit(student).model_dump_json()

        audit_with_cache(session, student)
        row = session.get(StudentAuditCache, student.id)
        row.result_blob = payload
        session.commit()

        result, from_cache = audit_with_cache(session, student)
        assert from_cache is False, label
        assert result.model_dump_json() == fresh, label

    assert get_metrics().counter(AUDIT_CACHE_READ_FAILURES) >= 1


@requires_db
def test_7_an_already_failed_transaction_is_cleared_before_the_engine_runs() -> None:
    """The Phase 5.7 defect, re-pinned.

    PostgreSQL aborts the whole transaction on a failed statement. A cache
    layer that catches its own exception but leaves the transaction aborted
    hands the Degree Engine a session where every query fails - turning
    "the cache is broken" into "the audit is broken".
    """
    with _session() as session:
        scenario = _scenario(session)
        session.commit()
        student_id = scenario["student"].id
        expected = DegreeAuditEngine(session).audit(scenario["student"]).model_dump_json()

    with _session() as session:
        student = session.get(Student, student_id)
        # Poison the transaction before the audit runs.
        with pytest.raises(Exception):
            session.execute(text("SELECT * FROM table_that_does_not_exist_510"))

        from app.services.audit.cache import _safe_rollback

        _safe_rollback(session)
        result, _ = audit_with_cache(session, student)
        assert result.model_dump_json() == expected


@requires_db
def test_the_whole_cache_can_be_deleted_without_changing_any_answer() -> None:
    """Derived state: losing all of it costs latency and nothing else."""
    with _session() as session:
        scenario = _scenario(session)
        session.commit()
        student = scenario["student"]
        before, _ = audit_with_cache(session, student)
        session.execute(text("DELETE FROM student_audit_cache"))
        session.commit()
        after, from_cache = audit_with_cache(session, student)

    assert from_cache is False
    assert after.model_dump_json() == before.model_dump_json()


# ==========================================================================
# Part 7 - the provider failure matrix
# ==========================================================================


class ScriptedModel:
    """A scripted explanation model. Never touches a network.

    Deliberately implements the same port the real provider adapts to, so
    the service under test is the production one.
    """

    def __init__(self, behaviour, *, available: bool = True) -> None:
        self.behaviour = behaviour
        self._available = available
        self.calls = 0

    def is_available(self) -> bool:
        return self._available

    def generate(self, request) -> str:
        self.calls += 1
        if callable(self.behaviour):
            return self.behaviour(self.calls)
        return self.behaviour


def _evidence_and_service(session, scenario, model):
    """Build the real service around a scripted model."""
    from app.services.audit.baseline import compute_baseline
    from app.services.explanations import RecommendationExplanationService
    from app.services.search.bm25 import build_bm25
    from app.services.search.documents import build_course_documents
    from app.services.search.synonyms import ExpandingSearcher

    audit = DegreeAuditEngine(session).audit(scenario["student"])
    baseline = compute_baseline(session, scenario["student"]).satisfied
    documents = build_course_documents(session)
    service = RecommendationExplanationService(
        documents_by_key={d.course_key: d for d in documents},
        searcher=ExpandingSearcher(build_bm25(documents)),
        model=model,
    )
    return audit, baseline, service


@requires_db
@pytest.mark.parametrize(
    "exception_name",
    [
        "TimeoutError", "ConnectionError", "HTTP_408", "HTTP_409", "HTTP_429",
        "HTTP_500", "HTTP_502", "HTTP_503", "EmptyResponse", "MalformedJSON",
    ],
)
def test_every_provider_failure_falls_back_deterministically(exception_name) -> None:
    """The matrix. Every one returns a correct explanation, never an error.

    The provider layer already collapses vendor exceptions into one
    provider-neutral `ProviderError`, so from the service's side these are
    the same shape - which is the point of that boundary. What is under test
    here is that the SERVICE does the right thing with each.
    """
    from app.llm.providers.anthropic import ProviderError

    def raise_it(_calls):
        if exception_name == "EmptyResponse":
            return ""
        if exception_name == "MalformedJSON":
            return "{not json at all"
        raise ProviderError(f"provider call failed ({exception_name})")

    with _session() as session:
        scenario = _scenario(session)
        session.commit()
        model = ScriptedModel(raise_it)
        audit, baseline, service = _evidence_and_service(session, scenario, model)

        outcome = service.explain_recommendation(
            audit, scenario["course"].course_string,
            baseline_satisfied=baseline,
        )

    # Always an explanation, always the deterministic one.
    assert outcome.explanation.summary
    assert outcome.used_model is False
    assert outcome.explanation.generated_by == "deterministic"

    metrics = get_metrics()
    assert metrics.counter(EXPLANATION_DETERMINISTIC) == 1
    if exception_name in {"EmptyResponse", "MalformedJSON"}:
        # The provider returned; the OUTPUT was unusable.
        assert metrics.counter(EXPLANATION_MODEL_REJECTED) == 1
        assert metrics.counter(EXPLANATION_MODEL_FAILED) == 0
    else:
        assert metrics.counter(EXPLANATION_MODEL_FAILED) == 1
        assert metrics.counter(EXPLANATION_MODEL_REJECTED) == 0


@requires_db
def test_a_provider_failure_is_attempted_exactly_once_by_the_service() -> None:
    """Rule: do not add another retry layer.

    Retries belong to the SDK, which retries only transient failures. A
    second application-level retry would multiply cost and turn one user
    request into several billable calls.
    """
    from app.llm.providers.anthropic import ProviderError

    def always_fail(_calls):
        raise ProviderError("provider call failed (TimeoutError)")

    with _session() as session:
        scenario = _scenario(session)
        session.commit()
        model = ScriptedModel(always_fail)
        audit, baseline, service = _evidence_and_service(session, scenario, model)
        service.explain_recommendation(audit, scenario["course"].course_string,
                                       baseline_satisfied=baseline)

    assert model.calls == 1, "the service must not retry; the SDK owns that"
    assert get_metrics().counter(EXPLANATION_MODEL_ATTEMPTED) == 1


@requires_db
def test_an_unavailable_provider_is_distinguishable_from_a_failing_one() -> None:
    """They look identical in the response and need opposite responses from
    an operator: configure something, versus fix something."""
    with _session() as session:
        scenario = _scenario(session)
        session.commit()
        model = ScriptedModel("unused", available=False)
        audit, baseline, service = _evidence_and_service(session, scenario, model)
        outcome = service.explain_recommendation(
            audit, scenario["course"].course_string, baseline_satisfied=baseline
        )

    assert outcome.used_model is False
    assert model.calls == 0
    metrics = get_metrics()
    assert metrics.counter(EXPLANATION_PROVIDER_UNAVAILABLE) == 1
    assert metrics.counter(EXPLANATION_MODEL_ATTEMPTED) == 0
    assert metrics.counter(EXPLANATION_MODEL_FAILED) == 0


@requires_db
def test_an_unsupported_claim_is_rejected_and_the_deterministic_answer_stands() -> None:
    """The model asserting something CoursePilot did not decide.

    This is the case the whole validation layer exists for: a fluent,
    well-formed answer that is academically false.
    """
    import json

    fabricated = json.dumps({
        "summary": "You have already satisfied every graduation requirement.",
        "reasons": ["All requirements are complete."],
        "course_information": [],
        "limitations": [],
        "citations": [],
    })

    with _session() as session:
        scenario = _scenario(session)
        session.commit()
        model = ScriptedModel(fabricated)
        audit, baseline, service = _evidence_and_service(session, scenario, model)
        outcome = service.explain_recommendation(
            audit, scenario["course"].course_string, baseline_satisfied=baseline
        )

    assert outcome.used_model is False, "a fabricated claim must not be served"
    assert outcome.rejection_problems
    assert get_metrics().counter(EXPLANATION_MODEL_REJECTED) == 1


@requires_db
def test_a_provider_failure_never_alters_the_degree_engine_result() -> None:
    """The invariant, checked directly rather than inferred."""
    from app.llm.providers.anthropic import ProviderError

    with _session() as session:
        scenario = _scenario(session)
        session.commit()
        direct = DegreeAuditEngine(session).audit(scenario["student"])

        def boom(_calls):
            raise ProviderError("provider call failed (ConnectionError)")

        model = ScriptedModel(boom)
        audit, baseline, service = _evidence_and_service(session, scenario, model)
        service.explain_recommendation(audit, scenario["course"].course_string,
                                       baseline_satisfied=baseline)

        after = DegreeAuditEngine(session).audit(scenario["student"])

    assert after.model_dump_json() == direct.model_dump_json()


@requires_db
def test_a_degree_engine_failure_is_never_answered_by_a_model(monkeypatch) -> None:
    """A domain failure must surface as a failure, not as a confident answer."""
    from app.services.audit.engine import DegreeAuditEngine as Engine

    with _session() as session:
        scenario = _scenario(session)
        session.commit()
        student_id = scenario["student"].id

    def boom(self, student, **kwargs):
        raise RuntimeError("engine exploded")

    monkeypatch.setattr(Engine, "audit", boom)
    with _session() as session:
        student = session.get(Student, student_id)
        with pytest.raises(RuntimeError):
            audit_with_cache(session, student)
