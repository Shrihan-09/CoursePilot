"""Phase 7.4 (Prompt 04): a confirmed WebReg schedule import writes the record (PostgreSQL).

Confirmation writes, in ONE transaction: the in-progress `student_course` row
(added, planned -> in progress, or skipped when already in progress), the
exact section as a `student_current_section`, and the registration identity.
Fabricated schedules and sections only: subject 999, term Spring 2098 (20981).
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from sqlalchemy import func, select, text

from app.models import (
    Course,
    CourseOffering,
    CourseSection,
    DataSource,
    SectionMeeting,
    StudentCourse,
    Subject,
)
from app.models.current_section import StudentCurrentSection
from app.models.schedule_import import ScheduleImport, StudentRegistration
from app.services.scheduling import imports as service
from app.services.scheduling.pdf_worker import default_schedule_extractor, extract_layout
from app.services.transcripts.extract import ExtractedText

from .support.synthetic_webreg import build_schedule_pdf, objects
from .test_multi_program import _client, _session, requires_db, world  # noqa: F401

URL = "/api/v1/student/schedule-imports"
TERM, LABEL = "20981", "Spring 2098"


def _app():
    from app.main import create_app

    app = create_app()

    def extract(data):
        result = extract_layout(data, 20, 200000)
        assert result[0] == "ok"
        return ExtractedText(result[1], result[2], result[3])

    app.dependency_overrides[default_schedule_extractor] = lambda: extract
    return app


@pytest.fixture
def two_courses(world):  # noqa: F811
    """X has sections 01 (11111) and 02 (22222); Y has section 01 (33333)."""
    with _session() as s:
        manual = DataSource(kind="manual_curation", url="synthetic://webreg-7-4",
                            content_hash=uuid.uuid4().hex, retrieved_at=dt.datetime.now(dt.UTC),
                            version=1)
        s.add(manual)
        s.flush()
        subject = s.scalar(select(Subject).where(Subject.offering_unit_code == "01",
                                                 Subject.code == "999"))
        if subject is None:
            subject = Subject(code="999", offering_unit_code="01", description="Synthetic",
                              source_id=manual.id)
            s.add(subject)
            s.flush()
        taken = set(s.scalars(select(Course.course_number).where(Course.subject_code == "999")))
        numbers = [f"{n}" for n in range(100, 1000) if f"{n}" not in taken][:2]
        soc = DataSource(kind="rutgers_official_api", url="synthetic://soc-20981",
                         content_hash=uuid.uuid4().hex, term_code=TERM, coverage="complete",
                         retrieved_at=dt.datetime.now(dt.UTC), version=1)
        s.add(soc)
        s.flush()
        out = {}
        for name, number, secs in (("X", numbers[0], (("11111", "01", "M"), ("22222", "02", "T"))),
                                   ("Y", numbers[1], (("33333", "01", "W"),))):
            course = Course(offering_unit_code="01", subject_code="999", course_number=number,
                            supplement_code="", course_string=f"01:999:{number}",
                            title="Synthetic", credits=3, subject_id=subject.id,
                            source_id=manual.id)
            s.add(course)
            s.flush()
            offering = CourseOffering(course_id=course.id, term_code=TERM, campus_code="NB",
                                      source_id=soc.id)
            s.add(offering)
            s.flush()
            for index, section, day in secs:
                sec = CourseSection(offering_id=offering.id, term_code=TERM, index_number=index,
                                    section_number=section, campus_code="NB", open_status=True,
                                    source_id=soc.id)
                s.add(sec)
                s.flush()
                s.add(SectionMeeting(section_id=sec.id, meeting_index=0, meeting_day=day,
                                     start_time_military="1020", end_time_military="1140",
                                     meeting_mode_code="02", meeting_mode_desc="LEC",
                                     building_code="HLL", room_number="114",
                                     campus_abbrev="BUS"))
            out[name], out[f"{name}_id"] = course.course_string, course.id
        s.commit()
    yield out
    with _session() as s:
        for table in ("student_current_section", "student_registration", "student_course"):
            s.execute(text(f"DELETE FROM {table} WHERE term_code = :t"), {"t": TERM})
        s.execute(text("DELETE FROM schedule_import WHERE student_id IN (:a, :b)"),
                  {"a": world["a"], "b": world["b"]})
        s.execute(text("DELETE FROM section_meeting WHERE section_id IN "
                       "(SELECT id FROM course_section WHERE term_code = :t)"), {"t": TERM})
        s.execute(text("DELETE FROM course_section WHERE term_code = :t"), {"t": TERM})
        s.execute(text("DELETE FROM course_offering WHERE term_code = :t"), {"t": TERM})
        s.commit()


def _pdf(*records, credits="12.0"):
    """records: (course, section, index) - each drawn in one weekday column."""
    items = objects(term=LABEL, records=[
        (170 + 100 * i, 610, f"{c}:{sec}-{idx} (3.0)") for i, (c, sec, idx) in enumerate(records)])
    total = f"Total Credits {credits}"
    items = [(x, y, t.replace("Total Credits 12.0", total)) for x, y, t in items]
    return build_schedule_pdf(items)


def _record(student_id, course_id):
    with _session() as s:
        return s.scalar(select(StudentCourse.status).where(
            StudentCourse.student_id == student_id, StudentCourse.course_id == course_id,
            StudentCourse.term_code == TERM))


def _count(model, student_id):
    with _session() as s:
        return s.scalar(select(func.count()).select_from(model).where(
            model.student_id == student_id, model.term_code == TERM))


async def _stage(ac, pdf, code=201):
    r = await ac.post(URL, content=pdf)
    assert r.status_code == code, r.text
    return r.json()


async def _confirm(ac, imp, code=200):
    r = await ac.post(f"{URL}/{imp['id']}/confirm", json={"expected_version": imp["version"]})
    assert r.status_code == code, r.text
    return r.json()


def _rows(imp):
    return {r["course_string"]: r for r in imp["rows"]}


@requires_db
async def test_confirm_writes_history_sections_and_registrations(world, two_courses):  # noqa: F811
    x, y = two_courses["X"], two_courses["Y"]
    with _session() as s:                                     # Y is planned on the record
        s.add(StudentCourse(student_id=world["a"], course_id=two_courses["Y_id"], term_code=TERM,
                            status="planned"))
        s.commit()
    async with _client(_app(), world["account_a"]) as ac:
        imp = await _stage(ac, _pdf((x, "01", "11111"), (y, "01", "33333")))
        rows = _rows(imp)
        # The section is PRE-SELECTED by its index; nothing is written yet.
        assert rows[x]["state"] == "matched" and rows[x]["resolved_index"] == "11111"
        assert (rows[x]["history_effect"], rows[x]["section_effect"]) == ("add_in_progress", "add")
        assert rows[y]["history_effect"] == "planned_to_in_progress"
        assert _record(world["a"], two_courses["X_id"]) is None
        assert _count(StudentCurrentSection, world["a"]) == 0
        done = await _confirm(ac, imp)
    assert {r["course_string"]: r["outcome"] for r in done["rows"]} == {
        x: {"history": "add_in_progress", "section": "added"},
        y: {"history": "planned_to_in_progress", "section": "added"}}
    assert _record(world["a"], two_courses["X_id"]) == "in_progress"
    assert _record(world["a"], two_courses["Y_id"]) == "in_progress"
    with _session() as s:
        sections = {(c.course_string, c.index_number, c.source) for c in s.scalars(
            select(StudentCurrentSection).where(StudentCurrentSection.student_id == world["a"],
                                                StudentCurrentSection.term_code == TERM))}
        kinds = set(s.scalars(select(StudentCourse.source_kind).where(
            StudentCourse.student_id == world["a"], StudentCourse.term_code == TERM)))
    assert sections == {(x, "11111", "webreg_schedule_import"),
                        (y, "33333", "webreg_schedule_import")}
    assert kinds == {"student_uploaded_schedule"}
    assert _count(StudentRegistration, world["a"]) == 2


@requires_db
async def test_re_importing_the_same_schedule_writes_nothing_new(world, two_courses):  # noqa: F811
    x = two_courses["X"]
    async with _client(_app(), world["account_a"]) as ac:
        await _confirm(ac, await _stage(ac, _pdf((x, "01", "11111"))))
        # A new print of the same schedule (different bytes) is a new import ...
        again = await _stage(ac, _pdf((x, "01", "11111"), credits="3.0"))
        row = _rows(again)[x]
        assert (row["history_effect"], row["section_effect"]) == (
            "already_in_progress", "already_confirmed")
        done = await _confirm(ac, again)
        assert _rows(done)[x]["outcome"] == {"history": "already_in_progress",
                                             "section": "already_confirmed"}
    # ... that duplicates nothing.
    with _session() as s:
        assert s.scalar(select(func.count()).select_from(StudentCourse).where(
            StudentCourse.student_id == world["a"], StudentCourse.term_code == TERM)) == 1
    assert _count(StudentCurrentSection, world["a"]) == 1
    assert _count(StudentRegistration, world["a"]) == 1


@requires_db
async def test_a_reused_index_is_a_conflict_never_retargeted(world, two_courses):  # noqa: F811
    """The print says X section 02 with index 11111, but 11111 is X section 01."""
    x = two_courses["X"]
    async with _client(_app(), world["account_a"]) as ac:
        imp = await _stage(ac, _pdf((x, "02", "11111")))
        row = imp["rows"][0]
        assert (row["state"], row["issues"], row["section_id"]) == (
            "conflict", ["IDENTIFIERS_DISAGREE"], None)
        assert imp["ready"] is False
        await _confirm(ac, imp, code=422)
    assert _record(world["a"], two_courses["X_id"]) is None


@requires_db
async def test_a_missing_section_records_only_its_identity(world, two_courses):  # noqa: F811
    x = two_courses["X"]
    async with _client(_app(), world["account_a"]) as ac:
        imp = await _stage(ac, _pdf((x, "09", "99999")))
        row = imp["rows"][0]
        assert (row["state"], row["section_id"], row.get("history_effect")) == (
            "metadata_unavailable", None, None)
        await _confirm(ac, imp)
    assert _record(world["a"], two_courses["X_id"]) is None        # no history invented
    assert _count(StudentCurrentSection, world["a"]) == 0
    assert _count(StudentRegistration, world["a"]) == 1


@requires_db
async def test_a_completed_record_is_a_conflict_the_student_resolves(world, two_courses):  # noqa: F811
    x = two_courses["X"]
    with _session() as s:
        s.add(StudentCourse(student_id=world["a"], course_id=two_courses["X_id"], term_code=TERM,
                            status="completed", grade="A"))
        s.commit()
    async with _client(_app(), world["account_a"]) as ac:
        imp = await _stage(ac, _pdf((x, "01", "11111")))
        row = imp["rows"][0]
        assert row["state"] == "conflict" and "RECORD_SAYS_COMPLETED" in row["issues"]
        kept = await ac.patch(f"{URL}/{imp['id']}/rows/{row['id']}",
                              json={"expected_version": imp["version"], "keep_record": True})
        assert kept.status_code == 200, kept.text
        imp = kept.json()
        assert imp["ready"] and imp["rows"][0]["history_effect"] == "keep_completed"
        await _confirm(ac, imp)
    assert _record(world["a"], two_courses["X_id"]) == "completed"     # never overwritten
    assert _count(StudentCurrentSection, world["a"]) == 0


@requires_db
async def test_a_different_confirmed_section_needs_explicit_replacement(world, two_courses):  # noqa: F811
    x = two_courses["X"]
    async with _client(_app(), world["account_a"]) as ac:
        await _confirm(ac, await _stage(ac, _pdf((x, "02", "22222"))))
        imp = await _stage(ac, _pdf((x, "01", "11111")))
        row = imp["rows"][0]
        assert row["state"] == "conflict" and row["issues"] == ["CONFIRM_REPLACE_EXISTING"]
        imp = (await ac.patch(f"{URL}/{imp['id']}/rows/{row['id']}", json={
            "expected_version": imp["version"], "replace_existing": True})).json()
        assert imp["ready"] and imp["rows"][0]["section_effect"] == "replace"
        done = await _confirm(ac, imp)
        assert done["rows"][0]["outcome"]["section"] == "replaced"
    with _session() as s:
        assert set(s.scalars(select(StudentCurrentSection.index_number).where(
            StudentCurrentSection.student_id == world["a"],
            StudentCurrentSection.term_code == TERM))) == {"11111"}


@requires_db
async def test_a_course_missing_from_a_newer_import_is_flagged_never_deleted(world, two_courses):  # noqa: F811
    x, y = two_courses["X"], two_courses["Y"]
    async with _client(_app(), world["account_a"]) as ac:
        await _confirm(ac, await _stage(ac, _pdf((x, "01", "11111"), (y, "01", "33333"))))
        newer = await _stage(ac, _pdf((x, "01", "11111")))
        assert [(d["course_string"], d["index_number"]) for d in newer["possibly_dropped"]] == [
            (y, "33333")]
        await _confirm(ac, newer)
        regs = {r["course_string"]: r for r in
                (await ac.get("/api/v1/student/registrations")).json()["registrations"]}
        assert regs[y]["on_latest_import"] is False and regs[x]["on_latest_import"] is True
        current = (await ac.get("/api/v1/student/current-schedule")).json()
        flags = {c["course"]: c["not_on_latest_webreg"] for c in current["courses"]}
        assert flags == {x: False, y: True}
    # Nothing was deleted: Y is still in progress, with its section and registration.
    assert _record(world["a"], two_courses["Y_id"]) == "in_progress"
    assert _count(StudentCurrentSection, world["a"]) == 2
    assert _count(StudentRegistration, world["a"]) == 2


@requires_db
async def test_confirmation_is_atomic(world, two_courses, monkeypatch):  # noqa: F811
    """A failure after the history row is written leaves NOTHING behind."""
    x, y = two_courses["X"], two_courses["Y"]
    calls = []

    def boom(session, student, row, record, now):
        calls.append(row["course_string"])
        if len(calls) == 2:
            raise RuntimeError("SYNTHETIC failure mid-confirmation")
        return "added"

    async with _client(_app(), world["account_a"]) as ac:
        imp = await _stage(ac, _pdf((x, "01", "11111"), (y, "01", "33333")))
        monkeypatch.setattr(service, "_write_section", boom)
        failed = await ac.post(f"{URL}/{imp['id']}/confirm",
                               json={"expected_version": imp["version"]})
        assert failed.status_code == 500 and len(calls) == 2
    assert _record(world["a"], two_courses["X_id"]) is None
    assert _count(StudentRegistration, world["a"]) == 0
    with _session() as s:
        assert s.get(ScheduleImport, uuid.UUID(imp["id"])).state == "extracted"
