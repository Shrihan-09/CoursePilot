"""Prompt 11 Part B: the assistant API is MY assistant (real PostgreSQL, fake provider).

    GET    /api/v1/student/assistant
    POST   /api/v1/student/assistant/messages      (NDJSON)
    POST   /api/v1/student/assistant/actions
    DELETE /api/v1/student/assistant/history
    GET    /api/v1/student/plans/current/terms/{term}/webreg
    PUT    /api/v1/student/plans/current/terms/{term}/sections

Reuses the Phase 6.0 multi-program world (synthetic programs, plans with no
placed courses): ownership, tokens, caps and deletion are what is tested
here; the assistant's behaviour is tested in test_assistant.py.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest
from sqlalchemy import func, select

from app.models import Student
from app.models.assistant import AssistantAction, AssistantMessage, AssistantUsage

from .test_multi_program import _client, _session, requires_db, world  # noqa: F401

BASE = "/api/v1/student/assistant"


@pytest.mark.parametrize(("message", "title"), [
    ("  Help me pick something instead of Art History please ", "Help me pick something instead of"),
    ("Hello\n  there", "Hello there"), ("   ", "New chat"), ("x" * 200, "x" * 160),
])
def test_conversation_titles_are_deterministic(message, title):
    from app.services.assistant.service import conversation_title
    assert conversation_title(message) == title


@requires_db
async def test_conversation_crud_is_scoped_and_history_is_separate(world):
    from app.models.assistant import AssistantConversation
    from app.services.assistant import service
    app = _app()
    async with _client(app, world["account_a"]) as ac:
        first = (await ac.post(f"{BASE}/conversations")).json()
        second = (await ac.post(f"{BASE}/conversations")).json()
        await _ask(ac, conversation_id=first["id"], text="Write my essay about a topic please")
        await _ask(ac, conversation_id=second["id"], text="Register me in WebReg")
        mine = (await ac.get(BASE, params={"conversation_id": first["id"]})).json()
        assert mine["history"][0]["content"] == "Write my essay about a topic please"
        assert len(mine["history"]) == 2
        assert next(c for c in mine["conversations"] if c["id"] == first["id"])["title"] == "Write my essay about a topic"
        renamed = await ac.patch(f"{BASE}/conversations/{first['id']}", json={"title": "My spring choices", "archived": True})
        assert renamed.status_code == 200 and renamed.json()["archived"] is True
        assert (await ac.patch(f"{BASE}/conversations/{first['id']}", json={"title": "  "})).status_code == 422
    with _session() as session:
        import uuid
        service._mint(session, session.get(Student, world["a"]), {"kind": "confirm_change"},
                      uuid.UUID(first["id"]))
        session.commit()
    async with _client(app, world["account_b"]) as other:
        assert (await other.get(BASE, params={"conversation_id": first["id"]})).status_code == 404
        assert (await other.patch(f"{BASE}/conversations/{first['id']}", json={"title": "Stolen"})).status_code == 404
        assert (await other.delete(f"{BASE}/conversations/{first['id']}")).status_code == 404
        assert (await other.post(f"{BASE}/messages", json={"conversation_id": first["id"], "text": "Hello"})).status_code == 404
        assert (await other.get(f"{BASE}/conversations")).json()["conversations"] == []
    async with _client(app, world["account_a"]) as ac:
        assert (await ac.delete(f"{BASE}/conversations/{first['id']}")).status_code == 200
        assert (await ac.get(BASE, params={"conversation_id": first["id"]})).status_code == 404
        remaining = (await ac.get(BASE, params={"conversation_id": second["id"]})).json()
        assert len(remaining["history"]) == 2
        assert remaining["history"][0]["content"] == "Register me in WebReg"
    with _session() as session:
        assert session.get(AssistantConversation, first["id"]) is None
        assert session.scalar(select(func.count()).select_from(AssistantMessage).where(
            AssistantMessage.conversation_id == first["id"])) == 0
        assert session.scalar(select(func.count()).select_from(AssistantAction).where(
            AssistantAction.conversation_id == first["id"])) == 0


@requires_db
def test_conversation_migration_preserves_legacy_messages_and_downgrades(world):
    import importlib
    import uuid
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import text
    migration = importlib.import_module("app.db.migrations.versions.20261109_p11b02_assistant_conversations")
    with _session() as session:
        with Operations.context(MigrationContext.configure(session.connection())):
            migration.downgrade()
            ids = [uuid.uuid4(), uuid.uuid4()]
            for i, cid in enumerate(ids):
                session.execute(text("INSERT INTO assistant_conversation (id,student_id) VALUES (:id,:student)"),
                                {"id": cid, "student": world["a"]})
                session.execute(text("INSERT INTO assistant_message (id,conversation_id,student_id,role,content,payload) "
                                     "VALUES (:id,:cid,:student,'user',:content,'{}')"),
                                {"id": uuid.uuid4(), "cid": cid, "student": world["a"],
                                 "content": "Help me pick an online course" if i == 0 else "Another message"})
            before = session.scalar(text("SELECT count(*) FROM assistant_message WHERE student_id=:id"), {"id": world["a"]})
            migration.upgrade()
            assert session.scalar(text("SELECT count(*) FROM assistant_message WHERE student_id=:id"), {"id": world["a"]}) == before
            assert session.scalar(text("SELECT count(DISTINCT conversation_id) FROM assistant_message WHERE student_id=:id"), {"id": world["a"]}) == 1
            migration.downgrade()
            assert session.scalar(text("SELECT count(*) FROM assistant_message WHERE student_id=:id"), {"id": world["a"]}) == before
            session.rollback()  # Entire exercise is isolated to the *_test DB and rolled back.


def _app():
    from app.main import create_app

    return create_app()


@pytest.fixture(autouse=True)
def _ready(request):
    if "world" not in request.fixturenames:
        return
    world = request.getfixturevalue("world")
    from app.domain.planning_profile import PlanningProfileInput
    from app.services.planning.profile import confirm_profile

    now = dt.datetime.now(dt.UTC)
    with _session() as session:
        for key in ("a", "b"):
            student = session.get(Student, world[key])
            student.onboarding_completed_at = now
            confirm_profile(session, student, PlanningProfileInput(), now)
        session.commit()


def _events(body: str) -> list[dict]:
    return [json.loads(line) for line in body.splitlines() if line.strip()]


async def _ask(ac, **body) -> list[dict]:
    r = await ac.post(f"{BASE}/messages", json=body)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/x-ndjson")
    return _events(r.text)


@requires_db
async def test_a_turn_streams_activity_then_a_validated_reply(world) -> None:  # noqa: F811
    async with _client(_app(), world["account_a"]) as ac:
        status = await ac.get(BASE)
        assert status.status_code == 200
        assert status.json()["enabled"] is True and status.json()["history"] == []
        assert "advisor and Degree Navigator are authoritative" in status.json()["footer"]
        await ac.post("/api/v1/student/plans", json={"start_term": "20261"})
        events = await _ask(ac, text="What do I still need?")
        assert [e["type"] for e in events][-1] == "reply"
        assert any(e["type"] == "activity" for e in events)
        reply = events[-1]["reply"]
        assert reply["text"] and reply["footer"].startswith("CoursePilot is a planning aid")
        assert events[-1]["remaining_today"] == 49
        refused = await _ask(ac, text="Register me in WebReg please")
        assert refused[-1]["reply"]["generated_by"] == "router"
        after = (await ac.get(BASE)).json()
    assert [m["role"] for m in after["history"]] == ["user", "assistant"] * 2
    with _session() as session:
        usage = session.scalar(select(AssistantUsage).where(
            AssistantUsage.student_id == world["a"]))
        assert usage.messages == 2 and usage.model_calls >= 1


@requires_db
async def test_another_students_history_and_tokens_are_unreachable(world) -> None:  # noqa: F811
    from app.services.assistant import service

    app = _app()
    async with _client(app, world["account_a"]) as ac:
        await ac.post("/api/v1/student/plans", json={"start_term": "20261"})
        await _ask(ac, text="What do I still need?")
    with _session() as session:
        a = session.get(Student, world["a"])
        token = service._mint(session, a, {"kind": "confirm_change", "preview_id": "nope",
                                           "change": {"kind": "lock", "course": "01:198:111",
                                                      "term_code": "20261"}})
        session.commit()
    async with _client(app, world["account_b"]) as ac:
        theirs = (await ac.get(BASE)).json()
        stolen = await ac.post(f"{BASE}/actions", json={"token": token})
        wiped = await ac.delete(f"{BASE}/history")
    assert theirs["history"] == []
    assert stolen.status_code == 409 and "expired" in stolen.json()["detail"]
    assert wiped.json() == {"deleted_messages": 0}
    async with _client(app, world["account_a"]) as ac:
        mine = await ac.post(f"{BASE}/actions", json={"token": token})
        again = await ac.post(f"{BASE}/actions", json={"token": token})
    assert mine.status_code == 200, mine.text
    assert mine.json()["reply"]["generated_by"] == "action"
    assert again.status_code == 409                        # single use
    with _session() as session:
        rows = session.scalars(select(AssistantAction).where(
            AssistantAction.student_id == world["a"])).all()
        assert rows and all(len(r.token_sha256) == 64 and r.token_sha256 != token
                            for r in rows)


@requires_db
async def test_the_daily_cap_is_enforced(world, monkeypatch) -> None:  # noqa: F811
    from app.core.config import get_settings

    monkeypatch.setenv("ASSISTANT_DAILY_MESSAGE_LIMIT", "1")
    get_settings.cache_clear()
    try:
        async with _client(_app(), world["account_a"]) as ac:
            first = await _ask(ac, text="Write my essay")
            second = await _ask(ac, text="Write my essay")
            status = (await ac.get(BASE)).json()
    finally:
        monkeypatch.delenv("ASSISTANT_DAILY_MESSAGE_LIMIT")
        get_settings.cache_clear()
    assert first[-1]["type"] == "reply"
    assert second[-1] == {"type": "error", "status": 429,
                          "detail": second[-1]["detail"]}
    assert "today's 1 assistant messages" in second[-1]["detail"]
    assert status["remaining_today"] == 0


@requires_db
async def test_chat_deletion_removes_everything(world) -> None:  # noqa: F811
    async with _client(_app(), world["account_a"]) as ac:
        await _ask(ac, text="Write my essay")
        await _ask(ac, text="What's the weather?")
        deleted = await ac.delete(f"{BASE}/history")
        after = (await ac.get(BASE)).json()
    assert deleted.json() == {"deleted_messages": 4}
    assert after["history"] == []
    with _session() as session:
        left = session.scalar(select(func.count()).select_from(AssistantMessage).where(
            AssistantMessage.student_id == world["a"]))
        assert left == 0


@requires_db
async def test_the_api_is_typed(world) -> None:  # noqa: F811
    async with _client(_app(), world["account_a"]) as ac:
        empty = await ac.post(f"{BASE}/messages", json={"text": "  "})
        extra = await ac.post(f"{BASE}/messages", json={"text": "hi",
                                                        "student_id": str(world["b"])})
        long = await ac.post(f"{BASE}/messages", json={"text": "x" * 601})
        bad_token = await ac.post(f"{BASE}/actions", json={"token": "short"})
        unknown = await ac.post(f"{BASE}/actions", json={"token": "x" * 40})
    assert empty.status_code == extra.status_code == long.status_code == 422
    assert bad_token.status_code == 422 and unknown.status_code == 409


@requires_db
async def test_ready_for_webreg_never_signs_in(world) -> None:  # noqa: F811
    async with _client(_app(), world["account_a"]) as ac:
        await ac.post("/api/v1/student/plans", json={"start_term": "20261"})
        panel = await ac.get("/api/v1/student/plans/current/terms/20261/webreg")
        missing = await ac.put("/api/v1/student/plans/current/terms/20261/sections",
                               json={"course": "01:198:111", "index_number": "99999"})
        bad = await ac.put("/api/v1/student/plans/current/terms/20261/sections",
                           json={"course": "01:198:111", "index_number": "1"})
    body = panel.json()
    assert panel.status_code == 200 and body["items"] == [] and body["index_numbers"] == []
    assert body["steps"][0] == "Open WebReg" and body["webreg_url"].startswith("https://")
    assert "does not sign in to WebReg" in body["note"]
    assert missing.status_code == 404 and bad.status_code == 422
