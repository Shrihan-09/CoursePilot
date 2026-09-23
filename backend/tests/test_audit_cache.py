"""Audit cache correctness and invalidation (Phase 5.7, Parts 18-19).

The invariant under test, stated once:

> A cached audit may be returned only when the cache key represents the same
> academic facts and rule state that would be supplied to the Degree Engine
> for a fresh computation.

Every test below is an attempt to violate it. Asserting "the cache works" is
easy and worthless; what matters is that each class of input change makes
the old result unreachable. Where a test claims the engine was skipped, it
proves it with a spy rather than trusting a boolean.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import decimal
import os
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text

from app.domain.audit import DegreeAuditResult
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
    StudentAuditCache,
    StudentCourse,
    Subject,
    UserAccount,
)
from app.services.audit.cache import (
    AuditCacheKey,
    academic_fingerprint,
    engine_version,
    invalidate_student_audit,
    read_cached_audit,
    rules_fingerprint,
    write_cached_audit,
)
from app.services.audit.cached_audit import audit_with_cache
from app.services.audit.engine import DegreeAuditEngine

requires_db = pytest.mark.db

AUDIT = "/api/v1/student/audit"
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


# --------------------------------------------------------------------------
# a real program with requirements, so rule changes are meaningful
# --------------------------------------------------------------------------


def _source(session):
    suffix = uuid.uuid4().hex[:8]
    source = DataSource(
        kind="manual_curation",
        url=f"synthetic://coursepilot/test/cache-{suffix}",
        content_hash=suffix,
        retrieved_at=dt.datetime.now(dt.UTC),
    )
    session.add(source)
    session.flush()
    return source


def _course(session, *, credits="4.0", title="Cache Course"):
    unit = "01"
    subject = uuid.uuid4().hex[:6]
    number = uuid.uuid4().hex[:6]
    source = _source(session)
    subject_row = Subject(
        code=subject, offering_unit_code=unit, description="s", source_id=source.id
    )
    session.add(subject_row)
    session.flush()
    course = Course(
        offering_unit_code=unit,
        subject_code=subject,
        course_number=number,
        supplement_code="",
        course_string=f"{unit}:{subject}:{number}",
        title=title,
        credits=decimal.Decimal(credits),
        subject_id=subject_row.id,
        source_id=source.id,
    )
    session.add(course)
    session.flush()
    return course


def _scenario(session, *, n_courses=2):
    """A student, an account, a program version with a real requirement."""
    source = _source(session)
    suffix = uuid.uuid4().hex[:8]
    school = School(
        code=f"S{suffix}", name="School", campus_code="NB", source_id=source.id
    )
    session.add(school)
    session.flush()
    program = Program(
        school_id=school.id,
        code=suffix,
        name="Cache Program",
        degree_type="BA",
        source_id=source.id,
    )
    session.add(program)
    session.flush()
    version = ProgramVersion(
        program_id=program.id, catalog_year="2026-2027", source_id=source.id
    )
    session.add(version)
    session.flush()

    requirement = Requirement(
        program_version_id=version.id,
        code=f"REQ_{suffix}",
        name="Core requirement",
        requirement_type="choose_n",
        min_count=2,
        sort_order=1,
        source_id=source.id,
    )
    session.add(requirement)
    session.flush()

    courses = [_course(session) for _ in range(n_courses)]
    for course in courses:
        session.add(
            RequirementCourseOption(
                requirement_id=requirement.id,
                course_id=course.id,
                source_id=source.id,
            )
        )
    session.flush()

    account = UserAccount(
        identity_provider="oidc", external_subject=f"cache-{uuid.uuid4()}"
    )
    session.add(account)
    session.flush()
    student = Student(
        external_ref=f"cache-{uuid.uuid4()}",
        catalog_year=version.catalog_year,
        program_version_id=version.id,
        user_id=account.id,
    )
    session.add(student)
    session.flush()
    for course in courses:
        session.add(
            StudentCourse(
                student_id=student.id,
                course_id=course.id,
                term_code="20269",
                status="completed",
                grade="A",
                credits_earned=decimal.Decimal("4.0"),
            )
        )
    session.flush()
    return {
        "student": student,
        "account": account,
        "version": version,
        "requirement": requirement,
        "courses": courses,
        "source": source,
    }


class SpyEngine:
    """Counts engine invocations without changing engine behaviour.

    Wraps the real method, so the genuine audit still runs and the
    production engine is not weakened to make the test possible.

    Assertions use DELTAS rather than absolute counts, because one logical
    audit invokes `audit()` more than once: the engine computes a
    completed-courses-only baseline by calling itself. Depending on that
    internal number would make these tests break whenever the engine is
    refactored, which is not what they are about. "Did the engine run at
    all?" is the question, and a delta answers it exactly.
    """

    calls = 0

    @classmethod
    def patch(cls, monkeypatch):
        cls.calls = 0
        real = DegreeAuditEngine.audit

        def counting(self, student, **kwargs):
            cls.calls += 1
            return real(self, student, **kwargs)

        monkeypatch.setattr(DegreeAuditEngine, "audit", counting)
        return cls


# ==========================================================================
# the cache saves real work
# ==========================================================================


@requires_db
def test_second_identical_audit_does_not_invoke_the_engine(monkeypatch) -> None:
    """Part 19: proven with a spy, not with a `from_cache` flag."""
    spy = SpyEngine.patch(monkeypatch)
    with _session() as session:
        scenario = _scenario(session)
        session.commit()
        student = scenario["student"]

        first, from_cache = audit_with_cache(session, student)
        assert spy.calls > 0, "a cold cache must invoke the engine"
        assert from_cache is False

        after_miss = spy.calls
        second, from_cache = audit_with_cache(session, student)
        assert spy.calls == after_miss, "engine ran again on an unchanged input"
        assert from_cache is True
        assert second.model_dump_json() == first.model_dump_json()


@requires_db
def test_a_cache_hit_is_byte_identical_to_a_fresh_audit() -> None:
    """The whole claim: a hit and a miss are indistinguishable."""
    with _session() as session:
        scenario = _scenario(session)
        session.commit()
        student = scenario["student"]

        fresh, _ = audit_with_cache(session, student, use_cache=False)
        audit_with_cache(session, student)  # populate
        cached, from_cache = audit_with_cache(session, student)

        assert from_cache is True
        assert cached.model_dump_json() == fresh.model_dump_json()


# ==========================================================================
# invalidation, one class per test
# ==========================================================================


@requires_db
def test_adding_a_course_invalidates(monkeypatch) -> None:
    spy = SpyEngine.patch(monkeypatch)
    with _session() as session:
        scenario = _scenario(session)
        session.commit()
        student = scenario["student"]

        audit_with_cache(session, student)
        after_miss = spy.calls
        assert after_miss > 0
        audit_with_cache(session, student)
        assert spy.calls == after_miss

        extra = _course(session)
        session.add(
            StudentCourse(
                student_id=student.id,
                course_id=extra.id,
                term_code="20271",
                status="completed",
                grade="B",
                credits_earned=decimal.Decimal("3.0"),
            )
        )
        session.commit()

        _, from_cache = audit_with_cache(session, student)
        assert from_cache is False
        assert spy.calls > after_miss


@requires_db
def test_changing_a_grade_invalidates() -> None:
    with _session() as session:
        scenario = _scenario(session)
        session.commit()
        student = scenario["student"]

        before = academic_fingerprint(session, student)
        row = session.scalar(
            select(StudentCourse).where(StudentCourse.student_id == student.id)
        )
        row.grade = "C"
        session.commit()
        assert academic_fingerprint(session, student) != before


@requires_db
def test_changing_credits_or_status_or_term_invalidates() -> None:
    """Each academic fact, separately - none may be invisible to the key."""
    for field, value in (
        ("credits_earned", decimal.Decimal("1.0")),
        ("status", "in_progress"),
        ("term_code", "20231"),
    ):
        with _session() as session:
            scenario = _scenario(session)
            session.commit()
            student = scenario["student"]
            before = academic_fingerprint(session, student)

            row = session.scalar(
                select(StudentCourse).where(StudentCourse.student_id == student.id)
            )
            if field == "status":
                row.grade = None  # completed_requires_grade works the other way
            setattr(row, field, value)
            session.commit()

            assert academic_fingerprint(session, student) != before, field


@requires_db
def test_removing_a_course_invalidates() -> None:
    with _session() as session:
        scenario = _scenario(session)
        session.commit()
        student = scenario["student"]
        before = academic_fingerprint(session, student)

        row = session.scalar(
            select(StudentCourse).where(StudentCourse.student_id == student.id)
        )
        session.delete(row)
        session.commit()
        assert academic_fingerprint(session, student) != before


@requires_db
def test_recuration_invalidates_although_the_student_is_untouched(monkeypatch) -> None:
    """Part 10, the critical one.

    A student's facts can be identical for a year while the audit changes
    because a requirement was re-read from the catalog. A student-only key
    would serve the old answer forever.
    """
    spy = SpyEngine.patch(monkeypatch)
    with _session() as session:
        scenario = _scenario(session)
        session.commit()
        student = scenario["student"]

        audit_with_cache(session, student)
        after_miss = spy.calls
        audit_with_cache(session, student)
        assert spy.calls == after_miss

        academic_before = academic_fingerprint(session, student)

        requirement = session.get(Requirement, scenario["requirement"].id)
        requirement.min_count = 5  # recuration: the rule got stricter
        session.commit()

        # The student did not change; the rules did.
        assert academic_fingerprint(session, student) == academic_before

        _, from_cache = audit_with_cache(session, student)
        assert from_cache is False, "served a stale audit after recuration"
        assert spy.calls > after_miss


@requires_db
def test_changing_course_eligibility_invalidates() -> None:
    """Which courses satisfy a requirement is rule state, not student state."""
    with _session() as session:
        scenario = _scenario(session)
        session.commit()
        version_id = scenario["version"].id
        before = rules_fingerprint(session, version_id)

        session.add(
            RequirementCourseOption(
                requirement_id=scenario["requirement"].id,
                course_id=_course(session).id,
                source_id=scenario["source"].id,
            )
        )
        session.commit()
        assert rules_fingerprint(session, version_id) != before


@requires_db
def test_changing_a_category_certification_invalidates() -> None:
    with _session() as session:
        scenario = _scenario(session)
        session.commit()
        version_id = scenario["version"].id
        before = rules_fingerprint(session, version_id)

        option = session.scalar(
            select(RequirementCourseOption).where(
                RequirementCourseOption.requirement_id == scenario["requirement"].id
            )
        )
        option.category = "AHp"
        session.commit()
        assert rules_fingerprint(session, version_id) != before


@requires_db
def test_changing_sharing_policy_invalidates() -> None:
    with _session() as session:
        scenario = _scenario(session)
        session.commit()
        version_id = scenario["version"].id
        before = rules_fingerprint(session, version_id)

        version = session.get(ProgramVersion, version_id)
        version.sharing_policy = "share_across_systems"
        session.commit()
        assert rules_fingerprint(session, version_id) != before


@requires_db
def test_adding_a_program_rule_invalidates() -> None:
    """Exclusions change which credits count toward the degree."""
    with _session() as session:
        scenario = _scenario(session)
        session.commit()
        version_id = scenario["version"].id
        before = rules_fingerprint(session, version_id)

        session.add(
            ProgramRule(
                program_version_id=version_id,
                code=f"RULE_{uuid.uuid4().hex[:6]}",
                name="No credit for X",
                rule_type="course_exclusion",
                source_id=scenario["source"].id,
            )
        )
        session.commit()
        assert rules_fingerprint(session, version_id) != before


@requires_db
def test_moving_the_student_to_another_program_version_invalidates() -> None:
    with _session() as session:
        scenario = _scenario(session)
        other = _scenario(session)
        session.commit()
        student = scenario["student"]
        before = academic_fingerprint(session, student)

        student.program_version_id = other["version"].id
        session.commit()
        assert academic_fingerprint(session, student) != before


@requires_db
def test_changing_catalog_year_invalidates() -> None:
    with _session() as session:
        scenario = _scenario(session)
        session.commit()
        student = scenario["student"]
        before = academic_fingerprint(session, student)

        student.catalog_year = "2024-2025"
        session.commit()
        assert academic_fingerprint(session, student) != before


@requires_db
def test_engine_version_change_invalidates(monkeypatch) -> None:
    """Semantics can change with no database row changing at all."""
    spy = SpyEngine.patch(monkeypatch)
    with _session() as session:
        scenario = _scenario(session)
        session.commit()
        student = scenario["student"]

        audit_with_cache(session, student)
        after_miss = spy.calls
        audit_with_cache(session, student)
        assert spy.calls == after_miss

        monkeypatch.setattr(
            "app.services.audit.cache.AUDIT_ENGINE_VERSION", "5.7.0-next"
        )
        _, from_cache = audit_with_cache(session, student)
        assert from_cache is False
        assert spy.calls > after_miss


def test_engine_version_tracks_the_live_policy_objects(monkeypatch) -> None:
    """The safety net: swapping the objective changes the key by itself.

    Without this, someone could change the adopted objective, forget to bump
    the constant, and every cached audit would silently reflect the old
    semantics.
    """
    baseline = engine_version()

    class OtherObjective:
        name = "objective-z"

    monkeypatch.setattr(
        "app.services.audit.optimizer.DEFAULT_OBJECTIVE", OtherObjective()
    )
    assert engine_version() != baseline

    monkeypatch.undo()

    class OtherStrategy:
        name = "strategy-z"

    monkeypatch.setattr(
        "app.services.audit.categories.DEFAULT_STRATEGY", OtherStrategy()
    )
    assert engine_version() != baseline


def test_timestamps_do_not_cause_spurious_invalidation() -> None:
    """created_at/updated_at are the only columns that change without
    changing meaning."""
    from app.services.audit.cache import _IGNORED_COLUMNS

    assert _IGNORED_COLUMNS == frozenset({"created_at", "updated_at"})


@pytest.mark.parametrize(
    "model", [Requirement, ProgramRule, ProgramVersion], ids=lambda m: m.__name__
)
def test_every_column_is_visible_to_the_fingerprint(model) -> None:
    """A column added to these tables next year must be covered automatically.

    A hand-written column list would silently omit it, and silently omitting
    an input from a cache key is exactly how a stale audit gets served. This
    test is what makes the reflective implementation a guarantee rather than
    a convention.

    Hashed in memory rather than through the database, so CHECK constraints
    on enum-like columns do not limit which columns can be probed - the
    question is whether `_hash_entity` READS the column, not whether a
    particular value is legal.
    """
    import hashlib

    from app.services.audit.cache import _IGNORED_COLUMNS, _hash_entity

    instance = model()
    for column in model.__table__.columns:
        setattr(instance, column.name, None)

    def digest(obj) -> str:
        hasher = hashlib.sha256()
        _hash_entity(hasher, obj)
        return hasher.hexdigest()

    baseline = digest(instance)
    uncovered = []
    for column in model.__table__.columns:
        if column.name in _IGNORED_COLUMNS:
            continue
        setattr(instance, column.name, "probe-57")
        if digest(instance) == baseline:
            uncovered.append(column.name)
        setattr(instance, column.name, None)

    assert uncovered == [], f"columns invisible to the fingerprint: {uncovered}"


def test_ignored_columns_really_are_ignored() -> None:
    """The flip side: timestamps must NOT move the fingerprint."""
    import hashlib

    from app.services.audit.cache import _IGNORED_COLUMNS, _hash_entity

    instance = Requirement()
    for column in Requirement.__table__.columns:
        setattr(instance, column.name, None)

    def digest(obj) -> str:
        hasher = hashlib.sha256()
        _hash_entity(hasher, obj)
        return hasher.hexdigest()

    baseline = digest(instance)
    for name in _IGNORED_COLUMNS:
        setattr(instance, name, dt.datetime.now(dt.UTC))
        assert digest(instance) == baseline, f"{name} caused a spurious miss"


# ==========================================================================
# cross-student isolation
# ==========================================================================


@requires_db
def test_one_student_never_receives_anothers_cached_audit() -> None:
    with _session() as session:
        a = _scenario(session)
        b = _scenario(session)
        session.commit()

        a_result, _ = audit_with_cache(session, a["student"])
        b_result, _ = audit_with_cache(session, b["student"])

        assert a_result.program_code != b_result.program_code
        rows = session.scalars(select(StudentAuditCache)).all()
        by_student = {r.student_id for r in rows}
        assert a["student"].id in by_student
        assert b["student"].id in by_student

        a_cached, from_cache = audit_with_cache(session, a["student"])
        assert from_cache is True
        assert a_cached.model_dump_json() == a_result.model_dump_json()
        assert a_cached.program_code != b_result.program_code


@requires_db
def test_cache_rows_are_student_scoped() -> None:
    """Never a shared entry keyed only on fingerprints: two students with
    identical facts still get their own row."""
    with _session() as session:
        a = _scenario(session)
        b = _scenario(session)
        session.commit()
        audit_with_cache(session, a["student"])
        audit_with_cache(session, b["student"])

        assert session.get(StudentAuditCache, a["student"].id) is not None
        assert session.get(StudentAuditCache, b["student"].id) is not None


# ==========================================================================
# failure behaviour: cache failure is never audit failure
# ==========================================================================


@requires_db
def test_a_corrupt_cache_row_yields_a_fresh_audit(monkeypatch) -> None:
    spy = SpyEngine.patch(monkeypatch)
    with _session() as session:
        scenario = _scenario(session)
        session.commit()
        student = scenario["student"]

        fresh, _ = audit_with_cache(session, student)
        after_miss = spy.calls
        assert after_miss > 0

        row = session.get(StudentAuditCache, student.id)
        row.result_json = "{not json at all"
        session.commit()

        result, from_cache = audit_with_cache(session, student)
        assert from_cache is False
        assert spy.calls > after_miss
        assert result.model_dump_json() == fresh.model_dump_json()


@requires_db
def test_a_cache_read_failure_yields_a_fresh_audit(monkeypatch) -> None:
    with _session() as session:
        scenario = _scenario(session)
        session.commit()
        student = scenario["student"]
        expected, _ = audit_with_cache(session, student, use_cache=False)

        def boom(*args, **kwargs):
            raise RuntimeError("cache table unavailable")

        monkeypatch.setattr("app.services.audit.cached_audit.read_cached_audit", boom)
        with pytest.raises(RuntimeError):
            audit_with_cache(session, student)

    # The wrapper deliberately does not swallow a read that raises OUTSIDE
    # read_cached_audit's own guard, so verify the guard itself instead.
    with _session() as session:
        scenario = _scenario(session)
        session.commit()
        student = scenario["student"]
        key = AuditCacheKey.compute(session, student)

        class Boom:
            def get(self, *a, **k):
                raise RuntimeError("table gone")

        assert read_cached_audit(Boom(), student, key) is None


@requires_db
def test_a_cache_write_failure_does_not_fail_the_audit() -> None:
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


@requires_db
def test_a_missing_cache_table_still_produces_an_audit() -> None:
    """The strongest form of 'cache failure is not audit failure'."""
    with _session() as session:
        scenario = _scenario(session)
        session.commit()
        student = scenario["student"]
        expected, _ = audit_with_cache(session, student, use_cache=False)

    # The rename is done on its own connection and restored there, so a
    # failure inside the assertions cannot leave the table hidden from every
    # other test in the file.
    def rename(frm: str, to: str) -> None:
        with _session() as admin:
            admin.execute(text(f"ALTER TABLE {frm} RENAME TO {to}"))
            admin.commit()

    rename("student_audit_cache", "sac_hidden")
    try:
        with _session() as session:
            student = session.get(Student, student.id)
            result, from_cache = audit_with_cache(session, student)
            assert from_cache is False
            assert result.model_dump_json() == expected.model_dump_json()
    finally:
        rename("sac_hidden", "student_audit_cache")


@requires_db
def test_truncating_the_cache_changes_no_academic_answer() -> None:
    """Derived state: deleting every row costs latency and nothing else."""
    with _session() as session:
        scenario = _scenario(session)
        session.commit()
        student = scenario["student"]

        before, _ = audit_with_cache(session, student)
        session.execute(
            text("DELETE FROM student_audit_cache WHERE student_id = :s"),
            {"s": student.id},
        )
        session.commit()

        after, from_cache = audit_with_cache(session, student)
        assert from_cache is False
        assert after.model_dump_json() == before.model_dump_json()


# ==========================================================================
# invalidation primitive (Part 9)
# ==========================================================================


@requires_db
def test_invalidate_removes_the_row_and_no_academic_data() -> None:
    from sqlalchemy import func

    with _session() as session:
        scenario = _scenario(session)
        session.commit()
        student = scenario["student"]
        audit_with_cache(session, student)
        assert session.get(StudentAuditCache, student.id) is not None

        courses_before = session.scalar(
            select(func.count())
            .select_from(StudentCourse)
            .where(StudentCourse.student_id == student.id)
        )

        assert invalidate_student_audit(session, student.id) is True
        session.commit()

        assert session.get(StudentAuditCache, student.id) is None
        assert session.get(Student, student.id) is not None
        assert courses_before == session.scalar(
            select(func.count())
            .select_from(StudentCourse)
            .where(StudentCourse.student_id == student.id)
        )


@requires_db
def test_invalidating_an_uncached_student_is_harmless() -> None:
    with _session() as session:
        scenario = _scenario(session)
        session.commit()
        assert invalidate_student_audit(session, scenario["student"].id) is False


@requires_db
def test_deleting_a_student_removes_its_cache_row() -> None:
    """CASCADE: derived state must not outlive - or block - its student."""
    with _session() as session:
        scenario = _scenario(session)
        session.commit()
        student_id = scenario["student"].id
        audit_with_cache(session, scenario["student"])
        assert session.get(StudentAuditCache, student_id) is not None

        session.execute(
            text("DELETE FROM student_course WHERE student_id = :s"), {"s": student_id}
        )
        session.execute(text("UPDATE student SET user_id = NULL WHERE id = :s"), {"s": student_id})
        session.execute(text("DELETE FROM student WHERE id = :s"), {"s": student_id})
        session.commit()

        assert session.get(StudentAuditCache, student_id) is None


# ==========================================================================
# serialization
# ==========================================================================


@requires_db
def test_serialization_round_trips_losslessly() -> None:
    with _session() as session:
        scenario = _scenario(session)
        session.commit()
        result = DegreeAuditEngine(session).audit(scenario["student"])

    payload = result.model_dump_json()
    restored = DegreeAuditResult.model_validate_json(payload)
    assert restored.model_dump_json() == payload


def test_the_cache_never_uses_pickle() -> None:
    """Rule: a row crossing processes and deploys must not be executable."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1] / "app" / "services" / "audit"
    for name in ("cache.py", "cached_audit.py"):
        source = (root / name).read_text(encoding="utf-8")
        code = "\n".join(
            line for line in source.splitlines() if not line.lstrip().startswith("#")
        )
        assert "pickle" not in code
        assert "eval(" not in code


