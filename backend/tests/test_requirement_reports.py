"""'Report a wrong requirement' (Prompt 08 B1 item 5).

A SYNTHETIC program (school ZZB1T, never real Rutgers data) is created in the
*_test database for each test and removed afterwards.
"""

from __future__ import annotations

import contextlib
import os
import uuid
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient

requires_db = pytest.mark.db
ISSUER = "https://idp.example.edu"
KEY = "zzb1t-999-bs"


@contextlib.contextmanager
def _session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import NullPool

    url = os.environ.get(
        "DATABASE_URL_SYNC",
        "postgresql+psycopg://coursepilot:coursepilot@localhost:5432/coursepilot_test")
    engine = create_engine(url, poolclass=NullPool)
    try:
        with sessionmaker(engine)() as session:
            yield session
    finally:
        engine.dispose()


@pytest.fixture
def app():
    from app.main import create_app

    return create_app()


def _client_as(app, account_id):
    from app.api.security import Principal, get_principal

    app.dependency_overrides[get_principal] = lambda: Principal(
        account_id=account_id, subject="s", issuer=ISSUER, provider="oidc")
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture
def world():
    from app.models import (
        DataSource,
        Program,
        ProgramVersion,
        Requirement,
        RequirementReport,
        School,
        UserAccount,
    )

    with _session() as s:
        src = DataSource(kind="synthetic", url="https://example.invalid", title="b1 test",
                         retrieved_at=datetime.now(UTC), content_hash=uuid.uuid4().hex)
        s.add(src)
        s.flush()
        school = School(code="ZZB1T", name="Synthetic school (TEST)", source_id=src.id)
        s.add(school)
        s.flush()
        program = Program(school_id=school.id, code="999", name="Synthetic (TEST)",
                          degree_type="BS", source_id=src.id)
        s.add(program)
        s.flush()
        version = ProgramVersion(program_id=program.id, catalog_year="2026-2027",
                                 source_id=src.id, definition_sha256="x")
        s.add(version)
        s.flush()
        s.add(Requirement(program_version_id=version.id, code="SYN_NODE", name="Synthetic node",
                          requirement_type="all_of", source_id=src.id))
        student = UserAccount(identity_provider="oidc", external_subject=f"r-{uuid.uuid4()}")
        admin = UserAccount(identity_provider="oidc", external_subject=f"a-{uuid.uuid4()}",
                            is_admin=True)
        s.add_all([student, admin])
        s.commit()
        ids = {"student": student.id, "admin": admin.id, "school": school.id, "source": src.id}
    yield ids
    with _session() as s:
        from sqlalchemy import delete

        s.execute(delete(RequirementReport).where(RequirementReport.program_key == KEY))
        s.execute(delete(Program).where(Program.school_id == ids["school"]))
        s.execute(delete(School).where(School.id == ids["school"]))
        s.execute(delete(UserAccount).where(UserAccount.id.in_([ids["student"], ids["admin"]])))
        s.execute(delete(DataSource).where(DataSource.id == ids["source"]))
        s.commit()


BODY = {"program_key": KEY, "catalog_year": "2026-2027", "requirement_code": "SYN_NODE",
        "message": "The catalog says three courses, not two."}


async def test_reporting_requires_sign_in(client) -> None:
    assert (await client.post("/api/v1/requirements/reports", json=BODY)).status_code == 401


@requires_db
async def test_a_student_reports_and_an_admin_lists_and_resolves(app, world) -> None:
    async with _client_as(app, world["student"]) as ac:
        created = await ac.post("/api/v1/requirements/reports", json=BODY)
        assert created.status_code == 201, created.text
        rid = created.json()["id"]
        # A student cannot read the admin list.
        assert (await ac.get("/api/v1/admin/requirement-reports")).status_code == 403
    async with _client_as(app, world["admin"]) as ac:
        listed = (await ac.get("/api/v1/admin/requirement-reports")).json()
        mine = next(r for r in listed if r["id"] == rid)
        assert mine["requirement_name"] == "Synthetic node"
        assert mine["message"] == BODY["message"]
        # No personal data beyond a redacted account reference.
        assert str(world["student"]) not in str(mine)
        assert set(mine) == {"id", "program_key", "catalog_year", "requirement_code",
                             "requirement_name", "message", "status", "resolved_note",
                             "created_at", "reporter"}
        done = await ac.patch(f"/api/v1/admin/requirement-reports/{rid}",
                              json={"status": "resolved", "resolved_note": "Fixed in encoding."})
        assert done.json()["status"] == "resolved"
        assert rid not in {r["id"] for r in
                           (await ac.get("/api/v1/admin/requirement-reports")).json()}


@requires_db
async def test_unknown_requirement_and_blank_messages_are_refused(app, world) -> None:
    async with _client_as(app, world["student"]) as ac:
        assert (await ac.post("/api/v1/requirements/reports",
                              json=BODY | {"requirement_code": "NOPE"})).status_code == 404
        assert (await ac.post("/api/v1/requirements/reports",
                              json=BODY | {"message": "   "})).status_code == 422
        assert (await ac.post("/api/v1/requirements/reports",
                              json=BODY | {"message": "x" * 2001})).status_code == 422
