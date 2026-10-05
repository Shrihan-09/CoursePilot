"""POST /api/v1/student/schedules/generate (Phase 6.6) - identity, errors, read-only.

Reuses the Phase 6.0 multi-program world (real PostgreSQL). Its synthetic
courses have no sections, so the answer is an honest
TERM_SCHEDULE_NOT_PUBLISHED - exactly what an identity and side-effect test
needs. Scheduling behaviour is tested on real SOC records in
ingestion/tests/test_schedule_engine.py.
"""

from __future__ import annotations

import ast
import pathlib
import subprocess
import sys

import pytest
from httpx import ASGITransport, AsyncClient

from app.models import Student

from .test_multi_program import _client, _session, _snapshot, requires_db, world  # noqa: F401

URL = "/api/v1/student/schedules/generate"
SCHEDULING = pathlib.Path(__file__).resolve().parents[1] / "app" / "services" / "scheduling"
BODY = {"term": "20269", "courses": ["01:198:344"]}


@requires_db
async def test_a_student_gets_an_answer_about_their_own_record(world) -> None:  # noqa: F811
    from app.main import create_app

    async with _client(create_app(), world["account_a"]) as ac:
        response = await ac.post(URL, json={**BODY, "term": "20291"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "term_schedule_not_published" and payload["options"] == []
    assert payload["metadata"]["schedule_engine_version"] == "6.6.0"


@requires_db
@pytest.mark.parametrize("field", [
    "student_id", "student_ref", "external_ref", "account_id", "user_id", "principal",
])
async def test_no_body_field_can_select_a_student(world, field) -> None:  # noqa: F811
    from app.main import create_app

    with _session() as session:
        victim = session.get(Student, world["b"])
        value = str(victim.id) if field != "external_ref" else victim.external_ref
    async with _client(create_app(), world["account_a"]) as ac:
        response = await ac.post(URL, json={**BODY, field: value})
    assert response.status_code == 422
    assert victim.external_ref not in response.text


@requires_db
async def test_request_validation(world) -> None:  # noqa: F811
    from app.main import create_app

    async with _client(create_app(), world["account_a"]) as ac:
        bad = {
            "term": await ac.post(URL, json={**BODY, "term": "20265"}),
            "course": await ac.post(URL, json={**BODY, "courses": ["CS344"]}),
            "empty": await ac.post(URL, json={**BODY, "courses": []}),
            "too_many_results": await ac.post(URL, json={**BODY, "max_results": 1000}),
            "too_many_courses": await ac.post(
                URL, json={**BODY, "courses": [f"01:198:{n}" for n in range(100, 110)]}),
            "pref_field": await ac.post(URL, json={**BODY, "preferences": {"bus_minutes": 5}}),
            "pref_day": await ac.post(URL, json={**BODY, "preferences": {"avoid_days": ["X"]}}),
            "registration": await ac.post(URL, json={**BODY, "register": True}),
        }
        ok = await ac.post(URL, json={**BODY, "courses": ["01:198:344", "01:198:344"],
                                      "max_results": 25})
    assert {k: r.status_code for k, r in bad.items()} == {k: 422 for k in bad}
    assert ok.status_code == 200
    assert ok.json()["requested_courses"] == ["01:198:344"]


async def test_schedules_require_authentication() -> None:
    from app.main import create_app

    async with AsyncClient(transport=ASGITransport(app=create_app()),
                           base_url="http://test") as ac:
        assert (await ac.post(URL, json=BODY)).status_code == 401


@requires_db
async def test_an_unlinked_account_gets_no_record(world) -> None:  # noqa: F811
    import uuid

    from app.main import create_app
    from app.models import UserAccount

    with _session() as session:
        account = UserAccount(identity_provider="oidc", external_subject=f"u-{uuid.uuid4()}")
        session.add(account)
        session.commit()
        account_id = account.id
    async with _client(create_app(), account_id) as ac:
        assert (await ac.post(URL, json=BODY)).status_code == 409


@requires_db
def test_schedule_generation_leaves_every_table_unchanged(world) -> None:  # noqa: F811
    from app.services.scheduling.service import generate_schedule

    with _session() as session:
        before = _snapshot(session)
        student = session.get(Student, world["a"])
        for term in ("20269", "20267", "20291"):
            generate_schedule(session, student, term_code=term, courses=["01:198:344"])
        assert not (session.new or session.dirty or session.deleted)
        session.commit()
    with _session() as session:
        assert _snapshot(session) == before


def test_the_schedule_engine_imports_no_llm_and_no_registration_client() -> None:
    forbidden = ("openai", "anthropic", "app.llm", "app.services.explanations",
                 "app.services.retrieval", "httpx", "requests", "urllib", "selenium",
                 "playwright")
    for path in sorted(SCHEDULING.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                     else [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
            for name in names:
                assert not any(name == f or name.startswith(f + ".") for f in forbidden), \
                    (path.name, name)
        text = path.read_text(encoding="utf-8").lower()
        assert "webreg.rutgers" not in text and "sims.rutgers" not in text


def test_no_llm_module_is_reachable_from_scheduling() -> None:
    code = ("import sys, app.services.scheduling.service, app.api.v1.routes.schedules; "
            "print('\\n'.join(sorted(m for m in sys.modules if m.startswith("
            "('app.llm', 'app.services.explanations', 'app.services.retrieval', "
            "'openai', 'anthropic')))))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         cwd=SCHEDULING.parents[2], check=True)
    assert out.stdout.strip() == "", out.stdout


def test_the_schedule_engine_never_chooses_courses() -> None:
    """Architecture: no planning, requirement or degree-audit module is used -
    the scheduler cannot substitute a course because it cannot see the degree."""
    for path in sorted(SCHEDULING.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith(
                    ("app.services.planning", "app.services.audit")), (path.name, node.module)
