"""Phase 7.0 onboarding API: profile, program selection, manual history (real PostgreSQL).

Every test starts from a brand-new account with NO academic record - the
state a real user is in after their first sign-in - and goes through the
public API only. The catalog is SYNTHETIC (`tests/support/onboarding_world.py`).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from app.models import Student, StudentAuditCache, StudentCourse, StudentLinkEvent

from .support import onboarding_world
from .support.onboarding_world import statuses
from .test_multi_program import _client, _session, requires_db

PROFILE = "/api/v1/student/profile"
COURSES = "/api/v1/student/courses"


@pytest.fixture
def ob():
    with _session() as session:
        world = onboarding_world.build(session)
        world["new_account"] = lambda: onboarding_world.new_account(session)
        world["account"] = onboarding_world.new_account(session)
        world["other"] = onboarding_world.new_account(session)
        yield world


def _app():
    from app.main import create_app

    return create_app()


async def _profile(ac, ob, which="pub"):
    r = await ac.post(PROFILE, json={"program_key": ob["keys"][which],
                                     "catalog_year": ob["year"]})
    assert r.status_code in (200, 201), r.text
    return r


def _issue_codes(response) -> list[str]:
    return [i["code"] for i in response.json()["error"]["issues"]]


# --------------------------------------------------------------------------
# profile
# --------------------------------------------------------------------------


@requires_db
async def test_a_new_account_is_told_to_create_a_profile(ob) -> None:
    async with _client(_app(), ob["account"]) as ac:
        status = await ac.get("/api/v1/student/onboarding")
        assert status.status_code == 200
        assert status.json() == {"state": "needs_profile", "profile": None,
                                 "history_counts": {}, "open_imports": [],
                                 "next_steps": ["create_profile"]}
        context = await ac.get("/api/v1/student/context")
        assert context.status_code == 409
        body = context.json()
        assert body["error"]["code"] == "unlinked_account"
        assert body["detail"].startswith("No academic record is linked to this account.")
        assert "/student/profile" in body["detail"]
        assert (await ac.get(PROFILE)).status_code == 409
        assert (await ac.get(COURSES)).status_code == 409


@requires_db
async def test_profile_creation_is_owned_idempotent_and_audited(ob) -> None:
    async with _client(_app(), ob["account"]) as ac:
        first = await _profile(ac, ob)
        assert first.status_code == 201 and first.json()["created"] is True
        program = first.json()["profile"]["program"]
        assert program["program_key"] == ob["keys"]["pub"]
        assert program["catalog_year"] == "2026-2027"
        # Phase 7.2.2: a legacy-curated publication is disclosed as such - it was
        # never recorded as human-reviewed (the world publishes PUB that way).
        assert program["support_status"] == "supported"
        assert [w["code"] for w in program["warnings"]] == ["legacy_curated_requirements"]
        # A retry - even naming another program - returns the record unchanged.
        again = await _profile(ac, ob, "pend")
        assert again.status_code == 200 and again.json()["created"] is False
        assert again.json()["profile"]["program"]["program_key"] == ob["keys"]["pub"]
        for path in ("/api/v1/student/context", "/api/v1/student/audit"):
            assert (await ac.get(path)).status_code == 200, path
    with _session() as s:
        [student] = s.scalars(select(Student).where(Student.user_id == ob["account"])).all()
        assert student.external_ref is None and student.program_version_id == ob["v_pub"]
        events = s.scalars(select(StudentLinkEvent).where(
            StudentLinkEvent.student_id == student.id)).all()
        assert [(e.action, e.performed_by_id) for e in events] == [("created", ob["account"])]


@requires_db
async def test_the_body_can_never_name_an_owner_or_a_record(ob) -> None:
    with _session() as s:
        victim = Student(external_ref=f"victim-{uuid.uuid4()}", catalog_year="2026-2027",
                         program_version_id=ob["v_pub"], user_id=None)
        s.add(victim)
        s.commit()
        victim_id, victim_ref = victim.id, victim.external_ref
    async with _client(_app(), ob["account"]) as ac:
        for extra in ({"student_id": str(victim_id)}, {"external_ref": victim_ref},
                      {"user_id": str(ob["other"])}, {"account_id": str(ob["other"])}):
            r = await ac.post(PROFILE, json={"program_key": ob["keys"]["pub"],
                                             "catalog_year": ob["year"], **extra})
            assert r.status_code == 422, extra
        await _profile(ac, ob)
    with _session() as s:
        assert s.get(Student, victim_id).user_id is None      # never claimed
        assert s.scalar(select(func.count()).select_from(Student).where(
            Student.user_id == ob["account"])) == 1


@requires_db
async def test_program_selection_is_validated_against_real_program_data(ob) -> None:
    async with _client(_app(), ob["account"]) as ac:
        cases = [
            ({"program_key": "sas-000000-ba", "catalog_year": "2026-2027"}, 404,
             "PROGRAM_NOT_FOUND"),
            ({"program_key": ob["keys"]["old"], "catalog_year": "2026-2027"}, 404,
             "CATALOG_YEAR_UNAVAILABLE"),
            ({"program_key": ob["keys"]["empty"], "catalog_year": "2026-2027"}, 422,
             "PROGRAM_NOT_EVALUABLE"),
        ]
        for body, code, issue in cases:
            r = await ac.post(PROFILE, json=body)
            assert r.status_code == code and _issue_codes(r) == [issue], body
        # Never "the latest year" or "the closest year".
        r = await ac.post(PROFILE, json={"program_key": ob["keys"]["old"],
                                         "catalog_year": "2026-2027"})
        assert "2025-2026" in r.json()["error"]["issues"][0]["message"]
        for bad in ({"program_key": "Robert'); DROP TABLE student;--", "catalog_year": "x"},
                    {"program_key": ob["keys"]["pub"]}, {"catalog_year": "2026-2027"},
                    {"program_key": ob["keys"]["pub"], "catalog_year": "2026"}):
            assert (await ac.post(PROFILE, json=bad)).status_code == 422, bad
        assert (await ac.get("/api/v1/student/onboarding")).json()["state"] == "needs_profile"


@requires_db
async def test_a_pending_review_program_is_selectable_but_always_flagged(ob) -> None:
    async with _client(_app(), ob["account"]) as ac:
        r = await _profile(ac, ob, "pend")
        program = r.json()["profile"]["program"]
        assert program["support_status"] == "pending_review"
        assert program["lifecycle_state"] == "validated"
        assert {w["code"] for w in program["warnings"]} == {
            "requirements_pending_review", "school_requirements_not_included"}
        # The student's OWN program: the audit says so too (Phase 7.0 fix).
        audit = (await ac.get("/api/v1/student/audit")).json()
        assert "program_pending_review" in {f["code"] for f in audit["findings"]}
        own = await ac.post("/api/v1/student/scenarios/audit",
                            json={"program_key": ob["keys"]["pend"]})
        assert own.status_code == 200, own.text
        assert "requirements_pending_review" in {a["code"] for a in own.json()["assumptions"]}


@requires_db
async def test_changing_program_keeps_courses_and_refreshes_the_audit(ob) -> None:
    async with _client(_app(), ob["account"]) as ac:
        await _profile(ac, ob)
        await ac.post(COURSES, json={"course_code": ob["code"]["C"], "term_code": "20249",
                                     "status": "completed", "grade": "A"})
        before = (await ac.get("/api/v1/student/audit")).json()
        assert before["program_code"].startswith("PUB")
        moved = await ac.put("/api/v1/student/program",
                             json={"program_key": ob["keys"]["pend"], "catalog_year": ob["year"]})
        assert moved.status_code == 200
        assert moved.json()["program"]["program_key"] == ob["keys"]["pend"]
        after = (await ac.get("/api/v1/student/audit")).json()
        assert after["program_code"].startswith("PEND")
        [req_c] = [r for r in after["requirements"] if r["requirement_code"] == "REQ_C"]
        assert req_c["status"] == "satisfied"
        assert len((await ac.get(COURSES)).json()["entries"]) == 1
        bad = await ac.put("/api/v1/student/program",
                           json={"program_key": ob["keys"]["empty"], "catalog_year": ob["year"]})
        assert bad.status_code == 422
        assert (await ac.get(PROFILE)).json()["program"]["program_key"] == ob["keys"]["pend"]


# --------------------------------------------------------------------------
# manual history
# --------------------------------------------------------------------------


@requires_db
async def test_history_entries_round_trip_and_feed_the_engine(ob) -> None:
    async with _client(_app(), ob["account"]) as ac:
        await _profile(ac, ob)
        first = (await ac.get("/api/v1/student/audit")).json()
        assert {k: v for k, v in statuses(first).items() if k in ("REQ_A", "REQ_B")} == {
            "REQ_A": "unsatisfied", "REQ_B": "unsatisfied"}

        a = await ac.post(COURSES, json={"course_code": ob["code"]["A"], "term_code": "20249",
                                         "status": "completed", "grade": " b+ ",
                                         "credits_earned": "3"})
        assert a.status_code == 201, a.text
        entry = a.json()
        assert {k: entry[k] for k in ("course_code", "term_code", "status", "grade",
                                      "credits_earned", "credit_origin", "source_kind",
                                      "from_transcript_import")} == {
            "course_code": ob["code"]["A"], "term_code": "20249", "status": "completed",
            "grade": "B+", "credits_earned": "3.0", "credit_origin": "rutgers",
            "source_kind": "student_self_reported", "from_transcript_import": False}
        b = await ac.post(COURSES, json={"course_code": ob["code"]["B"], "term_code": "20261",
                                         "status": "in_progress"})
        assert b.status_code == 201
        audit = (await ac.get("/api/v1/student/audit")).json()
        children = statuses(audit)
        assert children["REQ_A"] == "satisfied"
        assert children["REQ_B"] == "provisionally_satisfied"

        # Edit: the in-progress course is now complete. Then remove A.
        done = await ac.patch(f"{COURSES}/{b.json()['entry_id']}",
                              json={"status": "completed", "grade": "A"})
        assert done.status_code == 200 and done.json()["grade"] == "A"
        assert (await ac.delete(f"{COURSES}/{entry['entry_id']}")).status_code == 204
        assert (await ac.delete(f"{COURSES}/{entry['entry_id']}")).status_code == 404
        final = (await ac.get("/api/v1/student/audit")).json()
        children = statuses(final)
        assert (children["REQ_A"], children["REQ_B"]) == ("unsatisfied", "satisfied")
        listed = (await ac.get(COURSES)).json()["entries"]
        assert [(e["course_code"], e["status"]) for e in listed] == [(ob["code"]["B"],
                                                                      "completed")]


@requires_db
async def test_retakes_are_separate_entries_and_same_term_duplicates_are_refused(ob) -> None:
    async with _client(_app(), ob["account"]) as ac:
        await _profile(ac, ob)
        first = await ac.post(COURSES, json={"course_code": ob["code"]["A"],
                                             "term_code": "20249", "status": "completed",
                                             "grade": "F"})
        retake = await ac.post(COURSES, json={"course_code": ob["code"]["A"],
                                              "term_code": "20251", "status": "completed",
                                              "grade": "B"})
        assert first.status_code == retake.status_code == 201
        dup = await ac.post(COURSES, json={"course_code": ob["code"]["A"],
                                           "term_code": "20251", "status": "completed",
                                           "grade": "A"})
        assert dup.status_code == 422 and _issue_codes(dup) == ["DUPLICATE_ENTRY"]
        moved = await ac.patch(f"{COURSES}/{first.json()['entry_id']}",
                               json={"term_code": "20251"})
        assert moved.status_code == 422 and _issue_codes(moved) == ["DUPLICATE_ENTRY"]
        audit = (await ac.get("/api/v1/student/audit")).json()
        assert "repeated_course" in {f["code"] for f in audit["findings"]}
        assert statuses(audit)["REQ_A"] == "satisfied"
    with _session() as s:
        assert s.scalar(select(func.count()).select_from(StudentCourse).join(Student).where(
            Student.user_id == ob["account"])) == 2


@requires_db
async def test_invalid_entries_are_refused_with_structured_issues(ob) -> None:
    async with _client(_app(), ob["account"]) as ac:
        await _profile(ac, ob)
        cases = [
            ({"course_code": ob["code"]["A"], "term_code": "20249", "status": "completed",
              "grade": "A-"}, ["GRADE_UNRECOGNIZED"]),
            ({"course_code": ob["code"]["A"], "term_code": "20249", "status": "completed"},
             ["GRADE_MISSING"]),
            ({"course_code": ob["code"]["A"], "term_code": "Fall 2024", "status": "planned"},
             ["TERM_INVALID"]),
            ({"course_code": ob["code"]["A"], "term_code": "20249", "status": "completed",
              "credit_origin": "transfer", "grade": "A"}, ["GRADE_NOT_ALLOWED"]),
            ({"course_code": "98:000:000", "term_code": "20249", "status": "planned"},
             ["COURSE_NOT_FOUND"]),
            ({"course_code": "CS 111", "term_code": "20249", "status": "planned"},
             ["COURSE_CODE_INVALID"]),
            ({"course_code": ob["code"]["A"], "term_code": "20249", "status": "planned",
              "credits_earned": "-3"}, ["CREDITS_INVALID"]),
        ]
        for body, codes in cases:
            r = await ac.post(COURSES, json=body)
            assert r.status_code == 422, body
            assert _issue_codes(r) == codes, (body, r.json())
            assert r.json()["error"]["code"] == "validation_error"
            for issue in r.json()["error"]["issues"]:
                assert set(issue) == {"code", "field", "severity", "message"}
        # The supplement-only course resolves to its one registration record.
        lab = await ac.post(COURSES, json={"course_code": ob["code"]["F"], "term_code": "20249",
                                           "status": "planned"})
        assert lab.status_code == 201 and lab.json()["supplement_code"] == "LB"
        assert (await ac.post(COURSES, json={"course_code": ob["code"]["A"], "status": "planned",
                                             "term_code": "20249", "student_id": "x"})
                ).status_code == 422
        assert len((await ac.get(COURSES)).json()["entries"]) == 1


@requires_db
async def test_external_credit_is_recorded_without_a_grade(ob) -> None:
    async with _client(_app(), ob["account"]) as ac:
        await _profile(ac, ob)
        ap = await ac.post(COURSES, json={"course_code": ob["code"]["A"], "term_code": "20249",
                                          "status": "completed", "credit_origin": "exam_credit",
                                          "credits_earned": "3"})
        tr = await ac.post(COURSES, json={"course_code": ob["code"]["B"], "term_code": "20249",
                                          "status": "completed", "credit_origin": "transfer"})
        assert ap.status_code == tr.status_code == 201
        assert (ap.json()["grade"], ap.json()["credit_origin"]) == (None, "exam_credit")
        assert (tr.json()["grade"], tr.json()["credit_origin"]) == (None, "transfer")
        audit = (await ac.get("/api/v1/student/audit")).json()
        children = statuses(audit)
        assert (children["REQ_A"], children["REQ_B"]) == ("satisfied", "satisfied")


@requires_db
async def test_one_account_cannot_see_or_touch_anothers_history(ob) -> None:
    app = _app()
    async with _client(app, ob["account"]) as a:
        await _profile(a, ob)
        entry = (await a.post(COURSES, json={"course_code": ob["code"]["A"],
                                             "term_code": "20249", "status": "completed",
                                             "grade": "A"})).json()["entry_id"]
    async with _client(app, ob["other"]) as b:
        await _profile(b, ob)
        assert (await b.get(COURSES)).json()["entries"] == []
        theirs = await b.patch(f"{COURSES}/{entry}", json={"grade": "F"})
        missing = await b.patch(f"{COURSES}/{uuid.uuid4()}", json={"grade": "F"})
        assert theirs.status_code == missing.status_code == 404
        assert theirs.json()["detail"] == missing.json()["detail"]
        assert theirs.json()["error"]["code"] == missing.json()["error"]["code"]
        assert (await b.delete(f"{COURSES}/{entry}")).status_code == 404
    with _session() as s:
        row = s.get(StudentCourse, uuid.UUID(entry))
        assert row is not None and row.grade == "A"


@requires_db
async def test_writes_drop_the_cached_audit_in_the_same_transaction(ob) -> None:
    async with _client(_app(), ob["account"]) as ac:
        await _profile(ac, ob)
        await ac.get("/api/v1/student/audit")
        with _session() as s:
            sid = s.scalar(select(Student.id).where(Student.user_id == ob["account"]))
            assert s.get(StudentAuditCache, sid) is not None
        await ac.post(COURSES, json={"course_code": ob["code"]["A"], "term_code": "20249",
                                     "status": "planned"})
        with _session() as s:
            assert s.get(StudentAuditCache, sid) is None


@requires_db
async def test_onboarding_walks_to_complete(ob) -> None:
    async with _client(_app(), ob["account"]) as ac:
        await _profile(ac, ob)
        st = (await ac.get("/api/v1/student/onboarding")).json()
        assert st["state"] == "needs_academic_history"
        assert st["history_counts"] == {"completed": 0, "in_progress": 0, "planned": 0}
        await ac.post(COURSES, json={"course_code": ob["code"]["A"], "term_code": "20249",
                                     "status": "completed", "grade": "A"})
        assert (await ac.get("/api/v1/student/onboarding")).json()["state"] == "ready_to_complete"
        done = await ac.post("/api/v1/student/onboarding/complete")
        assert done.status_code == 200 and done.json()["onboarding_completed_at"]
        again = await ac.post("/api/v1/student/onboarding/complete")
        assert again.json()["onboarding_completed_at"] == done.json()["onboarding_completed_at"]
        assert (await ac.get("/api/v1/student/onboarding")).json()["state"] == "complete"
