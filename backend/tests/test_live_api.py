"""CoursePilot Live API (Phase 6.9) - ownership, tokens, inbox, health. Real PostgreSQL.

Reuses the Phase 6.0 multi-program world (two linked accounts) and the 6.8
watch-API seeding (one SYNTHETIC section in a term of its own per test).
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import UTC, datetime

import pytest

from app.models import UserAccount
from app.models.live import DeliveryEndpoint
from app.services.live import fan_out

from .test_multi_program import _client, _session, requires_db, world  # noqa: F401
from .test_watch_api import _opening, _seed_section

RUN = uuid.uuid4().hex


def _token(n) -> str:
    return hashlib.sha256(f"api-{RUN}-{n}".encode()).hexdigest()


async def _watch(ac, term, index):
    r = await ac.post("/api/v1/watches", json={"term": term, "index_number": index})
    assert r.status_code == 200
    return r.json()["watch"]["watch_id"]


@requires_db
async def test_inbox_lists_counts_and_marks_read(world) -> None:  # noqa: F811
    from app.main import create_app

    term, index, section_id, course = _seed_section()
    async with _client(create_app(), world["account_a"]) as ac:
        await _watch(ac, term, index)
        _opening(term, index, section_id, datetime.now(UTC))
        page = (await ac.get("/api/v1/notifications")).json()
        [item] = page["notifications"]
        assert item["read"] is False and item["event"]["course_string"] == course
        assert item["event"]["kind"] == "section_opened"
        assert (await ac.get("/api/v1/notifications/unread-count")).json() == {"unread": 1}
        read = await ac.post(f"/api/v1/notifications/{item['notification_id']}/read")
        assert read.status_code == 200 and read.json()["read"] is True
        assert (await ac.get("/api/v1/notifications/unread-count")).json() == {"unread": 0}
        assert (await ac.post("/api/v1/notifications/read-all", json={})).json() == {"marked": 0}
        assert (await ac.get("/api/v1/notifications", params={"cursor": "%%%"})).status_code \
            == 422


@requires_db
async def test_endpoints_never_echo_tokens_and_rotate(world, caplog) -> None:  # noqa: F811
    from app.main import create_app

    caplog.set_level(logging.INFO)
    token = _token("device")
    async with _client(create_app(), world["account_a"]) as ac:
        r = await ac.post("/api/v1/push-endpoints",
                          json={"environment": "sandbox", "token": token})
        assert r.status_code == 200 and token not in r.text
        body = r.json()
        assert body["kind"] == "apns_device" and len(body["fingerprint"]) == 12
        rotated = await ac.post("/api/v1/push-endpoints", json={
            "environment": "sandbox", "token": _token("device2"),
            "replaces_endpoint_id": body["endpoint_id"]})
        listed = (await ac.get("/api/v1/push-endpoints")).json()["endpoints"]
        assert [e["endpoint_id"] for e in listed] == [rotated.json()["endpoint_id"]]
        gone = await ac.delete(f"/api/v1/push-endpoints/{rotated.json()['endpoint_id']}")
        assert gone.status_code == 200
        assert (await ac.get("/api/v1/push-endpoints")).json()["endpoints"] == []
    logged = "\n".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    assert token not in logged and _token("device2") not in logged


@requires_db
async def test_one_account_cannot_touch_anothers(world) -> None:  # noqa: F811
    from app.main import create_app

    term, index, section_id, _course = _seed_section()
    app = create_app()
    async with _client(app, world["account_a"]) as a:
        watch_id = await _watch(a, term, index)
        endpoint = (await a.post("/api/v1/push-endpoints", json={
            "environment": "sandbox", "token": _token("a-dev")})).json()["endpoint_id"]
        activity = (await a.post("/api/v1/live-activities", json={
            "watch_id": watch_id, "client_activity_id": "a-1", "push_token": _token("a-la"),
            "environment": "sandbox"})).json()["live_activity_id"]
        _opening(term, index, section_id, datetime.now(UTC))
        note = (await a.get("/api/v1/notifications")).json()["notifications"][0][
            "notification_id"]
    async with _client(app, world["account_b"]) as b:
        assert (await b.get("/api/v1/push-endpoints")).json()["endpoints"] == []
        assert (await b.get("/api/v1/live-activities")).json()["live_activities"] == []
        assert (await b.get("/api/v1/notifications")).json()["notifications"] == []
        assert (await b.delete(f"/api/v1/push-endpoints/{endpoint}")).status_code == 404
        assert (await b.post(f"/api/v1/notifications/{note}/read")).status_code == 404
        assert (await b.post(f"/api/v1/live-activities/{activity}/end")).status_code == 404
        assert (await b.put(f"/api/v1/live-activities/{activity}/token", json={
            "push_token": _token("b"), "environment": "sandbox"})).status_code == 404
        hijack = await b.post("/api/v1/live-activities", json={
            "watch_id": watch_id, "client_activity_id": "b-1", "push_token": _token("b-la"),
            "environment": "sandbox"})
        assert hijack.status_code == 404                     # same answer as a random watch
        assert (await b.delete(f"/api/v1/push-endpoints/{uuid.uuid4()}")).status_code == 404
    async with _client(app, world["account_a"]) as a:
        assert len((await a.get("/api/v1/push-endpoints")).json()["endpoints"]) == 2
        assert (await a.get("/api/v1/notifications/unread-count")).json() == {"unread": 1}


@requires_db
@pytest.mark.parametrize("field", ["account_id", "user_account_id", "student_id", "user_id",
                                   "principal", "owner"])
async def test_owner_fields_are_rejected_everywhere(world, field) -> None:  # noqa: F811
    from app.main import create_app

    async with _client(create_app(), world["account_a"]) as ac:
        value = str(world["account_b"])
        bodies = [
            ("/api/v1/push-endpoints", {"environment": "sandbox", "token": _token("x"),
                                        field: value}),
            ("/api/v1/live-activities", {"watch_id": str(uuid.uuid4()),
                                         "client_activity_id": "x", "push_token": _token("y"),
                                         "environment": "sandbox", field: value}),
            ("/api/v1/notifications/read-all", {field: value}),
        ]
        for url, body in bodies:
            r = await ac.post(url, json=body)
            assert r.status_code == 422, (url, r.status_code)


async def test_live_routes_require_authentication() -> None:
    from httpx import ASGITransport, AsyncClient

    from app.main import create_app

    async with AsyncClient(transport=ASGITransport(app=create_app()),
                           base_url="http://test") as ac:
        for method, url in [("GET", "/api/v1/notifications"),
                            ("GET", "/api/v1/notifications/unread-count"),
                            ("POST", "/api/v1/notifications/read-all"),
                            ("GET", "/api/v1/push-endpoints"),
                            ("POST", "/api/v1/push-endpoints"),
                            ("GET", "/api/v1/live-activities"),
                            ("GET", "/api/v1/admin/live/health")]:
            assert (await ac.request(method, url)).status_code == 401, url


@requires_db
async def test_registration_is_rate_limited(world) -> None:  # noqa: F811
    from app.main import create_app

    async with _client(create_app(), world["account_a"]) as ac:
        codes = [(await ac.post("/api/v1/push-endpoints", json={
            "environment": "sandbox", "token": _token(f"burst-{i}")})).status_code
            for i in range(25)]
    assert 429 in codes and codes[0] == 200


@requires_db
async def test_live_health_is_for_administrators(world) -> None:  # noqa: F811
    from app.main import create_app

    with _session() as s:
        admin = UserAccount(identity_provider="oidc", external_subject=f"adm-{uuid.uuid4()}",
                            is_admin=True)
        s.add(admin)
        s.commit()
        admin_id = admin.id
    app = create_app()
    async with _client(app, world["account_a"]) as ac:
        assert (await ac.get("/api/v1/admin/live/health")).status_code == 403
    async with _client(app, admin_id) as ac:
        r = await ac.get("/api/v1/admin/live/health")
    assert r.status_code == 200
    body = r.json()
    assert {"terms", "scheduler_healthy", "pending_openings", "pending_fanout", "deliveries",
            "endpoints_invalidated", "active_live_activities"} <= set(body)
    assert "token" not in r.text


@requires_db
async def test_a_live_activity_registration_creates_the_delivery_path(world) -> None:  # noqa: F811
    from app.main import create_app

    term, index, section_id, _course = _seed_section()
    async with _client(create_app(), world["account_a"]) as ac:
        watch_id = await _watch(ac, term, index)
        r = await ac.post("/api/v1/live-activities", json={
            "watch_id": watch_id, "client_activity_id": f"act-{RUN}",
            "push_token": _token("live"), "environment": "sandbox"})
        assert r.status_code == 200 and _token("live") not in r.text
        activity = r.json()
        assert activity["state"] == "active" and activity["watch_id"] == watch_id
        _opening(term, index, section_id, datetime.now(UTC))
        with _session() as s:
            fan_out(s, now=datetime.now(UTC))
            endpoint = s.get(DeliveryEndpoint, s.scalar(
                __import__("sqlalchemy").select(DeliveryEndpoint.id).where(
                    DeliveryEndpoint.token_fingerprint == activity["token_fingerprint"])))
            assert endpoint.kind == "apns_live_activity"
        ended = await ac.post(f"/api/v1/live-activities/{activity['live_activity_id']}/end")
        assert ended.json()["state"] == "ended"
