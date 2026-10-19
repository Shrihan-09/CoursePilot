"""Owner-isolated read-only progress API; fabricated PostgreSQL worlds only."""

from sqlalchemy import select

from app.models.plans import StudentPlan

from .test_multi_program import _client, _session, requires_db, world  # noqa: F401
from .test_saved_plan_api import _profile

URL = "/api/v1/student/progress"


@requires_db
async def test_progress_requires_ownership_and_does_not_populate_saved_plan(world):  # noqa: F811
    from app.main import create_app

    app = create_app()
    with _session() as session:
        before = [
            (str(p.id), p.result_blob, p.updated_at) for p in session.scalars(select(StudentPlan))
        ]
    async with _client(app, world["account_a"]) as client:
        response = await client.get(URL)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["coverage"] == "partial"
        assert data["graduation_eligibility"] == "not_assessed"
        assert [m["scope"] for m in data["metrics"]] == [
            "modeled_overall",
            "primary_program",
            "school_core",
        ]
        assert data["projection_state"] == "absent"
        assert "student_id" not in data and "external_ref" not in data
        assert (await client.post(URL, json={})).status_code == 405
    async with _client(app, world["account_b"]) as other:
        b = await other.get(URL)
        assert b.status_code == 200
        assert b.json()["academic_fingerprint"] != data["academic_fingerprint"]
    with _session() as session:
        after = [
            (str(p.id), p.result_blob, p.updated_at) for p in session.scalars(select(StudentPlan))
        ]
    assert after == before


@requires_db
async def test_stale_saved_projection_is_withheld_and_never_regenerated(world):  # noqa: F811
    from app.main import create_app

    app = create_app()
    # A saved plan needs CONFIRMED history as well as a profile (7.2.1 readiness).
    import datetime as dt

    from app.models import Student

    with _session() as session:
        session.get(Student, world["a"]).onboarding_completed_at = dt.datetime.now(dt.UTC)
        session.commit()
    _profile(world)
    async with _client(app, world["account_a"]) as client:
        saved = await client.post("/api/v1/student/plans", json={"start_term": "20261"})
        assert saved.status_code == 200, saved.text
        fresh = (await client.get(URL)).json()
        assert fresh["projection_state"] == "available"
        assert fresh["metrics"][0]["projected_plan"] is not None
        _profile(world, target_credits_per_term="12", max_credits_per_term="12")
        stale = (await client.get(URL)).json()
        assert stale["projection_state"] == "stale"
        assert all(m["projected_plan"] is None for m in stale["metrics"])
        again = await client.get("/api/v1/student/plans/current")
        assert again.json()["plan"]["updated_at"] == saved.json()["updated_at"]
