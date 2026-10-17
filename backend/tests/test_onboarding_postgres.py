"""Phase 7.0 on REAL PostgreSQL: uniqueness, concurrency, locking, rollback.

Threads with a Barrier start the racing calls together, each on its own
connection; the database - row locks, UNIQUE constraints, partial unique
indexes, transactions - is what must make the outcome correct. Skipped
without a `*_test` PostgreSQL (`requires_db`).
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.models import Student, StudentAuditCache, StudentCourse, StudentLinkEvent
from app.models.onboarding import ImportState, TranscriptImport
from app.services import history, onboarding
from app.services.transcripts import imports
from app.services.transcripts.storage import MemoryTranscriptStorage

from .support import onboarding_world
from .support.onboarding_world import statuses
from .support.synthetic_transcript import build_pdf, transcript_lines
from .test_multi_program import _session, requires_db
from .test_transcript_import_api import in_process


def _now():
    return datetime.now(UTC)


@pytest.fixture
def ob():
    with _session() as session:
        world = onboarding_world.build(session)
        world["account"] = onboarding_world.new_account(session)
        yield world


def _race(n, fn):
    barrier = threading.Barrier(n)
    results: list = [None] * n

    def run(i):
        barrier.wait()
        try:
            results[i] = ("ok", fn(i))
        except Exception as exc:   # noqa: BLE001 - recorded for the assertion
            results[i] = ("error", exc)

    threads = [threading.Thread(target=run, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(120)
    return results


def _create(ob):
    with _session() as s:
        onboarding.create_profile(s, ob["account"], program_key=ob["keys"]["pub"],
                                  catalog_year=ob["year"])
        s.commit()


def _stage(ob, lines=None) -> imports.TranscriptImport:
    c = ob["code"]
    data = build_pdf([transcript_lines(courses={"FALL 2024": lines or [
        f"{c['A']} OB A 3.0 A", f"{c['B']} OB B 3.0 B"]})])
    with _session() as s:
        imp, _ = imports.upload_transcript(s, ob["account"], data,
                                           storage=MemoryTranscriptStorage(),
                                           extractor=in_process, now=_now(), max_bytes=10**6)
        return imp.id, imp.version


def _count(model, account):
    with _session() as s:
        return s.scalar(select(func.count()).select_from(model).join(
            Student, Student.id == model.student_id).where(Student.user_id == account))


@requires_db
def test_concurrent_first_profile_creates_make_exactly_one_record(ob) -> None:
    def create(_i):
        with _session() as s:
            student, created = onboarding.create_profile(
                s, ob["account"], program_key=ob["keys"]["pub"], catalog_year=ob["year"])
            s.commit()
            return student.id, created

    results = _race(8, create)
    assert all(kind == "ok" for kind, _ in results), results
    ids = {value[0] for _, value in results}
    assert len(ids) == 1 and sum(value[1] for _, value in results) == 1
    with _session() as s:
        assert s.scalar(select(func.count()).select_from(Student).where(
            Student.user_id == ob["account"])) == 1
        assert s.scalar(select(func.count()).select_from(StudentLinkEvent).where(
            StudentLinkEvent.student_id == ids.pop())) == 1


@requires_db
def test_concurrent_confirmations_write_once(ob) -> None:
    _create(ob)
    import_id, version = _stage(ob)

    def confirm(_i):
        with _session() as s:
            result = imports.confirm_import(s, ob["account"], import_id,
                                            expected_version=version, now=_now())
            s.commit()
            return result.already_confirmed

    results = _race(6, confirm)
    assert all(kind == "ok" for kind, _ in results), results
    assert sorted(v for _, v in results) == [False] + [True] * 5
    assert _count(StudentCourse, ob["account"]) == 2


@requires_db
def test_a_manual_add_racing_a_confirmation_never_duplicates(ob) -> None:
    _create(ob)
    import_id, version = _stage(ob)

    def work(i):
        with _session() as s:
            if i == 0:
                imports.confirm_import(s, ob["account"], import_id,
                                       expected_version=version, now=_now())
            else:
                history.add_entry(s, ob["account"], course_code=ob["code"]["A"],
                                  supplement_code=None, term_code="20249", status="completed",
                                  grade="F", credits_earned=None, credit_origin=None)
            s.commit()
            return i

    results = _race(2, work)
    kinds = {i: r for i, r in enumerate(results)}
    with _session() as s:
        rows = s.execute(select(StudentCourse.course_id, StudentCourse.term_code,
                                StudentCourse.grade).join(Student).where(
            Student.user_id == ob["account"])).all()
    a_rows = [r for r in rows if r.course_id == ob["course_id"]["A"]]
    assert len(a_rows) == 1                     # never two (course, term) entries
    if kinds[0][0] == "ok":                     # confirm won: the manual add was refused
        assert isinstance(kinds[1][1], history.EntryInvalid)
        assert a_rows[0].grade == "A"
    else:                                       # manual add won: confirm re-checked, refused
        assert isinstance(kinds[0][1], imports.ImportRefused)
        assert kinds[0][1].code == "IMPORT_NOT_READY"
        assert a_rows[0].grade == "F" and len(rows) == 1


@requires_db
def test_a_review_edit_racing_a_confirmation_is_all_or_nothing(ob) -> None:
    _create(ob)
    import_id, version = _stage(ob)
    with _session() as s:
        row_id = s.get(TranscriptImport, import_id).rows[0].id

    def work(i):
        with _session() as s:
            if i == 0:
                imports.confirm_import(s, ob["account"], import_id,
                                       expected_version=version, now=_now())
            else:
                imports.edit_row(s, ob["account"], import_id, row_id, {"grade": "C"},
                                 expected_version=version, now=_now())
            s.commit()

    results = _race(2, work)
    with _session() as s:
        imp = s.get(TranscriptImport, import_id)
        grades = sorted(s.scalars(select(StudentCourse.grade).join(Student).where(
            Student.user_id == ob["account"])))
    if results[0][0] == "ok":
        # Confirm saw the version it reviewed; the edit lost (stale or not editable).
        assert isinstance(results[1][1], imports.ImportRefused)
        assert imp.state == ImportState.CONFIRMED and grades == ["A", "B"]
    else:
        assert results[1][0] == "ok" and results[0][1].code == "IMPORT_VERSION_STALE"
        assert imp.state == ImportState.EXTRACTED and grades == []


@requires_db
def test_concurrent_uploads_of_one_document_are_one_import(ob) -> None:
    _create(ob)
    c = ob["code"]
    data = build_pdf([transcript_lines(courses={"FALL 2024": [f"{c['A']} OB A 3.0 A"]})])

    def upload(_i):
        with _session() as s:
            imp, created = imports.upload_transcript(
                s, ob["account"], data, storage=MemoryTranscriptStorage(),
                extractor=in_process, now=_now(), max_bytes=10**6)
            return imp.id

    results = _race(4, upload)
    assert all(kind == "ok" for kind, _ in results), results
    assert len({v for _, v in results}) == 1


@requires_db
def test_a_database_failure_mid_confirmation_rolls_everything_back(ob, monkeypatch) -> None:
    _create(ob)
    import_id, version = _stage(ob)
    calls = {"n": 0}
    real = imports.StudentCourse

    def failing_add(self, obj, *a, **kw):
        if isinstance(obj, real):
            calls["n"] += 1
            if calls["n"] == 2:
                raise IntegrityError("INSERT", {}, Exception("injected"))
        return original_add(self, obj, *a, **kw)

    from sqlalchemy.orm import Session

    original_add = Session.add
    monkeypatch.setattr(Session, "add", failing_add)
    with _session() as s, pytest.raises(IntegrityError):
        imports.confirm_import(s, ob["account"], import_id, expected_version=version,
                               now=_now())
        s.commit()
    monkeypatch.setattr(Session, "add", original_add)
    assert _count(StudentCourse, ob["account"]) == 0
    with _session() as s:
        assert s.get(TranscriptImport, import_id).state == ImportState.EXTRACTED
        imports.confirm_import(s, ob["account"], import_id, expected_version=version,
                               now=_now())
        s.commit()
    assert _count(StudentCourse, ob["account"]) == 2


@requires_db
def test_a_cache_invalidation_failure_never_loses_the_write(ob, monkeypatch) -> None:
    from app.services.audit.cached_audit import audit_with_cache

    _create(ob)
    with _session() as s:
        student = onboarding.owned_student(s, ob["account"])
        audit_with_cache(s, student)
        assert s.get(StudentAuditCache, student.id) is not None

    def broken(*_a, **_k):
        raise RuntimeError("cache store unavailable")

    from sqlalchemy.orm import Session

    real_get = Session.get

    def get(self, entity, ident, *a, **kw):
        if entity is StudentAuditCache:
            broken()
        return real_get(self, entity, ident, *a, **kw)

    monkeypatch.setattr(Session, "get", get)
    with _session() as s:
        history.add_entry(s, ob["account"], course_code=ob["code"]["A"], supplement_code=None,
                          term_code="20249", status="completed", grade="A",
                          credits_earned=None, credit_origin=None)
        s.commit()
    monkeypatch.setattr(Session, "get", real_get)
    assert _count(StudentCourse, ob["account"]) == 1
    with _session() as s:
        student = onboarding.owned_student(s, ob["account"])
        result, from_cache = audit_with_cache(s, student)
        assert from_cache is False          # the stale row's key no longer matches
        assert statuses(result)["REQ_A"] == "satisfied"


@requires_db
def test_the_cache_never_stores_a_result_computed_from_newer_rows(ob, monkeypatch) -> None:
    """The A -> B -> A race: a write lands between the cache key and the
    engine. Without the post-engine key re-check, the old key would be stored
    beside a result computed from the new rows, and undoing the write would
    then serve that wrong result."""
    import app.services.audit.cached_audit as cached_audit

    _create(ob)
    real_engine = cached_audit._run_engine

    def engine_with_concurrent_write(session, student):
        with _session() as other:
            history.add_entry(other, ob["account"], course_code=ob["code"]["A"],
                              supplement_code=None, term_code="20249", status="completed",
                              grade="A", credits_earned=None, credit_origin=None)
            other.commit()
        return real_engine(session, student)

    monkeypatch.setattr(cached_audit, "_run_engine", engine_with_concurrent_write)
    with _session() as s:
        student = onboarding.owned_student(s, ob["account"])
        cached_audit.audit_with_cache(s, student)
    monkeypatch.setattr(cached_audit, "_run_engine", real_engine)
    with _session() as s:
        sid = onboarding.owned_student(s, ob["account"]).id
        assert s.get(StudentAuditCache, sid) is None     # the write was skipped
    # Undo (state A again): the audit must not report REQ_A satisfied.
    with _session() as s:
        [entry] = history.list_entries(s, onboarding.owned_student(s, ob["account"]))
        history.delete_entry(s, ob["account"], entry[0].id)
        s.commit()
    with _session() as s:
        result, _ = cached_audit.audit_with_cache(s, onboarding.owned_student(s, ob["account"]))
        assert statuses(result)["REQ_A"] == "unsatisfied"


@requires_db
def test_database_constraints_hold_without_the_service(ob) -> None:
    _create(ob)
    import_id, _version = _stage(ob)
    with _session() as s:
        student = onboarding.owned_student(s, ob["account"])
        imp = s.get(TranscriptImport, import_id)
        # A second OPEN import of the same bytes is refused by the partial index...
        s.add(TranscriptImport(student_id=student.id, state=ImportState.EXTRACTED,
                               content_sha256=imp.content_sha256, byte_size=1, warnings=[]))
        with pytest.raises(IntegrityError):
            s.flush()
        s.rollback()
        # ...but a closed one is history, and may repeat.
        s.add(TranscriptImport(student_id=student.id, state=ImportState.DISCARDED,
                               content_sha256=imp.content_sha256, byte_size=1, warnings=[]))
        s.flush()
        s.rollback()
        # One account, one record.
        s.add(Student(user_id=ob["account"], catalog_year="2026-2027",
                      program_version_id=ob["v_pub"]))
        with pytest.raises(IntegrityError):
            s.flush()
        s.rollback()
        # Unknown import states / row states are refused.
        s.add(TranscriptImport(student_id=student.id, state="approved", content_sha256="x",
                               byte_size=1, warnings=[]))
        with pytest.raises(IntegrityError):
            s.flush()
        s.rollback()
        # One (course, term) per record.
        for _ in range(2):
            s.add(StudentCourse(student_id=student.id, course_id=ob["course_id"]["C"],
                                term_code="20249", status="planned"))
        with pytest.raises(IntegrityError):
            s.flush()
        s.rollback()
        # A self-created record's event action is a known one.
        s.add(StudentLinkEvent(student_id=student.id, user_account_id=ob["account"],
                               performed_by_id=ob["account"], action="claimed"))
        with pytest.raises(IntegrityError):
            s.flush()
        s.rollback()
