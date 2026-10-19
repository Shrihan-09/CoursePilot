"""Phase 7.2.1: Planning Profile, planning readiness, My current schedule.

Real PostgreSQL, the Phase 6.0 multi-program world. Every student is
synthetic. Current-schedule tests add SYNTHETIC Rutgers sections under the
far-future term 20991 so no other test's data can be affected, and remove them.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text

from app.models import (
    Course,
    CourseOffering,
    CourseSection,
    DataSource,
    SectionMeeting,
    Student,
    StudentCourse,
    Subject,
    UserAccount,
)

from .test_multi_program import _client, _session, _snapshot, requires_db, world  # noqa: F401

PROFILE = "/api/v1/student/planning-profile"
READY = "/api/v1/student/planning-readiness"
CURRENT = "/api/v1/student/current-schedule"
SECTIONS = "/api/v1/student/current-schedule/sections"
TERM = "20991"


def _app():
    from app.main import create_app

    return create_app()


VALID = {"pace": "balanced", "target_credits_per_term": 16, "max_credits_per_term": 18,
         "max_courses_per_term": 6, "include_summer": False, "include_winter": True,
         "schedule": {"earliest_start": "10:00", "latest_end": None, "avoid_days": ["F"]},
         "additional_goals": [{"kind": "minor", "label": "Mathematics minor"}]}


# --------------------------------------------------------------------------
# Planning Profile
# --------------------------------------------------------------------------


@requires_db
async def test_profile_is_null_until_confirmed_then_versioned(world) -> None:  # noqa: F811
    async with _client(_app(), world["account_a"]) as ac:
        empty = (await ac.get(PROFILE)).json()
        first = await ac.put(PROFILE, json=VALID)
        second = await ac.put(PROFILE, json={**VALID, "pace": "finish_earlier"})
        now = (await ac.get(PROFILE)).json()
    assert empty["profile"] is None
    assert empty["defaults"]["pace"] == "balanced"                 # BALANCED by default
    assert "not Rutgers credit-load policy" in empty["disclaimer"]
    assert [p["value"] for p in empty["paces"]] == ["balanced", "lighter", "finish_earlier"]
    assert first.status_code == 200 and first.json()["version"] == 1
    assert second.json()["version"] == 2
    assert now["profile"]["pace"] == "finish_earlier"
    assert now["profile"]["target_credits_per_term"] == "16"
    assert now["profile"]["additional_goals"][0]["status"] == "exploring"
    assert now["profile"]["effective_max_terms"] == 8


@requires_db
async def test_profile_belongs_to_its_owner(world) -> None:  # noqa: F811
    app = _app()
    async with _client(app, world["account_a"]) as ac:
        await ac.put(PROFILE, json=VALID)
    async with _client(app, world["account_b"]) as ac:
        assert (await ac.get(PROFILE)).json()["profile"] is None


@requires_db
@pytest.mark.parametrize("bad", [
    {"target_credits_per_term": 19, "max_credits_per_term": 18},       # target above max
    {"max_credits_per_term": 30},
    {"pace": "fastest"},
    {"student_id": "x"},
    {"completed_courses": ["01:198:111"]},                            # academic truth: never here
    {"schedule": {"earliest_start": "17:00", "latest_end": "09:00"}},
    {"additional_goals": [{"kind": "second_major", "label": "", "status": "exploring"}]},
    {"additional_goals": [{"kind": "second_major", "label": "Math", "status": "declared"}]},
])
async def test_profile_validation(world, bad) -> None:  # noqa: F811
    async with _client(_app(), world["account_a"]) as ac:
        response = await ac.put(PROFILE, json={**VALID, **bad})
        assert response.status_code == 422, bad
        assert (await ac.get(PROFILE)).json()["profile"] is None


@requires_db
async def test_confirming_preferences_never_touches_academic_data(world) -> None:  # noqa: F811
    with _session() as session:
        before = _snapshot(session)
    async with _client(_app(), world["account_a"]) as ac:
        assert (await ac.put(PROFILE, json=VALID)).status_code == 200
    with _session() as session:
        assert _snapshot(session) == before


# --------------------------------------------------------------------------
# Readiness
# --------------------------------------------------------------------------


@requires_db
async def test_readiness_states_in_order(world) -> None:  # noqa: F811
    with _session() as session:
        orphan = UserAccount(identity_provider="oidc", external_subject=f"u-{uuid.uuid4()}")
        session.add(orphan)
        session.commit()
        orphan_id = orphan.id
    app = _app()
    async with _client(app, orphan_id) as ac:
        assert (await ac.get(READY)).json()["state"] == "needs_program"
    async with _client(app, world["account_a"]) as ac:
        unconfirmed = (await ac.get(READY)).json()
        with _session() as session:
            session.get(Student, world["a"]).onboarding_completed_at = dt.datetime.now(dt.UTC)
            session.commit()
        no_profile = (await ac.get(READY)).json()
        await ac.put(PROFILE, json=VALID)
        ready = (await ac.get(READY)).json()
    assert unconfirmed["state"] == "needs_academic_history"
    assert no_profile["state"] == "needs_planning_profile"
    assert ready["state"] in ("ready", "ready_with_warnings")
    codes = {c["code"]: c["state"] for c in ready["checks"]}
    assert codes["academic_history"] == codes["planning_profile"] == "done"
    assert codes["additional_goals"] == "info"                        # intent, not planned
    assert any("not included in the plan" in c["message"] for c in ready["checks"])
    assert all(c["state"] != "missing" for c in ready["checks"])


@requires_db
async def test_readiness_mentions_planned_entries_and_is_read_only(world) -> None:  # noqa: F811
    with _session() as session:
        course = session.scalar(select(Course).where(Course.course_string == world["courses"]["Z"]))
        session.add(StudentCourse(student_id=world["a"], course_id=course.id, term_code="20279",
                                  status="planned"))
        session.commit()
        before = _snapshot(session)
    async with _client(_app(), world["account_a"]) as ac:
        body = (await ac.get(READY)).json()
    assert "planned_entries" in {c["code"] for c in body["checks"]}
    with _session() as session:
        assert _snapshot(session) == before


async def test_new_endpoints_require_authentication() -> None:
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://test") as ac:
        for method, url in (("get", PROFILE), ("get", READY), ("get", CURRENT),
                            ("get", f"{SECTIONS}?course=01:198:111")):
            assert (await getattr(ac, method)(url)).status_code == 401
        assert (await ac.put(PROFILE, json=VALID)).status_code == 401


# --------------------------------------------------------------------------
# My current schedule - never inferred
# --------------------------------------------------------------------------


@pytest.fixture
def sections(world):  # noqa: F811
    """Student A takes a course in TERM; Rutgers lists two sections of it.

    The course has a real-format code (01:999:NNN): course codes on these
    endpoints are validated like the Schedule Engine's (UU:SSS:CCC)."""
    with _session() as session:
        manual = DataSource(kind="manual_curation", url="synthetic://cs-course",
                            content_hash=uuid.uuid4().hex, retrieved_at=dt.datetime.now(dt.UTC),
                            version=1)
        session.add(manual)
        session.flush()
        subject = session.scalar(select(Subject).where(Subject.offering_unit_code == "01",
                                                       Subject.code == "999"))
        if subject is None:
            subject = Subject(code="999", offering_unit_code="01", description="Synthetic",
                              source_id=manual.id)
            session.add(subject)
            session.flush()
        taken = set(session.scalars(select(Course.course_number).where(Course.subject_code == "999")))
        number = next(f"{n}" for n in range(100, 1000) if f"{n}" not in taken)
        course = Course(offering_unit_code="01", subject_code="999", course_number=number,
                        supplement_code="", course_string=f"01:999:{number}", title="Synthetic",
                        credits=3, subject_id=subject.id, source_id=manual.id)
        session.add(course)
        session.flush()
        course_id, course_string = course.id, course.course_string
        source = DataSource(kind="rutgers_official_api", url="synthetic://soc-20991",
                            content_hash=uuid.uuid4().hex, term_code=TERM, coverage="complete",
                            retrieved_at=dt.datetime.now(dt.UTC), version=1)
        session.add(source)
        session.flush()
        offering = CourseOffering(course_id=course.id, term_code=TERM, campus_code="NB",
                                  source_id=source.id)
        session.add(offering)
        session.flush()
        ids = []
        for index, number, day, start, end in (("11111", "01", "M", "1020", "1140"),
                                               ("22222", "02", "T", "1400", "1520")):
            sec = CourseSection(offering_id=offering.id, term_code=TERM, index_number=index,
                                section_number=number, campus_code="NB", open_status=True,
                                source_id=source.id)
            session.add(sec)
            session.flush()
            session.add(SectionMeeting(section_id=sec.id, meeting_index=0, meeting_day=day,
                                       start_time_military=start, end_time_military=end,
                                       meeting_mode_code="02", meeting_mode_desc="LEC",
                                       building_code="HLL", room_number="114",
                                       campus_abbrev="BUS"))
            ids.append(sec.id)
        session.add(StudentCourse(student_id=world["a"], course_id=course.id, term_code=TERM,
                                  status="in_progress"))
        session.commit()
        source_id = source.id
    yield {"course": course_string, "ids": ids}
    with _session() as session:
        session.execute(text("DELETE FROM student_current_section WHERE term_code = :t"), {"t": TERM})
        session.execute(text("DELETE FROM student_course WHERE term_code = :t"), {"t": TERM})
        session.execute(text("DELETE FROM section_meeting WHERE section_id IN "
                             "(SELECT id FROM course_section WHERE term_code = :t)"), {"t": TERM})
        session.execute(text("DELETE FROM course_section WHERE term_code = :t"), {"t": TERM})
        session.execute(text("DELETE FROM course_offering WHERE term_code = :t"), {"t": TERM})
        session.execute(text("DELETE FROM data_source WHERE id = :s"), {"s": source_id})
        session.execute(text("DELETE FROM course WHERE id = :c"), {"c": course_id})
        session.commit()