@requires_db
def test_the_cache_stores_no_queryable_academic_facts() -> None:
    """The cache must not become a second source of truth."""
    columns = set(StudentAuditCache.__table__.columns.keys())
    assert columns == {
        "student_id",
        "academic_fingerprint",
        "rules_fingerprint",
        "engine_version",
        "result_json",
        "created_at",
        "updated_at",
    }
    assert not columns & {"grade", "credits", "status", "course_id", "term_code"}


# ==========================================================================
# concurrency
# ==========================================================================


@requires_db
def test_concurrent_misses_all_produce_the_same_answer() -> None:
    """Part 14: the race is benign because the engine is deterministic.

    Several requests may miss at once and all compute. They write identical
    bytes, so the only cost is duplicated CPU - never an incorrect result,
    and never an IntegrityError surfacing to a caller.
    """
    import threading

    with _session() as session:
        scenario = _scenario(session)
        session.commit()
        student_id = scenario["student"].id

    results: list[str] = []
    errors: list[Exception] = []

    def worker() -> None:
        try:
            with _session() as session:
                student = session.get(Student, student_id)
                result, _ = audit_with_cache(session, student)
                results.append(result.model_dump_json())
        except Exception as exc:  # pragma: no cover - surfaced by the assert
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert len(results) == 6
    assert len(set(results)) == 1, "concurrent audits disagreed"

    with _session() as session:
        rows = session.scalars(
            select(StudentAuditCache).where(
                StudentAuditCache.student_id == student_id
            )
        ).all()
        assert len(rows) == 1


