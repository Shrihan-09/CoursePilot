"""Phase 7.2: the saved plan and the planning context.

    GET    /api/v1/student/plans/context
    POST   /api/v1/student/plans
    GET    /api/v1/student/plans/current
    DELETE /api/v1/student/plans/current

Reuses the Phase 6.0 multi-program world (real PostgreSQL): synthetic
programs whose courses have no offerings, so plans are honest PARTIAL plans.
What is tested here is ownership, persistence and freshness - the planning
itself is unchanged Phase 6.5 code with its own suites.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text

from app.domain.planning import (
    PlanMetadata,
    PlanningConstraints,
    PlanningIssue,
    PlanResult,
    PlanStatus,
    Severity,
)
from app.domain.scenario import ScenarioTarget
from app.models import (
    Course,
    CourseOffering,
    CoursePrerequisite,
    DataSource,
    ProgramVersion,
    Student,
    StudentCourse,
    UserAccount,
)
from app.models.plans import StudentPlan

from .test_multi_program import _client, _session, _snapshot, requires_db, world  # noqa: F401

PLANS = "/api/v1/student/plans"
CURRENT = f"{PLANS}/current"
CONTEXT = f"{PLANS}/context"


def _app():
    from app.main import create_app

    return create_app()


@pytest.fixture(autouse=True)
def _ready(request):
    """Phase 7.2.1: a saved plan needs CONFIRMED history and a CONFIRMED
    Planning Profile. The world's students get both (a default profile), unless
    a test asks for the unready state with @pytest.mark.unready."""
    if "world" not in request.fixturenames or request.node.get_closest_marker("unready"):
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


def _profile(world, **fields):
    from app.domain.planning_profile import PlanningProfileInput
    from app.services.planning.profile import confirm_profile

    with _session() as session:
        confirm_profile(session, session.get(Student, world["a"]),
                        PlanningProfileInput(**fields), dt.datetime.now(dt.UTC))
        session.commit()


def _plan_rows(student_id) -> int:
    with _session() as session:
        return session.scalar(select(func.count()).select_from(StudentPlan)
                              .where(StudentPlan.student_id == student_id))


# --------------------------------------------------------------------------
# ownership and persistence
# --------------------------------------------------------------------------


@requires_db
async def test_a_saved_plan_belongs_to_its_owner_only(world) -> None:  # noqa: F811
    app = _app()
    async with _client(app, world["account_a"]) as ac:
        saved = await ac.post(PLANS, json={"start_term": "20261"})
        again = await ac.get(CURRENT)
    async with _client(app, world["account_b"]) as ac:
        other = await ac.get(CURRENT)
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["freshness"] == {"state": "current", "reasons": []}
    assert body["result"]["target"]["program_key"] == world["keys"]["p1"]
    assert body["result"]["target"]["is_current_program"] is True
    # The read returns exactly what was saved - the engine's own output.
    assert again.json()["plan"]["id"] == body["id"]
    assert again.json()["plan"]["result"] == body["result"]
    # Another account sees nothing of it.
    assert other.status_code == 200 and other.json() == {"plan": None}
    assert body["id"] not in other.text


@requires_db
async def test_the_saved_plan_is_the_engines_exact_output(world) -> None:  # noqa: F811
    from app.services.planning.service import generate_plan

    from app.domain.planning_profile import PlanningProfileInput, to_constraints

    _profile(world, max_terms=3)
    async with _client(_app(), world["account_a"]) as ac:
        body = (await ac.post(PLANS, json={"start_term": "20261"})).json()
    with _session() as session:
        fresh = generate_plan(session, session.get(Student, world["a"]), start_term="20261",
                              constraints=to_constraints(PlanningProfileInput(max_terms=3)))
    assert body["result"] == fresh.model_dump(mode="json")
    # The plan used the CONFIRMED profile: target, maximum and pace included.
    assert body["constraints"]["max_terms"] == 3
    assert body["constraints"]["pace"] == "balanced"
    assert body["constraints"]["target_credits_per_term"] == "15"


@requires_db
async def test_regenerating_replaces_and_never_accumulates(world) -> None:  # noqa: F811
    async with _client(_app(), world["account_a"]) as ac:
        first = (await ac.post(PLANS, json={"start_term": "20261"})).json()
        second = (await ac.post(PLANS, json={"start_term": "20269"})).json()
    assert _plan_rows(world["a"]) == 1
    assert second["start_term"] == "20269" and first["start_term"] == "20261"


@requires_db
@pytest.mark.parametrize("field,value", [
    ("student_id", "x"), ("account_id", "x"), ("program_key", "sas-198-ba"),
    ("catalog_year", "2025-2026"), ("status", "covers_all_requirements"),
    ("result", {}), ("terms", []), ("freshness", "current"),
    ("constraints", {"max_terms": 2}), ("pace", "finish_earlier"),
])
async def test_the_save_body_accepts_only_a_start_term(
        world, field, value) -> None:  # noqa: F811
    """No identity, no what-if program, no status, no client-made plan - and
    no load settings: those come only from the confirmed profile (7.2.1)."""
    async with _client(_app(), world["account_a"]) as ac:
        response = await ac.post(PLANS, json={"start_term": "20261", field: value})
    assert response.status_code == 422
    assert _plan_rows(world["a"]) == 0


@requires_db
async def test_delete_removes_only_my_plan(world) -> None:  # noqa: F811
    app = _app()
    async with _client(app, world["account_a"]) as ac:
        await ac.post(PLANS, json={"start_term": "20261"})
    async with _client(app, world["account_b"]) as ac:
        await ac.post(PLANS, json={"start_term": "20261"})
        gone = await ac.delete(CURRENT)
        after = await ac.get(CURRENT)
    assert gone.status_code == 204 and after.json() == {"plan": None}
    assert _plan_rows(world["a"]) == 1


@requires_db
async def test_reading_the_plan_and_context_writes_nothing(world) -> None:  # noqa: F811
    async with _client(_app(), world["account_a"]) as ac:
        await ac.post(PLANS, json={"start_term": "20261"})
        with _session() as session:
            before = _snapshot(session)
            plan_before = session.execute(text("SELECT * FROM student_plan ORDER BY 1")).all()
        for _ in range(2):
            assert (await ac.get(CURRENT)).status_code == 200
            assert (await ac.get(CONTEXT)).status_code == 200
    with _session() as session:
        assert _snapshot(session) == before
        assert session.execute(text("SELECT * FROM student_plan ORDER BY 1")).all() == plan_before


@requires_db
def test_deleting_the_student_deletes_the_plan(world) -> None:  # noqa: F811
    from app.services.planning.saved import save_plan

    with _session() as session:
        student = session.get(Student, world["a"])
        save_plan(session, student, start_term="20261", constraints=PlanningConstraints())
        session.commit()
        session.execute(text("DELETE FROM student_course WHERE student_id = :s"),
                        {"s": world["a"]})
        session.execute(text("DELETE FROM student_audit_cache WHERE student_id = :s"),
                        {"s": world["a"]})
        session.execute(text("UPDATE student SET user_id = NULL WHERE id = :s"),
                        {"s": world["a"]})
        session.execute(text("DELETE FROM student_link_event WHERE student_id = :s"),
                        {"s": world["a"]})
        session.execute(text("DELETE FROM student WHERE id = :s"), {"s": world["a"]})
        session.commit()
    assert _plan_rows(world["a"]) == 0


# --------------------------------------------------------------------------
# freshness: every input, and the cause rather than its echo
# --------------------------------------------------------------------------


async def _save(world, start="20261") -> None:  # noqa: F811
    async with _client(_app(), world["account_a"]) as ac:
        assert (await ac.post(PLANS, json={"start_term": start})).status_code == 200


async def _freshness(world) -> dict:  # noqa: F811
    async with _client(_app(), world["account_a"]) as ac:
        return (await ac.get(CURRENT)).json()["plan"]["freshness"]


@requires_db
async def test_a_history_change_makes_the_plan_stale(world) -> None:  # noqa: F811
    await _save(world)
    with _session() as session:
        course = session.scalar(select(Course).where(
            Course.course_string == world["courses"]["Z"]))
        session.add(StudentCourse(student_id=world["a"], course_id=course.id, term_code="20259",
                                  status="completed", grade="B", credits_earned=3))
        session.commit()
    assert await _freshness(world) == {"state": "stale", "reasons": ["academic_record_changed"]}


@requires_db
async def test_a_program_change_is_reported_as_the_cause(world) -> None:  # noqa: F811
    await _save(world)
    with _session() as session:
        session.get(Student, world["a"]).program_version_id = world["v2"]
        session.commit()
    fresh = await _freshness(world)
    assert fresh["state"] == "stale"
    assert "program_changed" in fresh["reasons"]
    assert "academic_record_changed" not in fresh["reasons"]


@requires_db
async def test_recurated_rules_make_the_plan_stale(world) -> None:  # noqa: F811
    await _save(world)
    with _session() as session:
        version = session.get(ProgramVersion, world["v1"])
        version.total_credits_min = (version.total_credits_min or 0) + 3
        session.commit()
    assert "program_rules_changed" in (await _freshness(world))["reasons"]


@requires_db
async def test_new_rutgers_data_makes_the_plan_stale(world) -> None:  # noqa: F811
    await _save(world)
    with _session() as session:
        session.add(DataSource(kind="rutgers_official_api", url="synthetic://soc",
                               content_hash=uuid.uuid4().hex, term_code="20991",
                               retrieved_at=dt.datetime.now(dt.UTC), version=1))
        session.commit()
    try:
        assert (await _freshness(world))["reasons"] == ["rutgers_data_changed"]
    finally:
        with _session() as session:
            session.execute(text("DELETE FROM data_source WHERE term_code = '20991'"))
            session.commit()


@requires_db
async def test_an_engine_update_makes_the_plan_stale(world) -> None:  # noqa: F811
    await _save(world)
    with _session() as session:
        row = session.scalar(select(StudentPlan).where(StudentPlan.student_id == world["a"]))
        row.planning_engine_version = "6.4.9"
        session.commit()
    assert (await _freshness(world))["reasons"] == ["engine_updated"]


@requires_db
async def test_a_plan_whose_first_term_is_now_recorded_is_stale(world) -> None:  # noqa: F811
    await _save(world, start="20261")
    with _session() as session:
        course = session.scalar(select(Course).where(
            Course.course_string == world["courses"]["Z"]))
        session.add(StudentCourse(student_id=world["a"], course_id=course.id, term_code="20261",
                                  status="in_progress"))
        session.commit()
    reasons = (await _freshness(world))["reasons"]
    assert "start_term_passed" in reasons and "academic_record_changed" in reasons


# --------------------------------------------------------------------------
# planning context
# --------------------------------------------------------------------------


@requires_db
async def test_context_starts_after_the_latest_recorded_term(world) -> None:  # noqa: F811
    async with _client(_app(), world["account_a"]) as ac:
        body = (await ac.get(CONTEXT)).json()
    assert body["latest_recorded_term"] == "20259"
    assert body["earliest_start_term"] == "20261"
    assert body["earliest_start_label"] == "Spring 2026"
    assert body["constraints_source"] == "coursepilot_planning_setting_not_rutgers_policy"
    assert body["default_constraints"]["max_terms"] == 8


@requires_db
async def test_context_lists_only_terms_with_section_data(world) -> None:  # noqa: F811
    from app.models import CourseSection

    with _session() as session:
        with_sections = set(session.scalars(select(CourseSection.term_code).distinct()))
    async with _client(_app(), world["account_a"]) as ac:
        listed = [t["term_code"] for t in (await ac.get(CONTEXT)).json()["loaded_terms"]]
    assert listed == sorted(with_sections)


@requires_db
async def test_unlinked_accounts_get_409_and_anonymous_401(world) -> None:  # noqa: F811
    with _session() as session:
        orphan = UserAccount(identity_provider="oidc", external_subject=f"u-{uuid.uuid4()}")
        session.add(orphan)
        session.commit()
        orphan_id = orphan.id
    async with _client(_app(), orphan_id) as ac:
        for method, url in (("get", CONTEXT), ("get", CURRENT), ("delete", CURRENT)):
            assert (await getattr(ac, method)(url)).status_code == 409
        assert (await ac.post(PLANS, json={"start_term": "20261"})).status_code == 409
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://test") as ac:
        for method, url in (("get", CONTEXT), ("get", CURRENT), ("delete", CURRENT)):
            assert (await getattr(ac, method)(url)).status_code == 401
        assert (await ac.post(PLANS, json={"start_term": "20261"})).status_code == 401


@requires_db
async def test_save_reports_the_engines_start_term_refusal(world) -> None:  # noqa: F811
    async with _client(_app(), world["account_a"]) as ac:
        past = await ac.post(PLANS, json={"start_term": "20259"})
    assert past.status_code == 422 and "future term" in past.json()["detail"]
    assert _plan_rows(world["a"]) == 0


# --------------------------------------------------------------------------
# blocked courses: titles and the ORIGINAL Rutgers condition, never a verdict
# --------------------------------------------------------------------------


def _result_with(issues) -> PlanResult:
    return PlanResult(
        target=ScenarioTarget(program_key="x-y-z", program_name="P", program_code="P",
                              degree_type="BA", school_code="01", catalog_year="2026-2027",
                              support_status="supported", curation_status="curated_from_prose",
                              is_current_program=True),
        status=PlanStatus.PARTIAL, issues=issues,
        metadata=PlanMetadata(audit_engine_version="t", start_term="20271",
                              constraints=PlanningConstraints(), academic_fingerprint="a",
                              rules_fingerprint="r", offering_dataset=[]))


@requires_db
def test_blocked_courses_carry_title_and_rutgers_text(world) -> None:  # noqa: F811
    from app.services.planning.saved import blocked_courses

    unknown, offering = world["courses"]["W"], world["courses"]["E"]
    with _session() as session:
        course = session.scalar(select(Course).where(Course.course_string == unknown))
        source = DataSource(kind="manual_curation", url="synthetic://prq",
                            content_hash=uuid.uuid4().hex, retrieved_at=dt.datetime.now(dt.UTC),
                            version=1)
        session.add(source)
        session.flush()
        off = CourseOffering(course_id=course.id, term_code="20261", campus_code="NB",
                             source_id=source.id)
        session.add(off)
        session.flush()
        session.add(CoursePrerequisite(
            offering_id=off.id, course_id=course.id, term_code="20261",
            raw_text="<em> Permission of instructor. </em>", classification="unknown",
            parser_version="t", source_id=source.id))
        session.flush()
        out = blocked_courses(session, _result_with([
            PlanningIssue(severity=Severity.NEEDS_CONFIRMATION, code="ELIGIBILITY_UNKNOWN",
                          requirement_code="P1_X", message="m",
                          details={"courses": {unknown: ["unmodeled_condition:permission"]},
                                   "count": 1}),
            PlanningIssue(severity=Severity.NEEDS_CONFIRMATION, code="NO_PLACEABLE_OFFERING",
                          requirement_code="P1_Y", message="m",
                          details={"by_reason": {"no_offering_evidence": [offering]}}),
            PlanningIssue(severity=Severity.BLOCKER, code="REQUIREMENT_NOT_PLANNED",
                          requirement_code="P1_X", message="m", details={}),
        ]))
        session.rollback()
    by_course = {b.course: b for b in out}
    assert set(by_course) == {unknown, offering}
    assert by_course[unknown].issue_code == "ELIGIBILITY_UNKNOWN"
    assert by_course[unknown].reasons == ["unmodeled_condition:permission"]
    assert by_course[unknown].prerequisite_text == "Permission of instructor."
    assert by_course[unknown].prerequisite_term == "20261"
    assert by_course[unknown].title
    assert by_course[offering].reasons == ["no_offering_evidence"]
    assert by_course[offering].prerequisite_text is None


# --------------------------------------------------------------------------
# Phase 7.2.1: readiness gate, start-term policy, preference staleness
# --------------------------------------------------------------------------


@requires_db
@pytest.mark.unready
async def test_no_saved_plan_without_confirmed_history_or_profile(world) -> None:  # noqa: F811
    async with _client(_app(), world["account_a"]) as ac:
        no_history = await ac.post(PLANS, json={})
    assert no_history.status_code == 409 and "academic history" in no_history.json()["detail"]
    with _session() as session:
        session.get(Student, world["a"]).onboarding_completed_at = dt.datetime.now(dt.UTC)
        session.commit()
    async with _client(_app(), world["account_a"]) as ac:
        no_profile = await ac.post(PLANS, json={})
    assert no_profile.status_code == 409 and "planned" in no_profile.json()["detail"]
    assert _plan_rows(world["a"]) == 0


@requires_db
async def test_the_start_defaults_to_the_server_choice(world) -> None:  # noqa: F811
    async with _client(_app(), world["account_a"]) as ac:
        ctx = (await ac.get(CONTEXT)).json()
        saved = (await ac.post(PLANS, json={})).json()
    assert saved["start_term"] == ctx["earliest_start_term"] == "20261"
    assert ctx["current_term"] == "20259"                      # ACADEMIC_TODAY 2025-12-01
    assert [o["term_code"] for o in ctx["start_options"]][:3] == ["20261", "20269", "20271"]


@requires_db
async def test_a_returning_student_never_starts_in_the_past(world, monkeypatch) -> None:  # noqa: F811
    """Record ends Fall 2025; 'today' is March 2027: the plan starts Fall 2027,
    not the term after the record (Spring 2026)."""
    from app.core.config import get_settings

    monkeypatch.setenv("ACADEMIC_TODAY", "2027-03-01")
    get_settings.cache_clear()
    try:
        async with _client(_app(), world["account_a"]) as ac:
            ctx = (await ac.get(CONTEXT)).json()
            past = await ac.post(PLANS, json={"start_term": "20261"})
        assert ctx["latest_recorded_term"] == "20259"
        assert ctx["current_term"] == "20271"
        assert ctx["earliest_start_term"] == "20279"
        assert past.status_code == 422 and _plan_rows(world["a"]) == 0
    finally:
        monkeypatch.delenv("ACADEMIC_TODAY")
        get_settings.cache_clear()


@requires_db
async def test_planned_rows_do_not_move_the_start(world) -> None:  # noqa: F811
    with _session() as session:
        course = session.scalar(select(Course).where(Course.course_string == world["courses"]["Z"]))
        session.add(StudentCourse(student_id=world["a"], course_id=course.id, term_code="20279",
                                  status="planned"))
        session.commit()
    async with _client(_app(), world["account_a"]) as ac:
        ctx = (await ac.get(CONTEXT)).json()
    assert ctx["latest_recorded_term"] == "20259"
    assert ctx["earliest_start_term"] == "20261"


@requires_db
async def test_changing_preferences_makes_the_plan_stale(world) -> None:  # noqa: F811
    await _save(world)
    _profile(world, target_credits_per_term="12", max_credits_per_term="12")
    assert await _freshness(world) == {"state": "stale",
                                       "reasons": ["planning_preferences_changed"]}


@requires_db
async def test_time_passing_makes_the_plan_stale(world, monkeypatch) -> None:  # noqa: F811
    from app.core.config import get_settings

    await _save(world)
    monkeypatch.setenv("ACADEMIC_TODAY", "2026-02-15")       # Spring 2026 is now under way
    get_settings.cache_clear()
    try:
        assert "start_term_passed" in (await _freshness(world))["reasons"]
    finally:
        monkeypatch.delenv("ACADEMIC_TODAY")
        get_settings.cache_clear()
