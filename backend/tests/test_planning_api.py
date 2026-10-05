"""POST /api/v1/student/plans/generate (Phase 6.5) - identity, errors, read-only.

Reuses the Phase 6.0 multi-program world (real PostgreSQL). Its courses have
no offerings, so plans are honest PARTIAL plans that place nothing - which is
exactly what an API test of identity and side effects needs. Planning
behaviour itself is tested on real SOC data in
ingestion/tests/test_planning_engine.py.
"""

from __future__ import annotations

import ast
import pathlib
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.models import Student, UserAccount

from .test_multi_program import _client, _session, _snapshot, requires_db, world  # noqa: F401

URL = "/api/v1/student/plans/generate"
PLANNING = pathlib.Path(__file__).resolve().parents[1] / "app" / "services" / "planning"


@requires_db
async def test_each_student_gets_a_plan_of_their_own_record(world) -> None:  # noqa: F811
    from app.main import create_app

    app = create_app()
    async with _client(app, world["account_a"]) as ac:
        a = await ac.post(URL, json={"start_term": "20261"})
    async with _client(app, world["account_b"]) as ac:
        b = await ac.post(URL, json={"start_term": "20261"})
    assert a.status_code == b.status_code == 200
    a, b = a.json(), b.json()
    assert a["target"]["program_key"] == world["keys"]["p1"]
    assert a["target"]["is_current_program"] is True
    assert a["metadata"]["academic_fingerprint"] != b["metadata"]["academic_fingerprint"]
    assert a["metadata"]["planning_engine_version"] == "6.5.0"


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
        response = await ac.post(URL, json={"start_term": "20261", field: value})
    assert response.status_code == 422
    assert victim.external_ref not in response.text


@requires_db
async def test_constraints_cannot_be_relabelled_as_policy(world) -> None:  # noqa: F811
    from app.main import create_app

    async with _client(create_app(), world["account_a"]) as ac:
        bad = await ac.post(URL, json={"start_term": "20261",
                                       "constraints": {"source": "rutgers_policy"}})
        too_many = await ac.post(URL, json={"start_term": "20261",
                                            "constraints": {"max_credits_per_term": 40}})
        ok = await ac.post(URL, json={"start_term": "20261",
                                      "constraints": {"max_credits_per_term": 12}})
    assert bad.status_code == too_many.status_code == 422
    assert ok.status_code == 200
    assert ok.json()["metadata"]["constraints_source"] == \
        "coursepilot_planning_setting_not_rutgers_policy"


@requires_db
async def test_plan_api_errors_are_honest(world) -> None:  # noqa: F811
    from app.main import create_app

    with _session() as session:
        unlinked = UserAccount(identity_provider="oidc", external_subject=f"u-{uuid.uuid4()}")
        session.add(unlinked)
        session.commit()
        unlinked_id = unlinked.id
    app = create_app()
    async with _client(app, world["account_a"]) as ac:
        unknown = await ac.post(URL, json={"start_term": "20261", "program_key": "zz9-nope-ba"})
        empty = await ac.post(URL, json={"start_term": "20261",
                                         "program_key": world["keys"]["p4"]})
        bad_term = await ac.post(URL, json={"start_term": "20265"})
        past = await ac.post(URL, json={"start_term": "20259"})
        missing = await ac.post(URL, json={})
    async with _client(app, unlinked_id) as ac:
        orphan = await ac.post(URL, json={"start_term": "20261"})
    assert unknown.status_code == 404
    assert empty.status_code == 422
    assert bad_term.status_code == 422 and missing.status_code == 422
    assert past.status_code == 422 and "20259" in past.json()["detail"]
    assert orphan.status_code == 409


async def test_plan_generation_requires_authentication() -> None:
    from app.main import create_app

    async with AsyncClient(transport=ASGITransport(app=create_app()),
                           base_url="http://test") as ac:
        assert (await ac.post(URL, json={"start_term": "20271"})).status_code == 401


@requires_db
def test_what_if_plans_leave_every_table_unchanged(world) -> None:  # noqa: F811
    from app.services.planning.service import generate_plan

    with _session() as session:
        before = _snapshot(session)
        cache = text("SELECT * FROM student_audit_cache WHERE student_id = :s")
        cache_before = session.execute(cache, {"s": world["a"]}).all()
        student = session.get(Student, world["a"])
        for key in (None, world["keys"]["p2"], world["keys"]["p3"]):
            generate_plan(session, student, start_term="20261", program_key_=key)
        assert not (session.new or session.dirty or session.deleted)
        cache_after = session.execute(cache, {"s": world["a"]}).all()
        session.commit()
    with _session() as session:
        assert _snapshot(session) == before
    assert cache_before == cache_after


def test_the_planning_engine_calls_no_llm_and_reimplements_no_grade_rule() -> None:
    """Architecture: planning imports no provider/LLM/explanation module and
    contains no grade table - grades are app.domain.grades' alone."""
    forbidden = ("openai", "anthropic", "app.services.explanations", "app.llm",
                 "app.services.retrieval", "httpx", "requests")
    for path in sorted(PLANNING.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                assert not any(name == f or name.startswith(f + ".") for f in forbidden), \
                    (path.name, name)
        source = path.read_text(encoding="utf-8")
        for grade_table in ('"A-"', '"B+"', '"C+"', "LETTERS", "gpa_points"):
            assert grade_table not in source, (path.name, grade_table)


def test_no_llm_module_is_reachable_from_planning() -> None:
    """Transitively, in a fresh interpreter: generating a plan loads no LLM
    provider, explanation or retrieval module."""
    import subprocess
    import sys

    code = ("import sys, app.services.planning.service, app.api.v1.routes.plans; "
            "print('\\n'.join(sorted(m for m in sys.modules if m.startswith("
            "('app.llm', 'app.services.explanations', 'app.services.retrieval', "
            "'openai', 'anthropic')))))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         cwd=PLANNING.parents[2], check=True)
    assert out.stdout.strip() == "", out.stdout
