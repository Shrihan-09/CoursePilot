"""Phase 7.0 transcript import through the API (real PostgreSQL, SYNTHETIC documents).

upload -> extraction -> normalization -> validation -> STAGING -> review
-> explicit confirmation -> student_course -> fresh audit.

Most tests use the real pypdf extraction IN-PROCESS (fast); one test goes
through the production child-process extractor end to end. Storage is the
in-memory implementation so each test can prove the document was deleted.
"""

from __future__ import annotations

import logging
import uuid

import pytest
from sqlalchemy import func, select

from app.api.security import reset_limiters
from app.models import Student, StudentCourse
from app.models.onboarding import TranscriptImport, TranscriptImportRow
from app.services.transcripts.extract import ExtractedText, ExtractionFailed
from app.services.transcripts.storage import MemoryTranscriptStorage

from .support import onboarding_world
from .support.onboarding_world import statuses
from .support.synthetic_transcript import build_pdf, transcript_lines
from .test_multi_program import _client, _session, requires_db

URL = "/api/v1/student/transcripts"
CANARY = "CANARY7F3A"


def in_process(data: bytes) -> ExtractedText:
    """The child's extraction code, run in this process (fast)."""
    from app.services.transcripts.pdf_worker import _extract

    out = _extract(data, 20, 200_000)
    if out[0] == "error":
        raise ExtractionFailed(out[1])
    return ExtractedText(pages=list(out[1]), page_count=out[2])


@pytest.fixture
def ob():
    with _session() as session:
        world = onboarding_world.build(session)
        world["account"] = onboarding_world.new_account(session)
        world["other"] = onboarding_world.new_account(session)
        yield world


@pytest.fixture
def storage():
    return MemoryTranscriptStorage()


def _app(storage, extractor=in_process):
    from app.api.v1.routes.onboarding import get_transcript_extractor
    from app.main import create_app
    from app.services.transcripts.storage import get_transcript_storage

    app = create_app()
    app.dependency_overrides[get_transcript_storage] = lambda: storage
    if extractor is not None:
        app.dependency_overrides[get_transcript_extractor] = lambda: extractor
    return app


async def _profile(ac, ob, which="pub"):
    r = await ac.post("/api/v1/student/profile",
                      json={"program_key": ob["keys"][which], "catalog_year": ob["year"]})
    assert r.status_code in (200, 201), r.text


def _doc(ob, **kw) -> bytes:
    c = ob["code"]
    courses = kw.pop("courses", None) or {
        "FALL 2024": [f"{c['A']} OB COURSE A 3.0 A", f"{c['B']} OB COURSE B 3.0 B+"],
        "SPRING 2025": [f"{c['C']} OB COURSE C 3.0"]}
    return build_pdf([transcript_lines(courses=courses, **kw)])


async def _upload(ac, data: bytes, code=201):
    r = await ac.post(URL, content=data, headers={"Content-Type": "application/pdf"})
    assert r.status_code == code, r.text
    return r.json()


def _rows(imp) -> dict:
    return {r["extracted"]["course_code"]: r for r in imp["rows"]}


def _record(account_id) -> list[tuple]:
    with _session() as s:
        return sorted((tuple(row) for row in s.execute(
            select(StudentCourse.term_code, StudentCourse.status, StudentCourse.grade,
                   StudentCourse.credit_origin, StudentCourse.source_kind)
            .join(Student).where(Student.user_id == account_id)).all()),
                      key=lambda t: tuple("" if v is None else v for v in t))


# --------------------------------------------------------------------------
# the happy path, and the invariant
# --------------------------------------------------------------------------


