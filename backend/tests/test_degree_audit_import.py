"""Phase 7.4 Degree Navigator audit import through the API (real PostgreSQL, SYNTHETIC PDFs).

upload -> extract (text + status icons) -> parse -> normalize -> STAGING
-> reconciliation -> review -> confirm -> history (transcript path) + DN status

The PDFs come from `support.synthetic_dn` in the real Chrome layout with
invented data (the OB world's subject under unit 98, "TEST STUDENT", RUID
000000000). The mapping table is written to a temporary directory for each
test. Extraction runs in-process; storage is in memory so every test can prove
the document was deleted.
"""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import func, select

from app.models import ProgramVersion, Student, StudentCourse
from app.models.degree_audit import (
    DegreeAuditCourseRow,
    DegreeAuditImport,
    DegreeAuditRequirementRow,
)
from app.models.onboarding import TranscriptImport, TranscriptImportRow
from app.services.degree_audit.pdf_worker import extract_audit_layout
from app.services.transcripts.extract import ExtractedText, ExtractionFailed
from app.services.transcripts.storage import MemoryTranscriptStorage

from .support import onboarding_world
from .support.onboarding_world import statuses
from .support.synthetic_dn import build_audit_pdf, render_pdf
from .test_multi_program import _client, _session, requires_db

URL = "/api/v1/degree-audit-imports"


def in_process(data: bytes) -> ExtractedText:
    out = extract_audit_layout(data, 20, 200_000)
    if out[0] == "error":
        raise ExtractionFailed(out[1])
    return ExtractedText(pages=list(out[1]), page_count=out[2], layout=out[3])


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


SUBJECT = "OB Program PUB (NB)"


@pytest.fixture
def mapping(ob, tmp_path, monkeypatch):
    """A reviewed SYNTHETIC mapping table for the OB world's PUB program."""
    def write(program_key=None, subject=SUBJECT):
        folder = tmp_path / ob["year"] / "dn-mapping"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "ob.json").write_text(json.dumps({
            "mapping_version": "1", "program_key": program_key or ob["keys"]["pub"],
            "scope": "program", "dn_subject": subject, "dn_versions": ["Fall 2026"],
            "requirements": [
                {"dn_heading": "Requirement R1 : Course A Requirement [RA]",
                 "requirement_code": "REQ_A", "review_status": "reviewed"},
                {"dn_heading": "Requirement R2 : Course B Requirement [RB]",
                 "requirement_code": "REQ_B", "review_status": "reviewed"}],
            "goals": []}))
    monkeypatch.setattr("app.services.degree_audit.mapping.mapping_root", lambda: tmp_path)
    write()
    return write


def model(ob, *, r2_courses=None, r2_status="fulfilled", grade_a="A", printed="10/20/26, 9:15 AM"):
    a, b = ob["code"]["A"], ob["code"]["B"]
    r2 = r2_courses if r2_courses is not None else [(b, 3, "Spring 2026", "B+")]
    return {
        "printed_at": printed, "report_date": "October 20 2026", "degree_id": "9997",
        "subject": "OB Program PUB (NB)", "version": "Fall 2026",
        "identity": ("TEST STUDENT", "000000000"),
        "requirements": [
            {"title": "Course A Requirement [RA]", "status": "fulfilled", "total": 1,
             "intro": "1 course from", "join": None,
             "goals": [("Course A Set", "RA", [(a, "completed")])],
             "courses": [(a, 3, "Fall 2025", grade_a)]},
            {"title": "Course B Requirement [RB]", "status": r2_status, "total": 1,
             "intro": "1 course from", "join": None,
             "needs": None if r2_status != "not_fulfilled" else
             "1 more course is needed to satisfy this requirement.",
             "goals": [("Course B Set", "RB", [(c, "completed") for c, *_ in r2])],
             "courses": r2},
            {"title": "Elective Studies [RX]", "status": "not_fulfilled", "total": 1,
             "intro": "1 course from", "join": None,
             "needs": "1 more course is needed to satisfy this requirement.",
             "goals": [("Electives", "RX", [])], "courses": []},
        ],
        "used": [(a, 3, "Fall 2025", grade_a), *r2], "unused": [], "below_minimum": [],
    }


