"""Prompt 11 Part A: the plan-change API is scoped to MY plan, and typed.

    GET  /api/v1/student/plans/current/courses/{course}/{term}/options
    POST /api/v1/student/plans/current/changes/preview
    POST /api/v1/student/plans/current/changes/apply
    POST /api/v1/student/plans/current/undo
    GET  /api/v1/student/plans/current/choices
    POST /api/v1/student/plans/current/search
    PUT  /api/v1/student/courses/{course}/{term}/doing-well
    GET|PUT /api/v1/student/plans/personalization

Reuses the Phase 6.0 multi-program world (real PostgreSQL): synthetic programs
whose courses have no offerings, so the saved plans place no courses. What is
tested here is ownership, status codes and validation; options, previews and
undo themselves are tested on real SOC data in
ingestion/tests/test_plan_options.py.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import select

from app.models import Student
from app.models.plans import PlanChangePreview

from .test_multi_program import _client, _session, requires_db, world  # noqa: F401

PLANS = "/api/v1/student/plans"
CHANGES = f"{PLANS}/current/changes"


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


@requires_db
async def test_without_a_saved_plan_nothing_can_be_changed(world) -> None:  # noqa: F811
    async with _client(_app(), world["account_a"]) as ac:
        options = await ac.get(f"{PLANS}/current/courses/01:198:111/20261/options")
        preview = await ac.post(f"{CHANGES}/preview", json={
            "kind": "lock", "course": "01:198:111", "term_code": "20261"})
        choices = await ac.get(f"{PLANS}/current/choices")
        search = await ac.post(f"{PLANS}/current/search", json={"text": "history"})
        undo = await ac.post(f"{PLANS}/current/undo")
    assert options.status_code == 404 and preview.status_code == 404
    assert choices.status_code == 404 and search.status_code == 404
    assert undo.status_code == 409


@requires_db
async def test_a_plan_change_reaches_only_my_own_plan(world) -> None:  # noqa: F811
    app = _app()
    async with _client(app, world["account_a"]) as ac:
        saved = await ac.post(PLANS, json={"start_term": "20261"})
        assert saved.status_code == 200, saved.text
        body = saved.json()
        assert body["revision"] >= 1 and body["edits"]["locks"] == []
        assert body["course_states"] == [] and body["last_change"] is None
        # A change the plan cannot honour is a 200 with reasons, never a write.
        refused = await ac.post(f"{CHANGES}/preview", json={
            "kind": "remove", "course": "01:198:111", "term_code": "20261"})
        assert refused.status_code == 200
        assert refused.json()["valid"] is False and refused.json()["preview_id"] == ""
        assert refused.json()["errors"][0].startswith("course_not_in_term")
        choose = await ac.post(f"{CHANGES}/preview", json={
            "kind": "choose_alternative", "requirement_code": "NOPE", "alternatives": ["X"]})
        assert choose.json()["valid"] is False
    with _session() as session:
        a = session.get(Student, world["a"])
        foreign = PlanChangePreview(
            student_id=a.id, base_revision=body["revision"], change={}, summary="",
            edits={}, program_version_id=a.program_version_id, catalog_year=a.catalog_year,
            constraints={}, planning_engine_version="x", audit_engine_version="x",
            academic_fingerprint="x", rules_fingerprint="x", offering_dataset=[],
            plan_status="partial", result_blob=b"",
            expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(minutes=5))
        session.add(foreign)
        session.commit()
        foreign_id = str(foreign.id)
    async with _client(app, world["account_b"]) as ac:
        # B has no plan: A's plan is not reachable through any route.
        options = await ac.get(f"{PLANS}/current/courses/01:198:111/20261/options")
        stolen = await ac.post(f"{CHANGES}/apply", json={"preview_id": foreign_id})
        mine = await ac.post(PLANS, json={"start_term": "20261"})
        stolen_again = await ac.post(f"{CHANGES}/apply", json={"preview_id": foreign_id})
    assert options.status_code == 404
    assert stolen.status_code == 404                      # B has no plan at all
    assert mine.status_code == 200
    assert stolen_again.status_code == 409                # A's preview is not B's
    assert "does not exist" in stolen_again.json()["detail"]
    with _session() as session:
        still = session.get(PlanChangePreview, foreign.id)
        assert still is not None and str(still.student_id) == str(world["a"])


@requires_db
async def test_the_change_api_is_typed(world) -> None:  # noqa: F811
    async with _client(_app(), world["account_a"]) as ac:
        await ac.post(PLANS, json={"start_term": "20261"})
        bad_course = await ac.get(f"{PLANS}/current/courses/CS111/20261/options")
        bad_term = await ac.get(f"{PLANS}/current/courses/01:198:111/2026/options")
        extra = await ac.post(f"{CHANGES}/preview", json={
            "kind": "lock", "course": "01:198:111", "term_code": "20261",
            "student_id": str(world["b"])})
        no_replacement = await ac.post(f"{CHANGES}/preview", json={
            "kind": "swap", "course": "01:198:111", "term_code": "20261"})
        unknown_kind = await ac.post(f"{CHANGES}/preview", json={
            "kind": "register_me", "course": "01:198:111", "term_code": "20261"})
        empty_apply = await ac.post(f"{CHANGES}/apply", json={"preview_id": "not-a-uuid"})
        search = await ac.post(f"{PLANS}/current/search", json={"text": "x" * 301})
    assert bad_course.status_code == 422 and bad_term.status_code == 422
    assert extra.status_code == 422 and no_replacement.status_code == 422
    assert unknown_kind.status_code == 422
    assert empty_apply.status_code == 409
    assert search.status_code == 422


@requires_db
async def test_personalization_toggle_and_doing_well_are_mine(world) -> None:  # noqa: F811
    async with _client(_app(), world["account_a"]) as ac:
        default = await ac.get(f"{PLANS}/personalization")
        off = await ac.put(f"{PLANS}/personalization",
                           json={"use_grades_for_suggestions": False})
        after = await ac.get(f"{PLANS}/personalization")
        not_in_progress = await ac.put("/api/v1/student/courses/01:198:111/20261/doing-well",
                                       json={"doing_well": True})
    async with _client(_app(), world["account_b"]) as ac:
        theirs = await ac.get(f"{PLANS}/personalization")
    assert default.json() == {"use_grades_for_suggestions": True}
    assert off.json() == after.json() == {"use_grades_for_suggestions": False}
    assert theirs.json() == {"use_grades_for_suggestions": True}
    assert not_in_progress.status_code == 404
    with _session() as session:
        rows = session.scalars(select(PlanChangePreview).where(
            PlanChangePreview.student_id.in_([world["a"], world["b"]]))).all()
        assert rows == []                                  # nothing here writes a preview