@requires_db
async def test_upload_stages_review_confirms_and_only_then_writes(ob, storage) -> None:
    async with _client(_app(storage), ob["account"]) as ac:
        await _profile(ac, ob)
        imp = await _upload(ac, _doc(ob))
        assert imp["state"] == "extracted" and imp["layout_verified"] is False
        assert "unverified" in imp["parser_version"]
        assert imp["transcript_reported_gpa"] == "3.412"
        rows = _rows(imp)
        a, b, c = (rows[ob["code"][n]] for n in "ABC")
        assert (a["state"], a["normalized"]["status"], a["normalized"]["grade"],
                a["normalized"]["term_code"]) == ("matched", "completed", "A", "20249")
        assert a["extracted"]["raw_text"].startswith(ob["code"]["A"])
        # No grade on the line: no status is guessed; the student must say.
        assert c["state"] == "needs_review" and c["normalized"]["status"] is None
        assert {i["code"] for i in c["issues"]} == {"STATUS_MISSING"}
        assert imp["ready"] is False and imp["blocking_rows"] == 1

        # INVARIANT: nothing reached the record, and the document is gone.
        assert _record(ob["account"]) == []
        assert storage.stored_keys() == []
        audit = (await ac.get("/api/v1/student/audit")).json()
        assert (statuses(audit)["REQ_A"], statuses(audit)["REQ_B"]) == ("unsatisfied",
                                                                        "unsatisfied")

        not_ready = await ac.post(f"{URL}/{imp['import_id']}/confirm",
                                  json={"expected_version": imp["version"]})
        assert not_ready.status_code == 422
        assert not_ready.json()["error"]["issues"][0]["code"] == "ROWS_NEED_ATTENTION"
        assert _record(ob["account"]) == []

        edited = await ac.patch(f"{URL}/{imp['import_id']}/rows/{c['row_id']}",
                                json={"expected_version": imp["version"],
                                      "status": "in_progress"})
        assert edited.status_code == 200, edited.text
        imp = edited.json()
        c = _rows(imp)[ob["code"]["C"]]
        assert c["state"] == "matched" and c["edited"] is True
        assert c["extracted"]["grade"] is None          # the evidence is never rewritten
        assert imp["ready"] is True

        done = await ac.post(f"{URL}/{imp['import_id']}/confirm",
                             json={"expected_version": imp["version"]})
        assert done.status_code == 200, done.text
        confirmed = done.json()
        assert confirmed["state"] == "confirmed" and confirmed["rows_inserted"] == 3
        assert {r["outcome"] for r in confirmed["rows"]} == {"inserted"}
        assert _record(ob["account"]) == [
            ("20249", "completed", "A", "rutgers", "student_uploaded_transcript"),
            ("20249", "completed", "B+", "rutgers", "student_uploaded_transcript"),
            ("20251", "in_progress", None, "rutgers", "student_uploaded_transcript")]
        audit = (await ac.get("/api/v1/student/audit")).json()
        assert (statuses(audit)["REQ_A"], statuses(audit)["REQ_B"]) == ("satisfied",
                                                                        "satisfied")
        entries = (await ac.get("/api/v1/student/courses")).json()["entries"]
        assert all(e["from_transcript_import"] for e in entries)
        profile = (await ac.get("/api/v1/student/profile")).json()
        assert profile["transcript_reported_gpa"]["value"] == "3.412"
        assert profile["transcript_reported_gpa"]["source"] == \
            "student_uploaded_unofficial_transcript"


@requires_db
async def test_confirmation_is_idempotent(ob, storage) -> None:
    async with _client(_app(storage), ob["account"]) as ac:
        await _profile(ac, ob)
        c = ob["code"]
        imp = await _upload(ac, _doc(ob, courses={"FALL 2024": [f"{c['A']} X 3.0 A"]}))
        first = await ac.post(f"{URL}/{imp['import_id']}/confirm",
                              json={"expected_version": imp["version"]})
        second = await ac.post(f"{URL}/{imp['import_id']}/confirm",
                               json={"expected_version": imp["version"]})
        assert first.status_code == second.status_code == 200
        assert first.json()["rows"] == second.json()["rows"]
        assert len(_record(ob["account"])) == 1
        # A confirmed import is part of the record; it cannot be edited or discarded.
        row = first.json()["rows"][0]["row_id"]
        assert (await ac.patch(f"{URL}/{imp['import_id']}/rows/{row}",
                               json={"expected_version": 1, "grade": "F"})).status_code == 422
        assert (await ac.delete(f"{URL}/{imp['import_id']}")).status_code == 422


@requires_db
async def test_the_same_document_uploaded_twice_is_one_import(ob, storage) -> None:
    calls = []

    def counting(data):
        calls.append(1)
        return in_process(data)

    async with _client(_app(storage, counting), ob["account"]) as ac:
        await _profile(ac, ob)
        data = _doc(ob)
        first = await _upload(ac, data, 201)
        again = await _upload(ac, data, 200)
        assert again["import_id"] == first["import_id"] and len(calls) == 1
        assert len((await ac.get(URL)).json()["imports"]) == 1