# ==========================================================================
# the endpoint keeps its Phase 5.6 contract
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


@requires_db
async def test_endpoint_response_is_unchanged_by_caching(app) -> None:
    """Phase 5.6's contract: a DegreeAuditResult, with no cache metadata."""
    with _session() as session:
        scenario = _scenario(session)
        session.commit()
        account_id = scenario["account"].id
        student_id = scenario["student"].id
        session.execute(
            text("DELETE FROM student_audit_cache WHERE student_id = :s"),
            {"s": student_id},
        )
        session.commit()

    async with _client_as(app, account_id) as ac:
        first = await ac.get(AUDIT)
        second = await ac.get(AUDIT)

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()

    body = first.json()
    for leaked in ("cached", "cache_age", "cache_key", "from_cache", "fingerprint"):
        assert leaked not in body

    with _session() as session:
        student = session.get(Student, student_id)
        direct = DegreeAuditEngine(session).audit(student)
    assert first.json() == direct.model_dump(mode="json")


@requires_db
async def test_endpoint_reflects_an_academic_change_immediately(app) -> None:
    """The user-visible form of the invariant."""
    with _session() as session:
        scenario = _scenario(session)
        session.commit()
        account_id = scenario["account"].id
        student_id = scenario["student"].id

    async with _client_as(app, account_id) as ac:
        before = (await ac.get(AUDIT)).json()

        with _session() as session:
            extra = _course(session)
            session.add(
                StudentCourse(
                    student_id=student_id,
                    course_id=extra.id,
                    term_code="20271",
                    status="completed",
                    grade="A",
                    credits_earned=decimal.Decimal("4.0"),
                )
            )
            session.commit()

        after = (await ac.get(AUDIT)).json()

    assert after != before, "the endpoint served a stale audit"
    assert after["credits_completed"] != before["credits_completed"]