def _app(storage):
    from app.api.v1.routes.degree_audit_imports import get_audit_extractor
    from app.main import create_app
    from app.services.transcripts.storage import get_transcript_storage

    app = create_app()
    app.dependency_overrides[get_transcript_storage] = lambda: storage
    app.dependency_overrides[get_audit_extractor] = lambda: in_process
    return app


async def _profile(ac, ob, which="pub"):
    r = await ac.post("/api/v1/student/profile",
                      json={"program_key": ob["keys"][which], "catalog_year": ob["year"]})
    assert r.status_code in (200, 201), r.text


async def _upload(ac, data: bytes, code=201) -> list[dict]:
    r = await ac.post(URL, content=data, headers={"Content-Type": "application/pdf"})
    assert r.status_code == code, r.text
    return r.json()["imports"]


def _req(imp, number=None, code=None) -> dict:
    return next(r for r in imp["requirements"] if r["kind"] == "requirement"
                and (number is None or r["dn_number"] == number)
                and (code is None or r["requirement_code"] == code))


def _record(account_id) -> list[tuple]:
    with _session() as s:
        return sorted(tuple(r) for r in s.execute(
            select(StudentCourse.term_code, StudentCourse.status, StudentCourse.grade)
            .join(Student).where(Student.user_id == account_id)).all())


async def _confirm(ac, imp, code=200) -> dict:
    r = await ac.post(f"{URL}/{imp['id']}/confirm", json={"expected_version": imp["version"]})
    assert r.status_code == code, r.text
    return r.json()


# --------------------------------------------------------------------------
# staging: nothing reaches the record before confirmation
# --------------------------------------------------------------------------


@requires_db
async def test_upload_stages_maps_and_reconciles_but_writes_nothing(ob, storage, mapping):
    async with _client(_app(storage), ob["account"]) as ac:
        await _profile(ac, ob)
        [imp] = await _upload(ac, build_audit_pdf(model(ob)))
        assert imp["state"] == "extracted" and imp["block_count"] == 1
        assert imp["track_check"] == "matches_track" and imp["mapping_key"] == ob["keys"]["pub"]
        assert (imp["as_of_source"], imp["dn_subject"], imp["dn_version_label"]) == (
            "print_header", "OB Program PUB (NB)", "Fall 2026")
        r1, r2, r3 = (_req(imp, n) for n in (1, 2, 3))
        assert (r1["requirement_code"], r1["dn_status"], r1["state"]) == (
            "REQ_A", "satisfied", "matched")
        assert (r3["requirement_code"], r3["state"], r3["reconciliation"]) == (
            None, "unmapped", "unmapped")
        # Nothing recorded yet, so CoursePilot's own audit does not agree - shown.
        assert r1["reconciliation"] == "dn_satisfied_cp_not" and r1["needs_review"]
        history = {h["course_code"]: h for h in imp["history"]}
        assert set(history) == {ob["code"]["A"], ob["code"]["B"]}
        assert (history[ob["code"]["A"]]["status"], history[ob["code"]["A"]]["grade"],
                history[ob["code"]["A"]]["term_code"]) == ("completed", "A", "20259")
        assert imp["readiness"]["ready"] is True

        # INVARIANT: no history written, and the document is gone.
        assert _record(ob["account"]) == []
        assert storage.stored_keys() == []
        audit = (await ac.get("/api/v1/student/audit")).json()
        assert statuses(audit)["REQ_A"] == "unsatisfied"


@requires_db
async def test_the_identity_block_is_never_stored(ob, storage, mapping):
    async with _client(_app(storage), ob["account"]) as ac:
        await _profile(ac, ob)
        [imp] = await _upload(ac, build_audit_pdf(model(ob)))
    with _session() as s:
        texts = []
        for table in (DegreeAuditImport, DegreeAuditRequirementRow, DegreeAuditCourseRow,
                      TranscriptImport, TranscriptImportRow):
            for row in s.scalars(select(table)):
                texts += [str(v) for v in row.__dict__.values()]
        blob = " ".join(texts)
    assert "TEST STUDENT" not in blob and "000000000" not in blob
    assert "TEST STUDENT" not in json.dumps(imp) and "000000000" not in json.dumps(imp)


# --------------------------------------------------------------------------
# confirmation: one transaction, idempotent, through the transcript path
# --------------------------------------------------------------------------


