"""The CS introductory transition (owner decision 2026-11-12; Prompt 08 B1 follow-up).

World: REAL public SOC records for Fall 2026 and Spring 2027
(fixtures/soc_program_expansion_terms.json: 01:198:111/112/120/121/205/211,
Precalculus 01:640:112 ...), the real CS B.S. encoding, and the department
course transitions (data/programs/rutgers/nb-undergrad/course-transitions.json).
Students are FABRICATED; the clock is frozen in October 2026.

One curriculum, course-based:
  * the intro slots are met by 121 OR a completed 111, 122 OR a completed 112;
    the 111/112 counting is flagged "needs review";
  * 111 is never offered after Fall 2026, 112 never after Spring 2027;
    120/121 from Spring 2027 and 122 from Fall 2027 are department-announced;
  * a 122 satisfies prerequisites naming 112 (and 121 those naming 111);
  * 122's prerequisite, until SOC publishes one, is assumed (121 or 111);
  * 120 earns degree credit, never CS major credit (free elective only).
"""

from __future__ import annotations

import json

import pytest
from app.domain.planning import (
    FillerPreference,
    OfferingEvidenceKind,
    Pace,
    PlanningConstraints,
    WorkloadLevel,
)
from app.domain.prerequisites import PrereqStatus
from app.models import Course, Program, ProgramVersion, Student, StudentCourse
from app.services import knowledge
from app.services.audit.engine import DegreeAuditEngine
from app.services.planning.offerings import OfferingIndex
from app.services.planning.service import generate_plan
from app.services.prerequisites import check_many
from sqlalchemy import select

from coursepilot_ingestion.loaders.requirements import RequirementLoader
from coursepilot_ingestion.loaders.transitions import (
    load_transitions,
    validate_transitions,
)
from coursepilot_ingestion.pipelines.courses import CourseIngestionPipeline
from coursepilot_ingestion.pipelines.sections import SectionIngestionPipeline
from coursepilot_ingestion.sources.soc import SocQuery

from .conftest import CURATED_DIR, FIXTURE_DIR

TRANSITIONS = CURATED_DIR.parent / "course-transitions.json"
SPRING27, FALL27, SPRING28 = "20271", "20279", "20281"


@pytest.fixture
def today(monkeypatch):
    monkeypatch.setenv("ACADEMIC_TODAY", "2026-10-15")


@pytest.fixture
def world(session, tmp_path):
    terms = json.loads((FIXTURE_DIR / "soc_program_expansion_terms.json").read_bytes())
    for term, records in terms.items():
        year, season = term.split("_")
        folder = tmp_path / term
        folder.mkdir()
        (folder / f"soc_courses_{year}_{season}_NB.json").write_text(json.dumps(records), "utf-8")
        query = SocQuery(year=int(year), term=season, campus="NB")
        CourseIngestionPipeline(session, folder).run(query, coverage="complete")
        SectionIngestionPipeline(session, folder).run(query)
    load_transitions(session, TRANSITIONS)
    RequirementLoader(session).load_file(CURATED_DIR / "sas-198-bs.json")
    session.commit()
    return session


def _version(session):
    return session.scalar(select(ProgramVersion).join(Program).where(
        Program.code == "198", Program.degree_type == "BS"))


def _student(session, history, ref):
    v = _version(session)
    s = Student(external_ref=f"cs-{ref}", catalog_year="2026-2027", program_version_id=v.id,
                first_term="20269")
    session.add(s)
    session.flush()
    for key, term, grade in history:
        course = session.scalar(select(Course).where(Course.course_string == key,
                                                     Course.supplement_code == ""))
        session.add(StudentCourse(student_id=s.id, course_id=course.id, term_code=term,
                                  status="completed", grade=grade,
                                  credits_earned=course.credits if grade not in ("F", "D")
                                  else 0))
    session.commit()
    return s


