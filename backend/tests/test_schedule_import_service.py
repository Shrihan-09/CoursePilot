"""Schedule import failure injection without a database or private documents."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.models.schedule_import import ScheduleImport, StudentRegistration
from app.services.scheduling import imports as service
from app.services.transcripts.extract import ExtractedText, ExtractionFailed

from .support.synthetic_webreg import extracted_layout, objects

NOW = datetime(2026, 10, 19, tzinfo=UTC)
ROW = {
    "term_code": "20991",
    "course_string": "01:999:101",
    "section_number": "01",
    "index_number": "91234",
}


class FakeSession:
    def __init__(self, matches=(), existing=None):
        self.matches, self.existing, self.added = matches, existing, []

    def execute(self, query):
        return SimpleNamespace(all=lambda: list(self.matches))

    def scalar(self, query):
        return self.existing

    def scalars(self, query):
        return []

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        pass


def test_missing_public_metadata_preserves_reviewed_identity():
    row = service.resolve_row(FakeSession(), uuid.uuid4(), ROW)
    assert row["state"] == "metadata_unavailable" and row["section_id"] is None
    assert row["index_number"] == ROW["index_number"]
    assert service.ready([row])


def test_ambiguous_and_conflicting_matches_never_ready():
    section = SimpleNamespace(id=uuid.uuid4(), index_number="91234", section_number="01")
    course = SimpleNamespace(course_string=ROW["course_string"], supplement_code="")
    ambiguous = service.resolve_row(
        FakeSession([(section, course), (section, course)]), uuid.uuid4(), ROW
    )
    assert ambiguous["state"] == "ambiguous" and not service.ready([ambiguous])
    wrong = service.resolve_row(
        FakeSession([(section, SimpleNamespace(course_string="01:999:999"))]), uuid.uuid4(), ROW
    )
    assert wrong["state"] == "conflict" and not service.ready([wrong])


def test_exact_index_carries_supplement_identity():
    section = SimpleNamespace(id=uuid.uuid4(), index_number="91234", section_number="01")
    course = SimpleNamespace(course_string=ROW["course_string"], supplement_code="LB")
    row = service.resolve_row(FakeSession([(section, course)]), uuid.uuid4(), ROW)
    assert row["resolved_supplement"] == "LB" and row["resolved_index"] == "91234"


def test_different_existing_index_requires_explicit_replace():
    row = service.resolve_row(
        FakeSession(existing=SimpleNamespace(index_number="99999")), uuid.uuid4(), ROW
    )
    assert row["issues"] == ["CONFIRM_REPLACE_EXISTING"]
    reviewed = service.resolve_row(
        FakeSession(existing=SimpleNamespace(index_number="99999")),
        uuid.uuid4(),
        {**ROW, "replace_existing": True},
    )
    assert service.ready([reviewed])


def test_upload_stages_only_and_retains_no_raw_text():
    session = FakeSession()
    student = SimpleNamespace(id=uuid.uuid4())
    items = objects(term="Spring 2099", records=[(70, 600, "01:999:101:01-91234 (3.0)")])
    items.append((40, 755, "PRIVATE_CANARY_NAME"))
    text = "\n".join(item[2] for item in items)
    imp = service.stage(
        session,
        student,
        b"%PDF-synthetic",
        extractor=lambda _: ExtractedText([text], 1, extracted_layout(items)),
        max_bytes=100,
        now=NOW,
    )
    assert [type(item) for item in session.added] == [ScheduleImport]
    assert "PRIVATE_CANARY_NAME" not in repr(imp.rows)
    assert imp.state == "extracted" and imp.rows[0]["extracted"] == ROW
    # Explicit confirmation writes only registration identity, never StudentCourse.
    service.confirm(session, imp, expected_version=1, now=NOW)
    assert [type(item) for item in session.added] == [ScheduleImport, StudentRegistration]
    service.confirm(session, imp, expected_version=1, now=NOW)
    assert len(session.added) == 2


def test_expired_and_stale_review_refuse_before_writes():
    session = FakeSession()
    imp = ScheduleImport(
        student_id=uuid.uuid4(),
        state="extracted",
        version=2,
        expires_at=NOW - timedelta(seconds=1),
        rows=[],
    )
    with pytest.raises(service.ScheduleImportRefused, match="EXPIRED"):
        service.confirm(session, imp, expected_version=2, now=NOW)
    imp.expires_at = NOW + timedelta(days=1)
    with pytest.raises(service.ScheduleImportRefused, match="VERSION_STALE"):
        service.confirm(session, imp, expected_version=1, now=NOW)
    assert session.added == []


def test_oversized_and_non_pdf_never_reach_extractor():
    def forbidden(_):
        raise AssertionError("Extractor must not run")

    for data, limit, code in [
        (b"%PDF-too-big", 2, "FILE_TOO_LARGE"),
        (b"no pdf", 100, "FILE_NOT_PDF"),
    ]:
        with pytest.raises(ExtractionFailed, match=code):
            service.stage(
                FakeSession(),
                SimpleNamespace(id=uuid.uuid4()),
                data,
                extractor=forbidden,
                max_bytes=limit,
                now=NOW,
            )