@requires_db
async def test_confirm_writes_history_and_status_and_is_idempotent(ob, storage, mapping):
    async with _client(_app(storage), ob["account"]) as ac:
        await _profile(ac, ob)
        [imp] = await _upload(ac, build_audit_pdf(model(ob)))
        done = await _confirm(ac, imp)
        assert done["state"] == "confirmed"
        assert done["history_outcome"]["inserted"] == 2
        assert _record(ob["account"]) == [("20259", "completed", "A"),
                                          ("20261", "completed", "B+")]
        # The record changed, so the reconciliation was redone: now they agree.
        assert {_req(done, n)["reconciliation"] for n in (1, 2)} == {"agrees"}
        with _session() as s:
            sources = set(s.scalars(select(TranscriptImport.source_document).join(
                StudentCourse, StudentCourse.transcript_import_id == TranscriptImport.id)))
        assert sources == {"degree_navigator_audit"}

        again = await _confirm(ac, done)                      # same version: no-op
        assert again["state"] == "confirmed" and len(_record(ob["account"])) == 2

        # The same document again opens a NEW import; every course is already
        # recorded identically, so confirming it inserts nothing.
        [second] = await _upload(ac, build_audit_pdf(model(ob)))
        assert {h["state"] for h in second["history"]} == {"matched"}
        assert all(any(i["code"] == "ALREADY_RECORDED" for i in h["issues"])
                   for h in second["history"])
        final = await _confirm(ac, second)
        assert final["history_outcome"] == {"inserted": 0, "replaced": 0, "skipped": 2,
                                            "excluded": 0}
        assert len(_record(ob["account"])) == 2
        listing = (await ac.get(URL)).json()["imports"]
        older = next(i for i in listing if i["id"] == imp["id"])
        assert older["superseded_at"] is not None          # the newest is the record

        # The transcript API never sees the DN-owned course staging.
        transcripts = (await ac.get("/api/v1/student/transcripts")).json()["imports"]
        assert transcripts == []


@requires_db
async def test_a_course_that_disagrees_with_the_record_is_a_conflict_the_student_resolves(
        ob, storage, mapping):
    async with _client(_app(storage), ob["account"]) as ac:
        await _profile(ac, ob)
        entry = await ac.post("/api/v1/student/courses", json={
            "course_code": ob["code"]["A"], "term_code": "20259", "status": "completed",
            "grade": "C"})
        assert entry.status_code == 201, entry.text
        [imp] = await _upload(ac, build_audit_pdf(model(ob)))
        row = next(h for h in imp["history"] if h["course_code"] == ob["code"]["A"])
        assert row["state"] == "conflict" and row["resolution"] is None
        refused = await _confirm(ac, imp, code=422)
        assert refused["error"]["issues"][0]["code"] == "ROWS_NEED_ATTENTION"
        assert ("20259", "completed", "C") in _record(ob["account"])

        resolved = await ac.patch(f"{URL}/{imp['id']}/rows/{row['id']}", json={
            "expected_version": imp["version"], "resolution": "replace_existing"})
        assert resolved.status_code == 200, resolved.text
        imp = resolved.json()
        assert imp["version"] > 1 and imp["readiness"]["ready"] is True
        done = await _confirm(ac, imp)
        assert done["history_outcome"]["replaced"] == 1
        assert ("20259", "completed", "A") in _record(ob["account"])
        assert ("20259", "completed", "C") not in _record(ob["account"])


# --------------------------------------------------------------------------
# DN status drives planning; CoursePilot's rules never change
# --------------------------------------------------------------------------