def _walk(nodes):
    for n in nodes:
        yield n
        yield from _walk(n.children)


def _node(audit, code):
    return next(n for n in _walk(audit.requirements) if n.requirement_code == code)


# --------------------------------------------------------------------------


def test_the_transitions_file_is_well_formed_and_announced_courses_get_rows(world):
    assert validate_transitions(json.loads(TRANSITIONS.read_text("utf-8"))) == []
    c122 = world.scalar(select(Course).where(Course.course_string == "01:198:122"))
    assert c122 is not None and c122.credits == 4 and c122.level == "U"
    assert "announced" in (c122.credits_description or "")


def test_retired_and_announced_offerings(world):
    idx = OfferingIndex(world)
    # 111: last offered Fall 2026 - never after, whatever its history.
    assert idx.evidence("01:198:111", FALL27).kind is OfferingEvidenceKind.NOT_OFFERED_IN_TERM
    # 112: still in Spring 2027 (published), never from Fall 2027.
    assert idx.evidence("01:198:112", SPRING27).kind is OfferingEvidenceKind.CONFIRMED_IN_TERM
    assert idx.evidence("01:198:112", FALL27).kind is OfferingEvidenceKind.NOT_OFFERED_IN_TERM
    # 122 from Fall 2027: announced by the department, placeable.
    assert idx.evidence("01:198:122", FALL27).kind is OfferingEvidenceKind.DEPARTMENT_ANNOUNCED
    assert idx.evidence("01:198:122", SPRING27).kind is not \
        OfferingEvidenceKind.DEPARTMENT_ANNOUNCED
    # 121: published for Spring 2027, announced for later terms.
    assert idx.evidence("01:198:121", SPRING27).kind is OfferingEvidenceKind.CONFIRMED_IN_TERM
    assert idx.evidence("01:198:121", FALL27).kind is OfferingEvidenceKind.DEPARTMENT_ANNOUNCED


def test_122_satisfies_prerequisites_that_name_112(world):
    s = _student(world, [("01:198:121", SPRING27, "A")], "eq")
    from app.domain.attempts import Attempt
    planned_122 = [Attempt("01:198:122", FALL27, "completed", "A")]
    without = check_many(world, s, ["01:198:211"], SPRING27)["01:198:211"]
    with_122 = check_many(world, s, ["01:198:211"], SPRING27, projected=planned_122)["01:198:211"]
    assert without.status is not PrereqStatus.SATISFIED
    assert with_122.status is PrereqStatus.SATISFIED


def test_122_uses_its_assumed_prerequisite_until_soc_publishes_one(world):
    none = _student(world, [], "none")
    has121 = _student(world, [("01:198:121", SPRING27, "B")], "has121")
    has111 = _student(world, [("01:198:111", "20269", "B")], "has111")
    a = check_many(world, none, ["01:198:122"], FALL27)["01:198:122"]
    b = check_many(world, has121, ["01:198:122"], FALL27)["01:198:122"]
    c = check_many(world, has111, ["01:198:122"], FALL27)["01:198:122"]
    assert a.status is PrereqStatus.UNSATISFIED
    assert b.status is PrereqStatus.SATISFIED and c.status is PrereqStatus.SATISFIED
    assert "assumed_prerequisite_needs_review" in b.reasons
    assert b.raw_text.startswith("Assumed (not yet published)")


def test_a_completed_111_fills_the_121_slot_flagged_for_review(world):
    s = _student(world, [("01:198:111", "20269", "A")], "old111")
    audit = DegreeAuditEngine(world).audit(s, program_version=_version(world))
    node = _node(audit, "CS_111")
    assert node.status.value in ("satisfied", "provisionally_satisfied")
    assert node.review_flags and node.review_flags[0]["id"] == "CS-111-AS-121"
    assert any(f.code == "counted_under_assumption" and f.requirement_code == "CS_111"
               for f in audit.findings)