# --------------------------------------------------------------------------
# normalization: unknown, external, conflicts, duplicates
# --------------------------------------------------------------------------


@requires_db
async def test_unrecognized_and_external_rows_stay_reviewable(ob, storage) -> None:
    c = ob["code"]
    data = _doc(ob, courses={"FALL 2024": [f"{c['A']} OB A 3.0 A", "98:000:999 GONE 3.0 B",
                                           f"{c['B']} OB B 3.0 A-"]},
                transfer=[f"{c['D']} COMP WRITING 3.0 TA", "98:000:998 MYSTERY 3.0"],
                exam=[f"{c['E']} AP SOMETHING 4.0"])
    async with _client(_app(storage), ob["account"]) as ac:
        await _profile(ac, ob)
        imp = await _upload(ac, data)
        rows = _rows(imp)
        gone = rows["98:000:999"]
        assert gone["state"] == "unrecognized"
        assert "COURSE_NOT_FOUND" in {i["code"] for i in gone["issues"]}
        minus = rows[c["B"]]
        assert minus["state"] == "needs_review"
        assert minus["extracted"]["grade"] == "A-"           # kept, never "rounded"
        assert "GRADE_UNRECOGNIZED" in {i["code"] for i in minus["issues"]}
        transfer = rows[c["D"]]
        assert transfer["normalized"]["credit_origin"] == "transfer"
        assert transfer["normalized"]["grade"] is None       # never a fabricated Rutgers grade
        assert transfer["extracted"]["grade"] == "TA"
        codes = {i["code"] for i in transfer["issues"]}
        assert {"EXTERNAL_GRADE_NOT_RECORDED", "TERM_MISSING"} <= codes
        mystery = rows["98:000:998"]
        assert mystery["state"] == "unrecognized"
        assert "EXTERNAL_CREDIT_NO_EQUIVALENT" in {i["code"] for i in mystery["issues"]}
        exam = rows[c["E"]]
        assert exam["normalized"]["credit_origin"] == "exam_credit"
        assert len(imp["rows"]) == 6                        # nothing silently dropped

        # Resolve everything explicitly: exclude the unknowns, fix the grade,
        # give external credit its term.
        version = imp["version"]
        for row, change in ((gone, {"excluded": True}), (mystery, {"excluded": True}),
                            (minus, {"grade": "B+"}), (transfer, {"term_code": "20249"}),
                            (exam, {"term_code": "20249"})):
            r = await ac.patch(f"{URL}/{imp['import_id']}/rows/{row['row_id']}",
                               json={"expected_version": version, **change})
            assert r.status_code == 200, r.text
            version = r.json()["version"]
        final = r.json()
        assert final["ready"] is True, [(x["state"], x["issues"]) for x in final["rows"]]
        done = await ac.post(f"{URL}/{imp['import_id']}/confirm",
                             json={"expected_version": version})
        assert done.status_code == 200
        assert (done.json()["rows_inserted"], done.json()["rows_excluded"]) == (4, 2)
        record = _record(ob["account"])
        assert ("20249", "completed", None, "transfer", "student_uploaded_transcript") in record
        assert ("20249", "completed", None, "exam_credit",
                "student_uploaded_transcript") in record


