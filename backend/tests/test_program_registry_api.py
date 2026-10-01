"""Program API after Phase 6.3: published vs pending vs discovered-only.

  * a variant is part of the natural key (`...-ba-option-a`);
  * `support_status` follows the lifecycle - a curated LABEL is not enough;
  * discovered catalog entries are returned only on request, in their own
    list, and are never programs (no key, not auditable).
"""

from __future__ import annotations

import contextlib
import datetime as dt
import os
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.models import (
    CatalogPage,
    CurationStatus,
    DataSource,
    Program,
    ProgramCandidate,
    ProgramVersion,
    Requirement,
    School,
    UserAccount,
)

requires_db = pytest.mark.db
CURATED = CurationStatus.CURATED_FROM_PROSE.value
YEAR = "2031-2032"          # a year no other test uses: discovered entries are filtered by it


@contextlib.contextmanager
def _session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import NullPool

    url = os.environ.get("DATABASE_URL_SYNC",
                         "postgresql+psycopg://coursepilot:coursepilot@localhost:5432/coursepilot_test")
    engine = create_engine(url, poolclass=NullPool)
    try:
        with sessionmaker(engine)() as session:
            yield session
    finally:
        engine.dispose()


@pytest.fixture
def registry_world():
    s = uuid.uuid4().hex[:6]
    now = dt.datetime.now(dt.UTC)
    with _session() as session:
        src = DataSource(kind="manual_curation", url=f"synthetic://reg/{s}", content_hash=s,
                         retrieved_at=now, version=1)
        session.add(src)
        session.flush()
        school = School(code=f"RG{s}", name="Registry School", campus_code="NB", source_id=src.id)
        session.add(school)
        session.flush()

        def program(variant, state, basis, curation):
            p = Program(school_id=school.id, code="640", name="Mathematics", degree_type="BA",
                        variant=variant, source_id=src.id)
            session.add(p)
            session.flush()
            v = ProgramVersion(program_id=p.id, catalog_year=YEAR, source_id=src.id,
                               curation_status=curation, lifecycle_state=state,
                               publication_basis=basis)
            session.add(v)
            session.flush()
            session.add(Requirement(program_version_id=v.id, code="ROOT", name="Root",
                                    requirement_type="all_of", curation_status=curation,
                                    source_id=src.id))
            return v

        option_a = program("option-a", "validated", None, CURATED)       # curated LABEL only
        option_b = program("option-b", "published", "human_review", "unverified")
        page = CatalogPage(catalog_key="nb-undergrad", catalog_year=YEAR,
                           url_path=f"/schools/sas/program-listing/reg-{s}", title=f"Reg {s} 999",
                           school_slug="sas", trail="Programs", page_class="subject_coded",
                           subject_code="999", first_seen_at=now, last_seen_at=now,
                           snapshot_id=src.id, prose_sha256="a" * 64, fetched_at=now)
        unfetched = CatalogPage(catalog_key="nb-undergrad", catalog_year=YEAR,
                                url_path=f"/schools/sas/program-listing/unf-{s}",
                                title=f"Unf {s} 998", school_slug="sas", trail="Programs",
                                page_class="subject_coded", subject_code="998",
                                first_seen_at=now, last_seen_at=now)
        session.add_all([page, unfetched])
        session.flush()
        session.add_all([
            ProgramCandidate(catalog_page_id=page.id, candidate_key="major", heading="Major",
                             credential_type="major", extraction_method="t",
                             program_version_id=option_a.id),
            ProgramCandidate(catalog_page_id=page.id, candidate_key="minor-x", heading="Minor X",
                             credential_type="minor", extraction_method="t"),
        ])
        account = UserAccount(identity_provider="oidc", external_subject=f"reg-{s}")
        session.add(account)
        session.commit()
        return {"school": school.code.lower(), "account": account.id, "page": page.url_path,
                "unfetched": unfetched.url_path, "versions": (option_a.id, option_b.id)}


def _client(app, account_id):
    from app.api.security import Principal, get_principal

    app.dependency_overrides[get_principal] = lambda: Principal(
        account_id=account_id, subject="s", issuer="https://issuer.test", provider="oidc")
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@requires_db
async def test_variants_are_distinct_programs_and_status_follows_lifecycle(registry_world) -> None:
    from app.main import create_app

    w = registry_world
    async with _client(create_app(), w["account"]) as ac:
        a = await ac.get(f"/api/v1/programs/{w['school']}-640-ba-option-a")
        b = await ac.get(f"/api/v1/programs/{w['school']}-640-ba-option-b")
        bare = await ac.get(f"/api/v1/programs/{w['school']}-640-ba")
    assert a.status_code == b.status_code == 200 and bare.status_code == 404
    va, vb = a.json()["versions"][0], b.json()["versions"][0]
    assert a.json()["variant"] == "option-a"
    # curated_from_prose label, but only `validated`: NOT supported.
    assert (va["support_status"], va["lifecycle_state"]) == ("pending_review", "validated")
    assert (vb["support_status"], vb["publication_basis"]) == ("supported", "human_review")


@requires_db
async def test_discovered_entries_are_opt_in_and_separate(registry_world) -> None:
    from app.main import create_app

    w = registry_world
    async with _client(create_app(), w["account"]) as ac:
        default = await ac.get("/api/v1/programs")
        opted = await ac.get("/api/v1/programs", params={"include_discovered": "true",
                                                          "catalog_year": YEAR})
        bad_year = await ac.get("/api/v1/programs", params={"include_discovered": "true",
                                                            "catalog_year": "next year"})
    assert default.status_code == 200 and default.json()["discovered"] is None
    assert w["page"] not in default.text and w["unfetched"] not in default.text

    body = opted.json()
    assert {p["program_key"] for p in body["programs"]} == {
        p["program_key"] for p in default.json()["programs"]}       # programs list unchanged
    mine = [d for d in body["discovered"] if d["url_path"] in (w["page"], w["unfetched"])]
    by = {(d["url_path"], d["candidate_key"]): d for d in mine}
    # The linked candidate (a curated version exists) is NOT discovered-only.
    assert (w["page"], "major") not in by
    assert by[(w["page"], "minor-x")]["lifecycle"] == "parsed"
    assert by[(w["unfetched"], None)]["lifecycle"] == "discovered"
    assert all("program_key" not in d for d in mine)
    for vid in w["versions"]:
        assert str(vid) not in opted.text
    assert bad_year.status_code == 422


@requires_db
def test_listing_uses_a_fixed_number_of_queries(registry_world) -> None:
    """No N+1: the program list costs the same queries for 2 or 200 programs."""
    from sqlalchemy import event

    from app.services.programs import list_discovered, list_programs

    with _session() as session:
        counter = {"n": 0}

        def count(*_args, **_kw):
            counter["n"] += 1

        event.listen(session.get_bind(), "before_cursor_execute", count)
        programs = list_programs(session)
        program_queries = counter["n"]
        counter["n"] = 0
        list_discovered(session, YEAR)
        discovered_queries = counter["n"]
    assert len(programs) >= 2
    assert program_queries <= 4, program_queries
    assert discovered_queries <= 2, discovered_queries