@requires_db
async def test_confirmed_dn_status_overrides_coursepilot_for_planning(ob, storage, mapping):
    """DN says R2 (REQ_B) is fulfilled by course C - an equivalency CoursePilot
    cannot see (C is not an option of REQ_B). After confirmation planning
    treats REQ_B as satisfied, the disagreement is NEEDS REVIEW, and
    CoursePilot's own audit (the rules) still says unsatisfied."""
    from app.services.degree_audit.overlay import (
        DISAGREE_LABEL,
        OverlaidAuditEngine,
        active_overlay,
    )
    from app.services.audit.engine import DegreeAuditEngine
    from app.services.planning.engine import PlanningEngine, active_leaves
    from app.services.planning.service import degree_navigator_overlay

    c = ob["code"]["C"]
    async with _client(_app(storage), ob["account"]) as ac:
        await _profile(ac, ob)
        [imp] = await _upload(ac, build_audit_pdf(model(ob, r2_courses=[
            (c, 3, "Spring 2026", "A")])))
        done = await _confirm(ac, imp)
        r2 = _req(done, 2)
        assert (r2["reconciliation"], r2["cp_status"], r2["needs_review"]) == (
            "dn_satisfied_cp_not", "unsatisfied", True)
        assert r2["label"] == DISAGREE_LABEL
        progress = (await ac.get(f"{URL}/progress")).json()
        assert progress["source"] == "degree_navigator"
        assert next(r for r in progress["requirements"]
                    if r["requirement_code"] == "REQ_B")["needs_review"] is True
        audit = (await ac.get("/api/v1/student/audit")).json()
        assert statuses(audit)["REQ_B"] == "unsatisfied"       # the rules are unchanged

    with _session() as s:
        student = s.scalar(select(Student).where(Student.user_id == ob["account"]))
        overlay = active_overlay(s, student)
        assert overlay.statuses["REQ_B"].planning_status == "satisfied"
        own = DegreeAuditEngine(s).audit(student)
        used = OverlaidAuditEngine(s, overlay).audit(student)
        assert statuses(own)["REQ_B"] == "unsatisfied"
        assert statuses(used)["REQ_B"] == "satisfied"
        node = next(n for root in used.requirements for n in [root, *root.children]
                    if n.requirement_code == "REQ_B")
        assert "Degree Navigator" in node.reason and DISAGREE_LABEL in node.reason
        assert "REQ_B" not in {n.requirement_code for n in active_leaves(used.requirements)}
        assert "REQ_B" in {n.requirement_code for n in active_leaves(own.requirements)}
        engine = PlanningEngine(s, None, overlay)
        assert isinstance(engine.audit_engine, OverlaidAuditEngine)
        version = s.get(ProgramVersion, student.program_version_id)
        _, notes = degree_navigator_overlay(s, student, version, ob["keys"]["pub"])
        assert any(n.code == "degree_navigator_disagreement" and "NEEDS REVIEW" in n.message
                   for n in notes)


@requires_db
async def test_without_an_import_requirement_status_is_estimated(ob, storage, mapping):
    from app.services.planning.service import degree_navigator_overlay

    async with _client(_app(storage), ob["account"]) as ac:
        await _profile(ac, ob)
        progress = (await ac.get(f"{URL}/progress")).json()
        assert progress["source"] == "coursepilot_estimate"
        assert progress["label"].startswith("Estimated")
    with _session() as s:
        student = s.scalar(select(Student).where(Student.user_id == ob["account"]))
        overlay, notes = degree_navigator_overlay(s, student, s.get(ProgramVersion, student.program_version_id),
                                                  ob["keys"]["pub"])
        assert overlay is None and notes[0].code == "requirement_status_estimated"


@requires_db
async def test_an_unmapped_requirement_is_kept_shown_and_never_used(ob, storage, mapping):
    from app.services.degree_audit.overlay import active_overlay

    async with _client(_app(storage), ob["account"]) as ac:
        await _profile(ac, ob)
        [imp] = await _upload(ac, build_audit_pdf(model(ob)))
        r3 = _req(imp, 3)
        assert (r3["state"], r3["requirement_code"], r3["reconciliation"]) == (
            "unmapped", None, "unmapped")
        assert any(i["code"] == "DN_REQUIREMENT_UNMAPPED" for i in r3["issues"])
        assert r3["dn_heading"] == "Requirement R3 : Elective Studies [RX]"
        done = await _confirm(ac, imp)                  # unmapped never blocks
        assert done["state"] == "confirmed"
    with _session() as s:
        student = s.scalar(select(Student).where(Student.user_id == ob["account"]))
        assert set(active_overlay(s, student).statuses) == {"REQ_A", "REQ_B"}


# --------------------------------------------------------------------------
# refusals and boundaries
# --------------------------------------------------------------------------


@requires_db
async def test_an_audit_for_another_program_blocks_confirmation(ob, storage, mapping):
    mapping(program_key=ob["keys"]["pend"])            # the table says: program PEND
    async with _client(_app(storage), ob["account"]) as ac:
        await _profile(ac, ob)
        [imp] = await _upload(ac, build_audit_pdf(model(ob)))
        assert imp["track_check"] == "mismatch"
        assert "your CoursePilot track is" in imp["track_message"]
        assert imp["readiness"]["ready"] is False
        refused = await _confirm(ac, imp, code=422)
        assert refused["error"]["issues"][0]["code"] == "DN_TRACK_MISMATCH"
        assert _record(ob["account"]) == []