@requires_db
async def test_manual_history_is_never_silently_overwritten(ob, storage) -> None:
    c = ob["code"]
    async with _client(_app(storage), ob["account"]) as ac:
        await _profile(ac, ob)
        manual_same = await ac.post("/api/v1/student/courses", json={
            "course_code": c["A"], "term_code": "20249", "status": "completed", "grade": "A"})
        manual_diff = await ac.post("/api/v1/student/courses", json={
            "course_code": c["B"], "term_code": "20249", "status": "completed", "grade": "C"})
        imp = await _upload(ac, _doc(ob, courses={"FALL 2024": [
            f"{c['A']} OB A 3.0 A", f"{c['B']} OB B 3.0 B+"]}))
        rows = _rows(imp)
        assert rows[c["A"]]["state"] == "matched"
        assert "ALREADY_RECORDED" in {i["code"] for i in rows[c["A"]]["issues"]}
        conflict = rows[c["B"]]
        assert conflict["state"] == "conflict"
        assert conflict["conflicts_with_entry_id"] == manual_diff.json()["entry_id"]
        assert imp["ready"] is False

        keep = await ac.patch(f"{URL}/{imp['import_id']}/rows/{conflict['row_id']}",
                              json={"expected_version": imp["version"],
                                    "resolution": "keep_existing"})
        assert keep.json()["ready"] is True
        done = await ac.post(f"{URL}/{imp['import_id']}/confirm",
                             json={"expected_version": keep.json()["version"]})
        assert (done.json()["rows_inserted"], done.json()["rows_skipped"]) == (0, 2)
        assert _record(ob["account"]) == [
            ("20249", "completed", "A", "rutgers", "student_self_reported"),
            ("20249", "completed", "C", "rutgers", "student_self_reported")]

        # A second document, this time REPLACING the conflicting entry - explicitly.
        imp2 = await _upload(ac, _doc(ob, courses={"FALL 2024": [f"{c['B']} OB B 3.0 B+"]},
                                      cumulative_gpa="3.500"))
        row = imp2["rows"][0]
        r = await ac.patch(f"{URL}/{imp2['import_id']}/rows/{row['row_id']}",
                           json={"expected_version": imp2["version"],
                                 "resolution": "replace_existing"})
        done = await ac.post(f"{URL}/{imp2['import_id']}/confirm",
                             json={"expected_version": r.json()["version"]})
        assert done.json()["rows_replaced"] == 1
        assert done.json()["rows"][0]["entry_id"] == manual_diff.json()["entry_id"]
        assert _record(ob["account"]) == [
            ("20249", "completed", "A", "rutgers", "student_self_reported"),
            ("20249", "completed", "B+", "rutgers", "student_uploaded_transcript")]
        # The newest confirmed document's reported GPA; computed GPA untouched.
        profile = (await ac.get("/api/v1/student/profile")).json()
        assert profile["transcript_reported_gpa"]["value"] == "3.500"
        assert manual_same.status_code == 201


@requires_db
async def test_the_same_course_twice_in_one_term_in_a_document_must_be_resolved(
        ob, storage) -> None:
    c = ob["code"]
    async with _client(_app(storage), ob["account"]) as ac:
        await _profile(ac, ob)
        imp = await _upload(ac, _doc(ob, courses={"FALL 2024": [
            f"{c['A']} OB A 3.0 A", f"{c['A']} OB A AGAIN 3.0 B"],
            "SPRING 2025": [f"{c['A']} OB A RETAKE 3.0 A"]}))
        states = [(r["normalized"]["term_code"], r["state"]) for r in imp["rows"]]
        assert states == [("20249", "needs_review"), ("20249", "needs_review"),
                          ("20251", "matched")]      # a retake in another term is fine
        first = imp["rows"][0]
        r = await ac.patch(f"{URL}/{imp['import_id']}/rows/{first['row_id']}",
                           json={"expected_version": imp["version"], "excluded": True})
        assert r.json()["ready"] is True
        done = await ac.post(f"{URL}/{imp['import_id']}/confirm",
                             json={"expected_version": r.json()["version"]})
        assert done.json()["rows_inserted"] == 2
        assert [t for t, *_ in _record(ob["account"])] == ["20249", "20251"]


# --------------------------------------------------------------------------
# staleness: version, record changes, expiry
# --------------------------------------------------------------------------


@requires_db
async def test_a_stale_review_cannot_be_confirmed(ob, storage) -> None:
    c = ob["code"]
    async with _client(_app(storage), ob["account"]) as ac:
        await _profile(ac, ob)
        imp = await _upload(ac, _doc(ob, courses={"FALL 2024": [f"{c['A']} OB A 3.0 A"]}))
        row = imp["rows"][0]["row_id"]
        await ac.patch(f"{URL}/{imp['import_id']}/rows/{row}",
                       json={"expected_version": imp["version"], "grade": "B"})
        stale = await ac.post(f"{URL}/{imp['import_id']}/confirm",
                              json={"expected_version": imp["version"]})
        assert stale.status_code == 422
        assert stale.json()["error"]["issues"][0]["code"] == "IMPORT_VERSION_STALE"
        old_edit = await ac.patch(f"{URL}/{imp['import_id']}/rows/{row}",
                                  json={"expected_version": imp["version"], "grade": "C"})
        assert old_edit.status_code == 422
        assert _record(ob["account"]) == []

        # The record changes AFTER review: confirmation re-checks and refuses.
        current = (await ac.get(f"{URL}/{imp['import_id']}")).json()
        await ac.post("/api/v1/student/courses", json={
            "course_code": c["A"], "term_code": "20249", "status": "completed", "grade": "F"})
        changed = await ac.post(f"{URL}/{imp['import_id']}/confirm",
                                json={"expected_version": current["version"]})
        assert changed.status_code == 422
        codes = {i["code"] for i in changed.json()["error"]["issues"]}
        assert "RECORD_CHANGED_SINCE_REVIEW" in codes
        after = (await ac.get(f"{URL}/{imp['import_id']}")).json()
        assert after["version"] == current["version"] + 1
        assert after["rows"][0]["state"] == "conflict"
        assert _record(ob["account"]) == [
            ("20249", "completed", "F", "rutgers", "student_self_reported")]