@requires_db
async def test_no_in_progress_courses_means_no_current_schedule(world) -> None:  # noqa: F811
    async with _client(_app(), world["account_a"]) as ac:
        body = (await ac.get(CURRENT)).json()
    assert body["term_code"] is None and body["courses"] == []


@requires_db
async def test_a_course_on_the_record_is_not_a_section(world, sections) -> None:  # noqa: F811
    """Two sections exist; until the student confirms one, NONE is shown."""
    async with _client(_app(), world["account_a"]) as ac:
        body = (await ac.get(CURRENT)).json()
        listed = (await ac.get(SECTIONS, params={"course": sections["course"]})).json()
    assert body["term_code"] == TERM and body["term_published"] is True
    [course] = body["courses"]
    assert course["course"] == sections["course"]
    assert course["confirmed"] == [] and course["sections_available"] == 2
    assert [s["index_number"] for s in listed["sections"]] == ["11111", "22222"]
    assert listed["transcript_section"] is None


@requires_db
async def test_the_student_confirms_their_exact_section(world, sections) -> None:  # noqa: F811
    async with _client(_app(), world["account_a"]) as ac:
        confirmed = await ac.put(SECTIONS, json={"course": sections["course"], "index_number": "22222"})
        wrong = await ac.put(SECTIONS, json={"course": sections["course"], "index_number": "33333"})
        not_mine = await ac.put(SECTIONS, json={"course": "01:198:111", "index_number": "11111"})
        extra = await ac.put(SECTIONS, json={"course": sections["course"], "index_number": "11111",
                                             "student_id": "x"})
    assert confirmed.status_code == 200
    [course] = confirmed.json()["courses"]
    [choice] = course["confirmed"]
    assert choice["index_number"] == "22222" and choice["section_number"] == "02"
    assert choice["meetings"][0]["day"] == "T" and choice["meetings"][0]["start_minute"] == 840
    assert wrong.status_code == 422                                  # never a guess
    assert not_mine.status_code == 404                               # not in progress
    assert extra.status_code == 422
    async with _client(_app(), world["account_b"]) as ac:
        assert (await ac.get(CURRENT)).json()["courses"] == []        # another account sees nothing


@requires_db
async def test_a_removed_section_is_reported_never_retargeted(world, sections) -> None:  # noqa: F811
    async with _client(_app(), world["account_a"]) as ac:
        await ac.put(SECTIONS, json={"course": sections["course"], "index_number": "11111"})
    with _session() as session:
        session.execute(text("DELETE FROM section_meeting WHERE section_id = :s"), {"s": sections["ids"][0]})
        session.execute(text("DELETE FROM course_section WHERE id = :s"), {"s": sections["ids"][0]})
        session.commit()
    async with _client(_app(), world["account_a"]) as ac:
        [course] = (await ac.get(CURRENT)).json()["courses"]
    assert course["confirmed"] == []
    assert course["unavailable"] == [{"index_number": "11111", "component": "primary"}]


@requires_db
async def test_forgetting_a_confirmation(world, sections) -> None:  # noqa: F811
    async with _client(_app(), world["account_a"]) as ac:
        await ac.put(SECTIONS, json={"course": sections["course"], "index_number": "11111"})
        gone = await ac.delete(SECTIONS, params={"course": sections["course"]})
        [course] = (await ac.get(CURRENT)).json()["courses"]
    assert gone.status_code == 204 and course["confirmed"] == []