@requires_db
async def test_an_image_only_pdf_is_refused_with_the_chrome_instruction(ob, storage, mapping):
    pages = [{"lines": [], "icons": [(40, 700, "completed")]}]
    async with _client(_app(storage), ob["account"]) as ac:
        await _profile(ac, ob)
        [imp] = await _upload(ac, render_pdf(pages))
        assert (imp["state"], imp["failure_code"]) == ("failed", "PDF_NO_TEXT_LAYER")
        assert imp["failure_message"] == ("This PDF has no text. In Chrome, use Print → "
                                          "Destination: Save as PDF (not Microsoft Print to "
                                          "PDF).")
        assert storage.stored_keys() == []


@requires_db
async def test_each_degree_block_is_its_own_import(ob, storage, mapping):
    other = model(ob)
    other["subject"], other["degree_id"] = "Unmapped Studies Minor (NB)", "9996"
    async with _client(_app(storage), ob["account"]) as ac:
        await _profile(ac, ob)
        imports = await _upload(ac, build_audit_pdf(model(ob), extra=[other]))
        assert [(i["block_index"], i["block_count"]) for i in imports] == [(0, 2), (1, 2)]
        assert [i["track_check"] for i in imports] == ["matches_track", "no_mapping"]
        assert {r["state"] for r in imports[1]["requirements"]} == {"unmapped"}
        again = await _upload(ac, build_audit_pdf(model(ob), extra=[other]), code=200)
        assert [i["id"] for i in again] == [i["id"] for i in imports]


@requires_db
async def test_another_students_import_is_not_found(ob, storage, mapping):
    async with _client(_app(storage), ob["account"]) as ac:
        await _profile(ac, ob)
        [imp] = await _upload(ac, build_audit_pdf(model(ob)))
    async with _client(_app(storage), ob["other"]) as ac:
        await _profile(ac, ob)
        assert (await ac.get(f"{URL}/{imp['id']}")).status_code == 404
        r = await ac.post(f"{URL}/{imp['id']}/confirm", json={"expected_version": 1})
        assert r.status_code == 404
        assert (await ac.get(f"{URL}/{uuid.uuid4()}")).status_code == 404


@requires_db
async def test_a_flipped_status_icon_needs_review_and_the_student_resolves_it(
        ob, storage, mapping):
    """scripts/inject_dn_failures.py: the flipped marker becomes NEEDS REVIEW."""
    import importlib.util
    import pathlib

    spec = importlib.util.spec_from_file_location(
        "inject_dn_failures",
        pathlib.Path(__file__).resolve().parents[2] / "scripts" / "inject_dn_failures.py")
    inject = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(inject)
    from .support.synthetic_dn import audit_layout

    damaged = inject.flipped_marker(audit_layout(model(ob, r2_status="not_fulfilled",
                                                       r2_courses=[])), number=2)
    async with _client(_app(storage), ob["account"]) as ac:
        await _profile(ac, ob)
        [imp] = await _upload(ac, render_pdf(damaged))
        r2 = _req(imp, 2)
        assert r2["state"] == "needs_review"
        assert {"STATUS_TEXT_MISMATCH"} <= {i["code"] for i in r2["issues"]}
        assert imp["readiness"]["ready"] is False
        fixed = await ac.patch(f"{URL}/{imp['id']}/rows/{r2['id']}", json={
            "expected_version": imp["version"], "dn_status": "not_satisfied"})
        assert fixed.status_code == 200, fixed.text
        imp = fixed.json()
        r2 = _req(imp, 2)
        # Every row carries the document-level count mismatch; each is resolved
        # by the student's look (exclude or state).
        for row in [r for r in imp["requirements"] if r["state"] == "needs_review"]:
            imp = (await ac.patch(f"{URL}/{imp['id']}/rows/{row['id']}", json={
                "expected_version": imp["version"], "excluded": True})).json()
        assert imp["readiness"]["ready"] is True
        assert _req(imp, 2)["dn_status"] == "not_satisfied"
        await _confirm(ac, imp)
    with _session() as s:
        assert s.scalar(select(func.count()).select_from(DegreeAuditImport)
                        .where(DegreeAuditImport.state == "confirmed",
                               DegreeAuditImport.id == uuid.UUID(imp["id"]))) == 1