@requires_db
async def test_an_expired_import_cannot_be_confirmed(ob, storage) -> None:
    c = ob["code"]
    async with _client(_app(storage), ob["account"]) as ac:
        await _profile(ac, ob)
        imp = await _upload(ac, _doc(ob, courses={"FALL 2024": [f"{c['A']} OB A 3.0 A"]}))
        with _session() as s:
            row = s.get(TranscriptImport, uuid.UUID(imp["import_id"]))
            row.expires_at = row.created_at
            s.commit()
        r = await ac.post(f"{URL}/{imp['import_id']}/confirm",
                          json={"expected_version": imp["version"]})
        assert r.status_code == 422
        assert r.json()["error"]["issues"][0]["code"] == "IMPORT_EXPIRED"
        assert (await ac.get(f"{URL}/{imp['import_id']}")).json()["state"] == "expired"
        assert _record(ob["account"]) == []
        # The same document can be imported afresh.
        again = await _upload(ac, _doc(ob, courses={"FALL 2024": [f"{c['A']} OB A 3.0 A"]}))
        assert again["import_id"] != imp["import_id"] and again["state"] == "extracted"


@requires_db
async def test_discarding_deletes_the_staged_copy(ob, storage) -> None:
    async with _client(_app(storage), ob["account"]) as ac:
        await _profile(ac, ob)
        imp = await _upload(ac, _doc(ob))
        gone = await ac.delete(f"{URL}/{imp['import_id']}")
        assert gone.status_code == 200 and gone.json()["state"] == "discarded"
        with _session() as s:
            assert s.scalar(select(func.count()).select_from(TranscriptImportRow).where(
                TranscriptImportRow.import_id == uuid.UUID(imp["import_id"]))) == 0
        assert (await ac.post(f"{URL}/{imp['import_id']}/confirm",
                              json={"expected_version": imp["version"]})).status_code == 422


# --------------------------------------------------------------------------
# hostile documents
# --------------------------------------------------------------------------


@requires_db
async def test_hostile_and_unusable_documents_are_refused(ob, storage, settings) -> None:
    async with _client(_app(storage), ob["account"]) as ac:
        await _profile(ac, ob)
        for data, status, code in (
                (b"", 422, "FILE_EMPTY"),
                (b"\x89PNG\r\n\x1a\n" + b"\x00" * 200, 422, "FILE_NOT_PDF"),
                (b"PK\x03\x04" + b"\x00" * 200, 422, "FILE_NOT_PDF"),
                (b"%PDF-1.4\n" + b"0" * (settings.transcript_max_bytes + 1), 413,
                 "FILE_TOO_LARGE")):
            r = await ac.post(URL, content=data, headers={"Content-Type": "application/pdf"})
            assert r.status_code == status, code
            assert r.json()["error"]["issues"][0]["code"] == code
        reset_limiters()      # 4 uploads spent; the limiter has its own test
        # Accepted as a PDF, refused by extraction: a FAILED import, no rows.
        for data, code in ((build_pdf([["x"]], encrypt_marker=True), "PDF_ENCRYPTED"),
                           (build_pdf([[]]), "PDF_NO_TEXT_LAYER"),
                           (b"%PDF-1.4\n" + b"\x00junk" * 99, "PDF_UNREADABLE"),
                           (build_pdf([["NO COURSES HERE"]]), "NO_COURSE_ROWS")):
            imp = await _upload(ac, data)
            assert (imp["state"], imp["failure_code"], imp["rows"]) == ("failed", code, [])
        assert storage.stored_keys() == []
        assert _record(ob["account"]) == []


