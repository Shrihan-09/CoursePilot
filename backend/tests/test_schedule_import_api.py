"""PostgreSQL integration: fabricated schedules, confirmation-only identity writes."""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import func, select, text

from app.models import StudentCourse
from app.models.schedule_import import ScheduleImport
from app.services.scheduling.pdf_worker import default_schedule_extractor, extract_layout
from app.services.transcripts.extract import ExtractedText

from .support.synthetic_webreg import build_schedule_pdf, objects
from .test_multi_program import _client, _session, requires_db, world  # noqa: F401
from .test_personalization_api import sections  # noqa: F401

URL = "/api/v1/student/schedule-imports"


def _app():
    from app.main import create_app

    app = create_app()

    def extract(data):
        result = extract_layout(data, 20, 200000)
        assert result[0] == "ok"
        return ExtractedText(result[1], result[2], result[3])

    app.dependency_overrides[default_schedule_extractor] = lambda: extract
    return app


def _pdf():
    items = objects(term="Spring 1999", records=[(70, 600, "01:999:101:01-91234 (3.0)")])
    items.append((40, 755, "FABRICATED_PRIVATE_CANARY"))
    return build_schedule_pdf(items)


@requires_db
async def test_stage_owner_confirm_idempotent_and_an_unmatched_row_writes_no_history(world, caplog):  # noqa: F811
    app = _app()
    with _session() as session:
        before = session.scalar(select(func.count()).select_from(StudentCourse))
    async with _client(app, world["account_a"]) as ac:
        response = await ac.post(URL, content=_pdf())
        assert response.status_code == 201, response.text
        imp = response.json()
        assert imp["ready"] and imp["rows"][0]["state"] == "metadata_unavailable"
        assert (await ac.get("/api/v1/student/registrations")).json()["registrations"] == []
        assert "FABRICATED_PRIVATE_CANARY" not in response.text
        same = (await ac.post(URL, content=_pdf())).json()
        assert same["id"] == imp["id"]
    async with _client(app, world["account_b"]) as other:
        assert (await other.get(f"{URL}/{imp['id']}")).status_code == 404
        assert (
            await other.post(f"{URL}/{imp['id']}/confirm", json={"expected_version": 1})
        ).status_code == 404
    async with _client(app, world["account_a"]) as ac:
        stale = await ac.post(f"{URL}/{imp['id']}/confirm", json={"expected_version": 9})
        assert stale.status_code == 422
        for _ in range(2):
            confirmed = await ac.post(
                f"{URL}/{imp['id']}/confirm", json={"expected_version": imp["version"]}
            )
            assert confirmed.status_code == 200 and confirmed.json()["state"] == "confirmed"
        records = (await ac.get("/api/v1/student/registrations")).json()["registrations"]
        assert len(records) == 1 and records[0]["index_number"] == "91234"
        assert records[0]["metadata_state"] == "unavailable" and records[0]["meetings"] == []
    with _session() as session:
        assert session.scalar(select(func.count()).select_from(StudentCourse)) == before
    assert "FABRICATED_PRIVATE_CANARY" not in caplog.text


@requires_db
async def test_expiration_discard_ownership_and_review_version(world):  # noqa: F811
    app = _app()
    async with _client(app, world["account_a"]) as ac:
        imp = (await ac.post(URL, content=_pdf())).json()
        edited = await ac.patch(
            f"{URL}/{imp['id']}/rows/{imp['rows'][0]['id']}",
            json={"expected_version": 1, "index_number": "91235"},
        )
        assert edited.status_code == 200 and edited.json()["version"] == 2
        assert (
            await ac.post(f"{URL}/{imp['id']}/confirm", json={"expected_version": 1})
        ).status_code == 422
        with _session() as session:
            record = session.get(ScheduleImport, uuid.UUID(imp["id"]))
            record.expires_at = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=1)
            session.commit()
        expired = (await ac.get(f"{URL}/{imp['id']}")).json()
        assert expired["state"] == "expired" and expired["rows"] == []
        assert (
            await ac.post(f"{URL}/{imp['id']}/confirm", json={"expected_version": 2})
        ).status_code == 422
        assert (await ac.delete(f"{URL}/{imp['id']}")).json()["state"] == "discarded"
        assert (await ac.post(URL, content=b"not pdf")).status_code == 422
        assert (await ac.get(f"{URL}/{uuid.uuid4()}")).status_code == 404