def test_a_121_fills_the_slot_without_any_assumption(world):
    s = _student(world, [("01:198:121", SPRING27, "A")], "new121")
    audit = DegreeAuditEngine(world).audit(s, program_version=_version(world))
    assert _node(audit, "CS_111").status.value in ("satisfied", "provisionally_satisfied")
    assert not any(f.code == "counted_under_assumption" for f in audit.findings)


def test_cs120_is_free_elective_only_and_never_major_credit(world):
    s = _student(world, [("01:198:120", SPRING27, "A")], "only120")
    audit = DegreeAuditEngine(world).audit(s, program_version=_version(world))
    assert not any(a.course.course_string == "01:198:120" and
                   a.requirement_code.startswith("CS_") for a in audit.allocation)
    rule = next(r for r in audit.rules if r.rule_code == "CS_120_NO_MAJOR_CREDIT")
    assert rule.status.value == "satisfied"
    knowledge.build_index(world)
    world.commit()
    k = knowledge.KnowledgeView(world, _version(world), SPRING27).judge("01:198:120")
    assert k.status == "free_elective_only"
    assert "SAS credit for CS120" in k.reasons[0]


def _plan(world, student):
    return generate_plan(world, student, start_term=SPRING27, constraints=PlanningConstraints(
        workload_level=WorkloadLevel.SEMI_RIGOROUS, filler_preference=FillerPreference.FLEXIBLE,
        max_courses_per_term=6, pace=Pace.BALANCED))


def _placed(plan):
    return {pc.course: (t.term_code, pc) for t in plan.terms for pc in t.courses}


def test_never_plans_a_retired_course_after_its_last_term(world, today):
    s = _student(world, [("01:640:151", "20269", "A")], "fresh")
    placed = _placed(_plan(world, s))
    assert "01:198:111" not in placed
    for course, last in (("01:198:111", "20269"), ("01:198:112", SPRING27)):
        if course in placed:
            assert placed[course][0] <= last


def test_student_with_111_takes_112_in_spring_2027_otherwise_122_from_fall_2027(world, today):
    """Owner decision: "a student with 111 but no 112 can take 112 in Spring 2027,
    otherwise 122 from Fall 2027". (In this fixture 112's Spring 2027 prerequisite
    carries a minimum-grade condition the engine does not evaluate, so the planner
    may take the 122 path - both are correct; neither may be mistimed.)"""
    s = _student(world, [("01:198:111", "20269", "A"), ("01:640:151", "20269", "A")], "has111p")
    placed = _placed(_plan(world, s))
    assert ("01:198:112" in placed) != ("01:198:122" in placed)      # exactly one path
    if "01:198:112" in placed:
        assert placed["01:198:112"][0] == SPRING27
    else:
        assert placed["01:198:122"][0] >= FALL27
    # 121 is never needed: the completed 111 fills the slot (flagged for review).
    assert "01:198:121" not in placed and "01:198:120" not in placed


def test_student_with_neither_starts_with_120_and_sees_the_ba_note(world, today):
    s = _student(world, [("01:640:112", "20269", "A")], "neither")
    placed = _placed(_plan(world, s))
    assert "01:198:111" not in placed and "01:198:112" not in placed
    assert "01:198:120" in placed or "01:198:121" in placed
    if "01:198:120" in placed:
        assert placed["01:198:120"][0] >= SPRING27
        notes = " ".join(placed["01:198:120"][1].notes)
        assert "may choose the B.A." in notes and "does not make it" in notes


def test_a_failed_111_is_retaken_as_120_or_121_never_111(world, today):
    s = _student(world, [("01:198:111", "20269", "F"), ("01:640:112", "20259", "A")], "f111")
    placed = _placed(_plan(world, s))
    assert "01:198:111" not in placed
    audit = DegreeAuditEngine(world).audit(s, program_version=_version(world))
    assert _node(audit, "CS_111").status.value not in ("satisfied", "provisionally_satisfied")
