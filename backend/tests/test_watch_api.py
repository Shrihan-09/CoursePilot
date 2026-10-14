"""/api/v1/watches (Phase 6.8) - ownership, validation, structure, read-only reads.

Reuses the Phase 6.0 multi-program world (two linked accounts, real
PostgreSQL). Its synthetic courses have no sections, so each test seeds
ONE SYNTHETIC section in a term of its own (a random far-future term, so
runs never collide); monitoring semantics on real Rutgers sections are
tested in ingestion/tests/test_section_monitoring.py.
"""

from __future__ import annotations

import logging
import random
import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from app.models import (
    Course,
    CourseOffering,
    CourseSection,
    DataSource,
    Subject,
)
from app.models.availability import AvailabilityRefresh, SectionAvailabilityChange
from app.models.monitoring import AvailabilityChangeDispatch, NotificationEvent, SectionWatch
from app.services.monitoring import process_openings

from .test_multi_program import _client, _session, _snapshot, requires_db, world  # noqa: F401

URL = "/api/v1/watches"


def _seed_section(open_status=False):
    """(term, index, section_id) of one SYNTHETIC section, its own term."""
    term = f"{random.randint(3000, 9999)}9"
    tag = uuid.uuid4().hex[:6]
    with _session() as s:
        src = DataSource(kind="rutgers_official_api", retrieved_at=datetime.now(UTC),
                         content_hash=f"watch-api-{tag}", term_code=term, coverage="complete")
        s.add(src)
        s.flush()
        subject = Subject(code=f"w{tag}", offering_unit_code="01", source_id=src.id)
        s.add(subject)
        s.flush()
        course = Course(offering_unit_code="01", subject_code=f"w{tag}", course_number="101",
                        course_string=f"01:w{tag}:101", title="WATCH API TEST",
                        subject_id=subject.id, source_id=src.id)
        s.add(course)
        s.flush()
        offering = CourseOffering(course_id=course.id, term_code=term, campus_code="NB",
                                  source_id=src.id)
        s.add(offering)
        s.flush()
        section = CourseSection(offering_id=offering.id, term_code=term, index_number="12345",
                                section_number="03", campus_code="NB", open_status=open_status,
                                source_id=src.id)
        s.add(section)
        s.commit()
        return term, "12345", section.id, course.course_string


def _opening(term, index, section_id, when):
    """Record a SYNTHETIC closed -> open change for the seeded section."""
    with _session() as s:
        refresh = AvailabilityRefresh(term_code=term, source_url="synthetic://watch-api",
                                      observed_at=when, verdict="accepted")
        s.add(refresh)
        s.flush()
        s.add(SectionAvailabilityChange(section_id=section_id, term_code=term,
                                        index_number=index, from_status="closed",
                                        to_status="open", observed_at=when,
                                        refresh_id=refresh.id))
        s.commit()
        process_openings(s, now=datetime.now(UTC))


def _monitoring_rows(session):
    """Every watch / event / dispatch row, in a stable order."""
    out = {}
    for model in (SectionWatch, NotificationEvent, AvailabilityChangeDispatch):
        table = model.__table__
        out[table.name] = sorted(repr(tuple(r)) for r in session.execute(select(table)))
    return out


@requires_db
async def test_create_list_deactivate_and_events_are_structured(world) -> None:  # noqa: F811
    from app.main import create_app

    term, index, section_id, course = _seed_section()
    async with _client(create_app(), world["account_a"]) as ac:
        created = await ac.post(URL, json={"term": term, "index_number": index})
        again = await ac.post(URL, json={"term": term, "index_number": index})
        assert created.status_code == again.status_code == 200
        body, repeat = created.json(), again.json()
        assert body["created"] is True and repeat["created"] is False
        watch = body["watch"]
        assert repeat["watch"]["watch_id"] == watch["watch_id"]
        assert {k: watch[k] for k in ("status", "term_code", "index_number", "course_string",
                                      "section_number", "notifies_on", "target_removed")} == {
            "status": "active", "term_code": term, "index_number": index,
            "course_string": course, "section_number": "03", "notifies_on": "closed_to_open",
            "target_removed": False}
        assert watch["current_availability"]["freshness"] in ("stale", "unobserved")

        _opening(term, index, section_id, datetime.now(UTC))
        events = (await ac.get(f"{URL}/events")).json()["events"]
        [event] = events
        assert {k: event[k] for k in ("kind", "watch_id", "term_code", "index_number",
                                      "course_string", "section_number", "from_status",
                                      "to_status", "required_component")} == {
            "kind": "section_opened", "watch_id": watch["watch_id"], "term_code": term,
            "index_number": index, "course_string": course, "section_number": "03",
            "from_status": "closed", "to_status": "open", "required_component": None}
        assert event["observed_at"] and event["event_id"]

        listed = (await ac.get(URL)).json()["watches"]
        assert [w["watch_id"] for w in listed] == [watch["watch_id"]]
        gone = await ac.delete(f"{URL}/{watch['watch_id']}")
        assert gone.status_code == 200 and gone.json()["status"] == "inactive"
        assert gone.json()["deactivation_reason"] == "user_cancelled"
        assert (await ac.delete(f"{URL}/{watch['watch_id']}")).status_code == 200  # idempotent
        assert (await ac.get(URL)).json()["watches"] == []
        history = (await ac.get(URL, params={"include_inactive": "true"})).json()["watches"]
        assert [w["status"] for w in history] == ["inactive"]