@requires_db
async def test_exact_soc_match_survives_metadata_removal(world, sections):  # noqa: F811
    pdf = build_schedule_pdf(
        objects(term="Spring 2099", records=[(70, 600, f"{sections['course']}:01-11111 (3.0)")])
    )
    async with _client(_app(), world["account_a"]) as ac:
        response = await ac.post(URL, content=pdf)
        assert response.status_code == 201, response.text
        imp = response.json()
        assert imp["rows"][0]["state"] == "matched"
        confirmed = await ac.post(
            f"{URL}/{imp['id']}/confirm", json={"expected_version": imp["version"]}
        )
        assert confirmed.status_code == 200, confirmed.text
        records = (await ac.get("/api/v1/student/registrations")).json()["registrations"]
        assert records[0]["metadata_state"] == "available"
        assert records[0]["meetings"][0]["day"] == "M"
        with _session() as session:
            session.execute(
                text("DELETE FROM section_meeting WHERE section_id = :s"), {"s": sections["ids"][0]}
            )
            session.execute(
                text("DELETE FROM course_section WHERE id = :s"), {"s": sections["ids"][0]}
            )
            session.commit()
        records = (await ac.get("/api/v1/student/registrations")).json()["registrations"]
        assert records[0]["metadata_state"] == "unavailable"
        assert records[0]["index_number"] == "11111" and records[0]["section_number"] == "01"
        assert records[0]["meetings"] == []


@requires_db
async def test_fallback_matches_exact_section_and_soc_change_requires_new_review(world, sections):  # noqa: F811
    pdf = build_schedule_pdf(
        objects(
            term="Spring 2099",
            records=[],
            arrangement=[f"* FABRICATED COURSE ({sections['course']}:01)"],
        )
    )
    async with _client(_app(), world["account_a"]) as ac:
        imp = (await ac.post(URL, content=pdf)).json()
        assert imp["rows"][0]["resolved_index"] == "11111"
        with _session() as session:
            session.execute(
                text("DELETE FROM section_meeting WHERE section_id = :s"), {"s": sections["ids"][0]}
            )
            session.execute(
                text("DELETE FROM course_section WHERE id = :s"), {"s": sections["ids"][0]}
            )
            session.commit()
        changed = await ac.post(
            f"{URL}/{imp['id']}/confirm", json={"expected_version": imp["version"]}
        )
        assert changed.status_code == 422
        # An explicit review patch revalidates the new metadata state/version.
        reviewed = (
            await ac.patch(
                f"{URL}/{imp['id']}/rows/{imp['rows'][0]['id']}",
                json={"expected_version": imp["version"], "index_number": "11111"},
            )
        ).json()
        assert reviewed["rows"][0]["state"] == "metadata_unavailable"
        assert (
            await ac.post(
                f"{URL}/{imp['id']}/confirm", json={"expected_version": reviewed["version"]}
            )
        ).status_code == 200


@requires_db
async def test_registration_whose_soc_index_changed_shows_no_meetings(world, sections):  # noqa: F811
    pdf = build_schedule_pdf(
        objects(term="Spring 2099", records=[(70, 600, f"{sections['course']}:01-11111 (3.0)")])
    )
    async with _client(_app(), world["account_a"]) as ac:
        imp = (await ac.post(URL, content=pdf)).json()
        confirmed = await ac.post(
            f"{URL}/{imp['id']}/confirm", json={"expected_version": imp["version"]}
        )
        assert confirmed.status_code == 200, confirmed.text
        with _session() as session:
            # SOC renumbered the section: the stored identity no longer describes it.
            session.execute(
                text("UPDATE course_section SET index_number = '29999' WHERE id = :s"),
                {"s": sections["ids"][0]},
            )
            session.commit()
        records = (await ac.get("/api/v1/student/registrations")).json()["registrations"]
        assert records[0]["metadata_state"] == "unavailable"
        assert records[0]["index_number"] == "11111"
        assert records[0]["meetings"] == [] and records[0]["normalized_meetings"] == []