@requires_db
async def test_a_parser_crash_halfway_leaves_a_failed_import_and_no_history(
        ob, storage, caplog) -> None:
    caplog.set_level(logging.DEBUG)

    def crashing(data):
        in_process(data)
        raise RuntimeError(f"boom {CANARY} 01:198:111 A")

    async with _client(_app(storage, crashing), ob["account"]) as ac:
        await _profile(ac, ob)
        imp = await _upload(ac, _doc(ob))
        assert (imp["state"], imp["failure_code"]) == ("failed", "PDF_EXTRACTION_CRASHED")
        assert CANARY not in str(imp)
    assert storage.stored_keys() == [] and _record(ob["account"]) == []
    # The exception quoted the document; neither its text nor the course reaches a log.
    logged = "\n".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    assert CANARY not in logged and "01:198:111" not in logged


@requires_db
async def test_the_production_extractor_end_to_end(ob, storage) -> None:
    """The real child-process extractor, through the API."""
    async with _client(_app(storage, extractor=None), ob["account"]) as ac:
        await _profile(ac, ob)
        imp = await _upload(ac, _doc(ob))
        assert imp["state"] == "extracted" and len(imp["rows"]) == 3


# --------------------------------------------------------------------------
# ownership and privacy
# --------------------------------------------------------------------------


@requires_db
async def test_another_account_cannot_reach_an_import_or_its_rows(ob, storage) -> None:
    app = _app(storage)
    async with _client(app, ob["account"]) as a:
        await _profile(a, ob)
        imp = await _upload(a, _doc(ob))
    row = imp["rows"][0]["row_id"]
    async with _client(app, ob["other"]) as b:
        assert (await b.post(URL, content=_doc(ob))).status_code == 409   # no profile yet
        await _profile(b, ob)
        assert (await b.get(URL)).json()["imports"] == []
        attempts = [
            await b.get(f"{URL}/{imp['import_id']}"),
            await b.patch(f"{URL}/{imp['import_id']}/rows/{row}",
                          json={"expected_version": 1, "grade": "F"}),
            await b.post(f"{URL}/{imp['import_id']}/confirm", json={"expected_version": 1}),
            await b.delete(f"{URL}/{imp['import_id']}"),
        ]
        missing = await b.get(f"{URL}/{uuid.uuid4()}")
        for r in attempts:
            assert r.status_code == 404
            assert r.json()["detail"] == missing.json()["detail"]
        # Their own import, someone else's row id: still not found.
        mine = await _upload(b, _doc(ob, cumulative_gpa="2.000"))
        cross = await b.patch(f"{URL}/{mine['import_id']}/rows/{row}",
                              json={"expected_version": mine["version"], "grade": "F"})
        assert cross.status_code == 404
    with _session() as s:
        assert s.get(TranscriptImport, uuid.UUID(imp["import_id"])).state == "extracted"
        assert s.get(TranscriptImportRow, uuid.UUID(row)).grade == "A"


@requires_db
async def test_transcript_contents_never_reach_the_logs(ob, storage, caplog) -> None:
    c = ob["code"]
    data = _doc(ob, courses={"FALL 2024": [f"{c['A']} {CANARY} TITLE 3.0 A"]},
                cumulative_gpa="3.141")
    caplog.set_level(logging.DEBUG)
    async with _client(_app(storage), ob["account"]) as ac:
        await _profile(ac, ob)
        imp = await _upload(ac, data)
        await ac.post(f"{URL}/{imp['import_id']}/confirm",
                      json={"expected_version": imp["version"]})
        bad = await ac.post(URL, content=b"%PDF-1.4\n" + CANARY.encode() * 10)
        assert CANARY not in bad.text
    text = "\n".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    for secret in (CANARY, c["A"], "3.141", "TITLE"):
        assert secret not in text, secret


@requires_db
async def test_uploads_have_their_own_rate_limit(ob, storage) -> None:
    calls = []

    def counting(data):
        calls.append(1)
        return in_process(data)

    async with _client(_app(storage, counting), ob["account"]) as ac:
        await _profile(ac, ob)
        statuses_seen = [(await ac.post(URL, content=_doc(ob, cumulative_gpa=f"3.{i}00"))
                          ).status_code for i in range(6)]
    assert statuses_seen[:5] == [201, 201, 201, 422, 422]   # 3 open imports max, then capped
    assert statuses_seen[5] == 429 and len(calls) == 3