@requires_db
async def test_one_account_cannot_see_or_touch_anothers(world) -> None:  # noqa: F811
    from app.main import create_app

    term, index, section_id, _course = _seed_section()
    app = create_app()
    async with _client(app, world["account_a"]) as a:
        watch_id = (await a.post(URL, json={"term": term, "index_number": index})
                    ).json()["watch"]["watch_id"]
    _opening(term, index, section_id, datetime.now(UTC))
    async with _client(app, world["account_b"]) as b:
        assert (await b.get(URL, params={"include_inactive": "true"})).json()["watches"] == []
        assert (await b.get(f"{URL}/events")).json()["events"] == []
        assert (await b.delete(f"{URL}/{watch_id}")).status_code == 404
        assert (await b.delete(f"{URL}/{uuid.uuid4()}")).status_code == 404  # same answer
    with _session() as s:
        watch = s.get(SectionWatch, uuid.UUID(watch_id))
        assert watch.deactivated_at is None                    # B changed nothing
        assert s.scalar(select(NotificationEvent).where(
            NotificationEvent.watch_id == watch.id)).user_account_id == world["account_a"]


@requires_db
async def test_validation_and_unknown_targets(world) -> None:  # noqa: F811
    from app.main import create_app

    term, index, _sid, _course = _seed_section()
    async with _client(create_app(), world["account_a"]) as ac:
        bad = {
            "term": await ac.post(URL, json={"term": "20265", "index_number": index}),
            "index": await ac.post(URL, json={"term": term, "index_number": "1234"}),
            "owner": await ac.post(URL, json={"term": term, "index_number": index,
                                              "account_id": str(world["account_b"])}),
            "student": await ac.post(URL, json={"term": term, "index_number": index,
                                                "student_id": str(world["b"])}),
            "limit": await ac.get(f"{URL}/events", params={"limit": 1000}),
            "watch_id": await ac.delete(f"{URL}/not-a-uuid"),
        }
        missing = await ac.post(URL, json={"term": term, "index_number": "54321"})
    assert {k: r.status_code for k, r in bad.items()} == {k: 422 for k in bad}
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "not_found"
    assert "54321" in missing.json()["detail"]                 # the caller's own input only


async def test_watches_require_authentication() -> None:
    from httpx import ASGITransport, AsyncClient

    from app.main import create_app

    async with AsyncClient(transport=ASGITransport(app=create_app()),
                           base_url="http://test") as ac:
        assert (await ac.get(URL)).status_code == 401
        assert (await ac.post(URL, json={"term": "20269", "index_number": "13385"})
                ).status_code == 401


@requires_db
async def test_reads_write_nothing_and_logs_carry_no_target(world, caplog) -> None:  # noqa: F811
    from app.main import create_app

    term, index, section_id, course = _seed_section()
    async with _client(create_app(), world["account_a"]) as ac:
        await ac.post(URL, json={"term": term, "index_number": index})
        _opening(term, index, section_id, datetime.now(UTC))
        with _session() as s:
            before = (_snapshot(s), _monitoring_rows(s))
        caplog.set_level(logging.INFO)
        await ac.get(URL)
        await ac.get(URL, params={"include_inactive": "true"})
        await ac.get(f"{URL}/events")
        with _session() as s:
            assert (_snapshot(s), _monitoring_rows(s)) == before
    logged = "\n".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records
                       if r.name.startswith("app."))
    assert index not in logged and course not in logged and term not in logged
