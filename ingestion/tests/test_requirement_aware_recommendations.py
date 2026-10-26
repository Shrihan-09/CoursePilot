"""Phase 7.6 (Prompt 05): requirement-aware recommendations.

World: REAL public SOC records (fixtures/soc_workload_terms_sample.json - CS
core, Calc I/II, Linear Algebra, Gen Physics 203/204 with labs, Sociology,
Linguistics 135, Art History 207) for Spring 2026, Fall 2026 and Spring 2027,
loaded by the production pipelines - courses, sections, restrictions and the
term-scoped coreCodes. The real curated CS B.A. and B.S. (pending review) with
SAS Core attached at the school level from the same records' coreCodes.

CSP is not adopted (PHASE_7_3_PLAN.md section 1a): SOC is the section source,
so the "synthetic CSP data" of Prompt 05 is this public SOC sample. Every
STUDENT is fabricated; the Degree Navigator audit is the synthetic Chrome
layout from backend/tests/support/synthetic_dn.py (subject headings and Core
codes are Rutgers' public names), taken through the real extractor, parser,
mapping table and confirmation; the WebReg schedule is the record its
confirmation writes (in-progress course + registration row).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import html as html_lib
import importlib.util
import json
import pathlib
import re
import uuid
from types import SimpleNamespace

import pytest
from app.domain.course_ranking import CourseRankingEvidence
from app.domain.planning import (
    EligibilityState,
    OfferingEvidenceKind,
    Pace,
    PlanningConstraints,
    WorkloadLevel,
)
from app.models import (
    Course,
    CourseOffering,
    CourseSection,
    Program,
    ProgramVersion,
    SchoolRequirementSetVersion,
    SectionRestriction,
    Student,
    StudentCourse,
)
from app.models.core_codes import OfferingCoreCode
from app.models.identity import UserAccount
from app.models.schedule_import import StudentRegistration
from app.services.academic_context import composed_requirements
from app.services.planning.engine import PlanningEngine
from app.services.planning.ranking import rank_key
from app.services.planning.service import UNMAPPED_OPEN, generate_plan
from sqlalchemy import delete, select

from coursepilot_ingestion.loaders.requirements import RequirementLoader
from coursepilot_ingestion.pipelines.core import CoreIngestionPipeline
from coursepilot_ingestion.pipelines.courses import CourseIngestionPipeline
from coursepilot_ingestion.pipelines.sections import SectionIngestionPipeline
from coursepilot_ingestion.sources.soc import SocQuery

from .conftest import CS_REQUIREMENTS, CURATED_DIR, FIXTURE_DIR, SAS_CORE

ROOT = pathlib.Path(__file__).resolve().parents[2]
SPRING27, FALL26 = "20271", "20269"
CS_BS = CURATED_DIR / "sas-198-bs.json"
NOW = dt.datetime(2026, 10, 15, 14, 0, tzinfo=dt.UTC)

#: A fabricated CS student: first year done, second year under way.
HISTORY = [("01:198:111", "20259", "A"), ("01:640:151", "20259", "A"),
           ("01:920:101", "20259", "A"), ("01:198:112", "20261", "B+"),
           ("01:640:152", "20261", "B"), ("01:198:205", "20261", "A")]
#: Fall 2026, from a confirmed WebReg schedule.
CURRENT = ["01:198:206", "01:198:211", "01:640:250"]


# --------------------------------------------------------------------------
# world and helpers
# --------------------------------------------------------------------------


@pytest.fixture
def world(session, tmp_path):
    terms = json.loads((FIXTURE_DIR / "soc_workload_terms_sample.json").read_bytes())
    combined = {}
    for term, records in terms.items():
        year, season = term.split("_")
        folder = tmp_path / term
        folder.mkdir()
        (folder / f"soc_courses_{year}_{season}_NB.json").write_text(json.dumps(records), "utf-8")
        query = SocQuery(year=int(year), term=season, campus="NB")
        CourseIngestionPipeline(session, folder).run(query, coverage="complete")
        SectionIngestionPipeline(session, folder).run(query)
        combined.update({r["courseString"]: r for r in records})
    RequirementLoader(session).load_file(CS_REQUIREMENTS)
    RequirementLoader(session).load_file(CS_BS)
    session.commit()
    archive = tmp_path / "combined.json"
    archive.write_text(json.dumps(list(combined.values())), "utf-8")
    CoreIngestionPipeline(session).run(SAS_CORE, archive, observed_term_code=SPRING27)
    session.commit()
    return session


def _version(session, degree="BA", code="198"):
    return session.scalar(select(ProgramVersion).join(Program)
                          .where(Program.code == code, Program.degree_type == degree))


def _course(session, key):
    return session.scalar(select(Course).where(Course.course_string == key,
                                               Course.supplement_code == ""))


def _student(session, history=HISTORY, current=CURRENT, *, degree="BA", declaration=None,
             version=None):
    version = version or _version(session, degree)
    student = Student(external_ref=f"ra-{uuid.uuid4().hex[:8]}", catalog_year=version.catalog_year,
                      program_version_id=version.id, declaration_status=declaration)
    session.add(student)
    session.flush()
    for key, term, grade in history:
        course = _course(session, key)
        session.add(StudentCourse(student_id=student.id, course_id=course.id, term_code=term,
                                  status="completed", grade=grade, credits_earned=course.credits))
    _webreg(session, student, current)
    session.commit()
    return student


def _webreg(session, student, courses, term=FALL26):
    """What confirming a WebReg schedule writes (DATA_MODEL section 52): the
    in-progress course and the exact registration row."""
    for key in courses:
        course = _course(session, key)
        exists = session.scalar(select(StudentCourse.id).where(
            StudentCourse.student_id == student.id, StudentCourse.course_id == course.id,
            StudentCourse.term_code == term))
        if exists is None:
            session.add(StudentCourse(student_id=student.id, course_id=course.id, term_code=term,
                                      status="in_progress", grade=None, credits_earned=None))
        session.add(StudentRegistration(student_id=student.id, term_code=term, course_string=key,
                                        section_number="01", source="student_schedule_pdf",
                                        confirmed_at=NOW))
    session.flush()


def _plan(session, student, **kw):
    kw.setdefault("start_term", SPRING27)
    kw.setdefault("constraints", PlanningConstraints(max_courses_per_term=6,
                                                     max_credits_per_term=18))
    return generate_plan(session, student, **kw)


def _placed(plan):
    return {pc.course: pc for t in plan.terms for pc in t.courses}


def _term(plan, code=SPRING27):
    return next((t for t in plan.terms if t.term_code == code), None)


def _codes(plan):
    return {r.requirement_code for pc in _placed(plan).values() for r in pc.requirements}


# --------------------------------------------------------------------------
# 1. track isolation
# --------------------------------------------------------------------------


def test_a_ba_student_never_gets_bs_only_requirements(world):
    ba = _plan(world, _student(world, degree="BA"))
    bs = _plan(world, _student(world, degree="BS"))
    ba_codes = {r.code for r in composed_requirements(world, _version(world, "BA"))}
    bs_codes = {r.code for r in composed_requirements(world, _version(world, "BS"))}
    assert _codes(ba) <= ba_codes and _codes(bs) <= bs_codes
    assert not any(c.startswith("CS_BS_") for c in _codes(ba))
    assert not any(r.requirement_code.startswith("CS_BS_") for r in ba.requirements)
    assert any(c.startswith("CS_BS_") for c in _codes(bs))        # the B.S. science sequence
    # A B.A. student takes physics only where it fills a requirement the B.A. has (Core NS).
    for pc in _placed(ba).values():
        if pc.course.startswith("01:750:"):
            assert {r.requirement_system for r in pc.requirements} <= {"core"}


def test_an_option_a_student_never_gets_another_options_requirements(session, tmp_path):
    from .conftest import MATH_REQUIREMENTS
    from .test_planning_engine import _records, _terms

    _terms(session, tmp_path, _records("soc_math_courses_sample.json"))
    RequirementLoader(session).load_file(MATH_REQUIREMENTS)
    other = json.loads(MATH_REQUIREMENTS.read_bytes())
    other["program"]["variant"] = "option-b"          # SYNTHETIC second option
    other["requirements"].append({"code": "MATH_OPTB_ONLY", "name": "SYNTHETIC Option B only",
                                  "requirement_type": "course", "parent": "MATH_BA",
                                  "sort_order": 99, "courses": ["01:640:250"]})
    RequirementLoader(session).load(other, json.dumps(other).encode())
    session.commit()
    option_a = session.scalar(select(ProgramVersion).join(Program).where(
        Program.code == "640", Program.variant == "option-a"))
    student = _student(session, [("01:640:151", "20259", "A")], [], version=option_a)
    plan = _plan(session, student, start_term="20271")
    assert plan.target.program_key == "sas-640-ba-option-a"
    a_codes = {r.code for r in composed_requirements(session, option_a)}
    assert _codes(plan) <= a_codes
    assert "MATH_OPTB_ONLY" not in {r.requirement_code for r in plan.requirements}
    what_if = _plan(session, student, start_term="20271", program_key_="sas-640-ba-option-b")
    assert "MATH_OPTB_ONLY" in {r.requirement_code for r in what_if.requirements}


# --------------------------------------------------------------------------
# 2. school Core chosen by the program's school; unencoded -> unknown
# --------------------------------------------------------------------------


def test_school_core_is_the_programs_schools_and_unencoded_is_unknown(world):
    sas = _plan(world, _student(world))
    assert (sas.school_core.status, sas.school_core.requirement_sets) == ("encoded", ["sas-core"])
    assert any(r.requirement_system == "core" for pc in _placed(sas).values()
               for r in pc.requirements)

    sebs = json.loads(CS_REQUIREMENTS.read_bytes())
    sebs["school"] = {**sebs["school"], "code": "SEBS",
                      "name": "SYNTHETIC School of Environmental and Biological Sciences"}
    RequirementLoader(world).load(sebs, json.dumps(sebs).encode())
    world.commit()
    version = world.scalar(select(ProgramVersion).join(Program)
                           .where(Program.code == "198", Program.degree_type == "BA",
                                  Program.school.has(code="SEBS")))
    plan = _plan(world, _student(world, version=version), program_key_="sebs-198-ba")
    assert plan.school_core.status == "unknown"
    assert "Core status is unknown" in plan.school_core.message
    assert "SAS Core is not used" in plan.school_core.message
    assert "school_core_unknown" in {a.code for a in plan.assumptions}
    assert "SCHOOL_CORE_UNKNOWN" in {i.code for i in plan.issues}
    assert not any(r.requirement_system == "core" or r.requirement_code.startswith("CORE_")
                   for pc in _placed(plan).values() for r in pc.requirements)
    assert not any(r.requirement_code.startswith("CORE_") for r in plan.requirements)


# --------------------------------------------------------------------------
# 3. Degree Navigator status overrides the estimate; unmapped stays shown
# --------------------------------------------------------------------------


def _dn_support():
    spec = importlib.util.spec_from_file_location(
        "synthetic_dn_76", ROOT / "backend" / "tests" / "support" / "synthetic_dn.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _dn_model():
    """A SAS Core audit for the fabricated student (Chrome layout, synthetic).
    DN shows Arts and Humanities FULFILLED although no AH course is on the
    record CoursePilot sees (an exception DN knows about), and one requirement
    CoursePilot has no mapping for."""
    used = [("01:920:101", 3, "Fall 2025", "A"), ("01:640:151", 4, "Fall 2025", "A"),
            ("01:198:111", 4, "Fall 2025", "A")]
    return {
        "printed_at": "10/14/26, 9:15 AM", "report_date": "October 14 2026", "degree_id": "9999",
        "subject": "The SAS Core Curriculum (NB)", "version": "Summer 2019",
        "identity": ("TEST STUDENT", "000000000"),
        "requirements": [
            {"title": "Contemporary Challenges [CC]", "status": "not_fulfilled", "total": 2,
             "intro": "The required number of courses from 2 of the following sets of courses:",
             "join": "and", "needs": "2 more courses are needed to satisfy this requirement.",
             "goals": [("Contemporary Challenges: Our Common Future Courses", "CCO", []),
                       ("Contemporary Challenges: Diversity and Social Inequalities Courses",
                        "CCD", [])], "courses": []},
            {"title": "Areas of Inquiry: Natural Sciences [NS]", "status": "not_fulfilled",
             "total": 2, "intro": "A total of 2 courses from", "join": None,
             "needs": "2 more courses are needed to satisfy this requirement.",
             "goals": [("Natural Sciences", "NS", [])], "courses": []},
            {"title": "Areas of Inquiry: Social [SCL] and Historical [HST] Analysis",
             "status": "not_fulfilled", "total": 2,
             "intro": "1 courses from 2 of the following sets of courses:", "join": "and",
             "needs": "1 more course is needed to satisfy this requirement.",
             "goals": [("Historical Analysis", "HST", []),
                       ("Social Analysis", "SCL", [("01:920:101", "completed")])],
             "courses": [("01:920:101", 3, "Fall 2025", "A")]},
            {"title": "Areas of Inquiry: Arts and Humanities [AHo], [AHp], [AHq], [AHr]",
             "status": "fulfilled", "total": 2,
             "intro": "1 courses from 2 of the following sets of courses:", "join": "or",
             "goals": [("Human Experience", "AHo", []), ("Arts and/or Literatures", "AHp", []),
                       ("Nature of Languages", "AHq", []),
                       ("Critical Creative Expression", "AHr", [])], "courses": []},
            {"title": "Cognitive Skills and Processes: Writing and Communication "
                      "[WC], [WCr], [WCd]", "status": "not_fulfilled", "total": 3,
             "intro": "1 courses from 3 of the following sets of courses:", "join": "and",
             "needs": "3 more courses are needed to satisfy this requirement.",
             "goals": [("College Writing", "WC", []),
                       ("Revision-Based Writing and Communication", "WCr", []),
                       ("Discipline-Based Writing and Communication", "WCd", [])],
             "courses": []},
            {"title": "Cognitive Skills and Processes: Quantitative and Formal Reasoning "
                      "[QQ], [QR]", "status": "fulfilled", "total": 2,
             "intro": "1 courses from 2 of the following sets of courses:", "join": "and",
             "goals": [("Quantitative Information", "QQ", [("01:640:151", "completed")]),
                       ("Mathematical or Formal Reasoning", "QR", [("01:198:111", "completed")])],
             "courses": [("01:640:151", 4, "Fall 2025", "A"), ("01:198:111", 4, "Fall 2025", "A")]},
            {"title": "SYNTHETIC Unmapped Experience [ZZ]", "status": "not_fulfilled",
             "total": 1, "intro": "1 courses from", "join": None,
             "needs": "1 more course is needed to satisfy this requirement.",
             "goals": [("SYNTHETIC Unmapped Experience", "ZZ", [])], "courses": []},
        ],
        "used": used,
        "unused": [("01:198:112", 4, "Spring 2026", "B+"), ("01:640:152", 4, "Spring 2026", "B"),
                   ("01:198:205", 4, "Spring 2026", "A"),
                   *[(c, 4 if c != "01:640:250" else 3, "Fall 2026", "current") for c in CURRENT]],
        "below_minimum": [],
    }


def _confirm_dn(session, student, model):
    """The real Phase 7.4 path - render, extract (in-process), upload, confirm."""
    from app.services.degree_audit.imports import confirm_import, upload_degree_audit
    from app.services.degree_audit.pdf_worker import extract_audit_layout
    from app.services.transcripts.extract import ExtractedText, ExtractionFailed
    from app.services.transcripts.storage import MemoryTranscriptStorage

    def in_process(data: bytes) -> ExtractedText:
        out = extract_audit_layout(data, 20, 200_000)
        if out[0] == "error":
            raise ExtractionFailed(out[1])
        return ExtractedText(pages=list(out[1]), page_count=out[2], layout=out[3])

    account = UserAccount(identity_provider="oidc", external_subject=f"ra-{uuid.uuid4()}")
    session.add(account)
    session.flush()
    student.user_id = account.id
    session.commit()
    imports, _ = upload_degree_audit(session, account.id, _dn_support().build_audit_pdf(model),
                                     storage=MemoryTranscriptStorage(), extractor=in_process,
                                     now=NOW, max_bytes=5_000_000)
    for imp in imports:
        assert imp.state == "extracted", imp.failure_code
        confirm_import(session, account.id, imp.id, expected_version=imp.version, now=NOW)
    session.commit()
    return imports


def _dn_student(session, degree="BA"):
    """History and current courses enter ONLY through the confirmed DN audit
    (its transcript path), then the WebReg registration rows."""
    student = _student(session, [], [], degree=degree)
    _confirm_dn(session, student, _dn_model())
    _webreg(session, student, CURRENT)
    session.commit()
    return student


def test_degree_navigator_status_overrides_the_estimate(world):
    estimated = _plan(world, _student(world))
    assert "requirement_status_estimated" in {a.code for a in estimated.assumptions}
    est_ah = [pc for pc in _placed(estimated).values()
              if any(r.requirement_code.startswith("CORE_AH") for r in pc.requirements)]
    assert est_ah, "without DN, CoursePilot's estimate plans Arts and Humanities"
    assert all(t.status_source == "estimated" for pc in est_ah for t in pc.trace.requirements)

    dn = _plan(world, _dn_student(world))
    assert "requirement_status_from_degree_navigator" in {a.code for a in dn.assumptions}
    # DN says Arts and Humanities is fulfilled: nothing is planned for it.
    assert not [pc for pc in _placed(dn).values()
                if any(r.requirement_code.startswith("CORE_AH") for r in pc.requirements)]
    # Natural Sciences is open per DN - the trace says so.
    ns = [t for pc in _placed(dn).values() for t in pc.trace.requirements
          if t.requirement_code == "CORE_NS"]
    assert ns and all(t.status_source == "degree_navigator" for t in ns)
    assert all(t.status_rule for t in ns)


def test_an_unmapped_open_dn_requirement_is_shown_and_never_filled(world):
    plan = _plan(world, _dn_student(world))
    unmapped = [i for i in plan.issues if i.code == "DN_REQUIREMENT_OPEN_UNMAPPED"]
    assert len(unmapped) == 1
    assert UNMAPPED_OPEN in unmapped[0].message and "SYNTHETIC Unmapped" in unmapped[0].message
    # Goal rows of a FULFILLED requirement (AHo..AHr) are not reported as open.
    assert not any("AHp" in i.message for i in unmapped)
    assert all(not r.requirement_code.startswith("ZZ") for pc in _placed(plan).values()
               for r in pc.requirements)


# --------------------------------------------------------------------------
# 4. in-progress (WebReg) courses unlock next-term prerequisites
# --------------------------------------------------------------------------


def test_in_progress_courses_unlock_next_term_prerequisites(world):
    with_current = _plan(world, _student(world))
    spring = {pc.course: pc for pc in _term(with_current).courses}
    assert "01:198:344" in spring            # needs 206, which is in progress
    pre = spring["01:198:344"].prerequisite
    assert pre.state is EligibilityState.CONDITIONAL_ON_PLAN
    assert {(d.course, d.source) for d in pre.conditional_on} >= {("01:198:206", "in_progress")}
    without = _plan(world, _student(world, current=[]))
    assert "01:198:344" not in {pc.course for pc in _term(without).courses}


# --------------------------------------------------------------------------
# 5. the multi-requirement preference respects the sharing policy
# --------------------------------------------------------------------------


def _set_sharing(session, policy):
    for v in session.scalars(select(ProgramVersion)):
        v.sharing_policy = policy
    for v in session.scalars(select(SchoolRequirementSetVersion)):
        v.sharing_policy = policy
    session.commit()


def test_double_counting_is_preferred_only_where_sharing_allows(world):
    workload = PlanningConstraints(workload_level=WorkloadLevel.SEMI_RIGOROUS,
                                   max_courses_per_term=6, pace=Pace.BALANCED)
    shared = _plan(world, _student(world, degree="BS"), constraints=workload)
    phys = _placed(shared)["01:750:203"]
    assert {r.requirement_code for r in phys.requirements} == {"CORE_NS", "CS_BS_PHYS_A_203"}
    assert phys.role == "multi_requirement" and phys.ranking_evidence.candidate_requirement_coverage == 2
    assert "Natural Sciences" in phys.role_reason

    # SYNTHETIC: every real SAS program shares through the SAS Core set. Under
    # EXCLUSIVE the planner never prefers a course for counting twice. (Where
    # the Degree Engine finally credits it is the engine's decision - its
    # credit-requirement pass checks only the requirement's own scope, a
    # latent gap recorded in the Phase 7.6 PR; no real program reaches it.)
    _set_sharing(world, "exclusive")
    exclusive = _plan(world, _student(world, degree="BS"), constraints=workload)
    for pc in _placed(exclusive).values():
        assert pc.role != "multi_requirement", pc.course
        if pc.ranking_evidence is not None:
            assert pc.ranking_evidence.candidate_requirement_coverage <= 1


# --------------------------------------------------------------------------
# 6. section restrictions: excluded with the reason
# --------------------------------------------------------------------------


def _majors_only(session, key="01:198:344", term=SPRING27):
    """SYNTHETIC: drop the CS-minor entry so the term's sections read "open
    only to majors 185/198" (the real Spring 2027 listing also admits CS
    minors, which CoursePilot cannot check)."""
    ids = list(session.scalars(
        select(CourseSection.id).join(CourseOffering, CourseOffering.id == CourseSection.offering_id)
        .join(Course, Course.id == CourseOffering.course_id)
        .where(Course.course_string == key, CourseSection.term_code == term)))
    session.execute(delete(SectionRestriction).where(SectionRestriction.section_id.in_(ids),
                                                     SectionRestriction.kind == "minor"))
    session.commit()


def test_a_restricted_course_is_excluded_with_its_reason(world):
    _majors_only(world)
    undeclared = _plan(world, _student(world, declaration="undeclared"))
    assert "01:198:344" not in {pc.course for pc in _term(undeclared).courses}
    issue = next(i for i in undeclared.issues
                 if i.code == "SECTION_RESTRICTED" and i.course == "01:198:344")
    assert "Every Spring 2027 section is open only to" in issue.message
    assert "major 198" in issue.message and "not declared it yet" in issue.message
    assert issue.details["term"] == SPRING27
    # A declared CS major is let in by the same sections.
    declared = _plan(world, _student(world))
    pc = next(pc for pc in _term(declared).courses if pc.course == "01:198:344")
    assert pc.trace.restriction.outcome == "satisfied"


def test_an_uncheckable_restriction_is_placed_and_flagged(world):
    plan = _plan(world, _student(world, declaration="undeclared"))   # real listing: majors + minor
    pc = next(pc for pc in _term(plan).courses if pc.course == "01:198:344")
    assert pc.trace.restriction.outcome == "unknown"
    assert any("not declared this major yet" in n for n in pc.notes)
    assert "SECTION_RESTRICTION_UNCONFIRMED" in {i.code for i in plan.issues
                                                 if i.course == "01:198:344"}


# --------------------------------------------------------------------------
# 7. Core goals from the TERM's SOC coreCodes
# --------------------------------------------------------------------------


def test_core_goals_come_from_the_planned_terms_core_codes(world):
    plan = _plan(world, _student(world))
    ns = next(pc for pc in _placed(plan).values()
              if any(r.requirement_code == "CORE_NS" for r in pc.requirements))
    assert ns.term_code == SPRING27
    evidence = ns.trace.core_codes
    assert (evidence.term_code, evidence.basis, evidence.goals) == (SPRING27, "planned_term",
                                                                    {"NS": True})
    assert any(s.fact == "core_codes" and s.data_source_id for s in ns.trace.sources)

    # SYNTHETIC: Spring 2027's listing no longer certifies NS for Gen Physics.
    world.execute(delete(OfferingCoreCode).where(OfferingCoreCode.term_code == SPRING27,
                                                 OfferingCoreCode.code == "NS"))
    world.commit()
    later = _plan(world, _student(world))
    spring_ns = [pc for pc in _term(later).courses
                 if any(r.requirement_code == "CORE_NS" for r in pc.requirements)]
    # Not chosen FOR Core NS any more. If it is still placed (as the
    # prerequisite of a later Natural Sciences course) and the Degree Engine
    # credits it to NS, the recommendation says the term's listing does not.
    for pc in spring_ns:
        assert pc.unlocks, pc.course
        # ranked as a prerequisite, not as a Core NS candidate
        assert pc.ranking_evidence.candidate_requirement_coverage == 0, pc.course
        assert pc.trace.core_codes.goals == {"NS": False}
        assert any("does not list NS" in n for n in pc.notes)
        assert any(i.code == "CORE_CODE_NOT_LISTED_FOR_TERM" and i.course == pc.course
                   for i in later.issues)
    fall_ns = [pc for pc in _placed(later).values() if pc.term_code > SPRING27
               and any(r.requirement_code == "CORE_NS" for r in pc.requirements)]
    assert all(pc.trace.core_codes.basis == "latest_offering" for pc in fall_ns)


def test_core_codes_are_loaded_per_term_and_reload_idempotently(session, tmp_path):
    from app.services.sources.sections import core_codes

    records = json.loads((FIXTURE_DIR / "soc_workload_terms_sample.json").read_bytes())["2027_1"]

    def load(name, payload):
        folder = tmp_path / name
        folder.mkdir()
        (folder / "soc_courses_2027_1_NB.json").write_text(json.dumps(payload), "utf-8")
        return CourseIngestionPipeline(session, folder).run(SocQuery(year=2027, term="1",
                                                                     campus="NB"))

    first = load("a", records).core_codes
    expected = sum(len(r.get("coreCodes") or []) for r in records)
    assert first.get("inserted") == expected
    assert load("b", records).core_codes == {"unchanged": expected}
    listed = core_codes(session, SPRING27, ["01:750:203", "01:198:211", "01:999:999"])
    assert listed["01:750:203"].codes == ["NS"] and listed["01:198:211"].codes == []
    assert "01:999:999" not in listed                     # not offered: nothing was published
    assert listed["01:750:203"].provenance.source == "soc_courses_json"
    # SYNTHETIC: a newer payload drops 203's NS - the stored code goes with it.
    changed = json.loads(json.dumps(records))
    next(r for r in changed if r["courseString"] == "01:750:203")["coreCodes"] = []
    assert load("c", changed).core_codes.get("deleted") == 1
    assert core_codes(session, SPRING27, ["01:750:203"])["01:750:203"].codes == []


# --------------------------------------------------------------------------
# 8. sequencing: long chains, then one-season-only courses
# --------------------------------------------------------------------------


def test_sequencing_prefers_long_chains_then_one_season_courses():
    base = {"confirmed_offering": True, "prerequisites_met_by_history": True}
    chain = CourseRankingEvidence(unlock_chain_depth=3, **base)
    short = CourseRankingEvidence(unlock_chain_depth=1, season_only=True, **base)
    plain = CourseRankingEvidence(unlock_chain_depth=1, **base)
    assert rank_key("B", chain) < rank_key("A", short) < rank_key("A0", plain)
    needed = CourseRankingEvidence(required_dependency=True, **base)
    assert rank_key("Z", needed) < rank_key("A", chain)        # a needed prerequisite still first


def test_a_one_season_course_names_the_next_chance():
    engine = PlanningEngine.__new__(PlanningEngine)
    engine.offerings = SimpleNamespace(
        terms={"F": ["20239", "20249", "20259"], "B": ["20241", "20249", "20251"]},
        coverage={t: "complete" for t in ("20239", "20241", "20249", "20251", "20259", "20261")})
    fall = engine._pattern("F", "20269")
    assert (fall.only_season, fall.next_chance) == ("Fall", "20279")
    assert "the next chance is Fall 2027" in fall.message
    assert engine._pattern("B", "20271").only_season is None


def test_the_chain_depth_counts_open_requirement_courses(world):
    plan = _plan(world, _student(world, history=HISTORY[:3], current=[]))
    placed = _placed(plan)
    # 112 leads to 211/205-206 and on to 344: a longer chain than a Core course.
    assert placed["01:198:112"].trace.unlock_chain_depth > 0
    assert placed["01:198:112"].trace.unlock_chain_depth >= max(
        (pc.trace.unlock_chain_depth for pc in placed.values()
         if any(r.requirement_system == "core" for r in pc.requirements)), default=0)


def _node(code, rtype, status="unsatisfied", children=(), satisfied=0, need=1):
    from app.domain.audit import RequirementResult, RequirementStatus

    return RequirementResult(requirement_code=code, requirement_name=code, requirement_type=rtype,
                             status=RequirementStatus(status), reason="", children=list(children),
                             satisfied_count=satisfied, needed_count=need)


def _science(*, b_done: bool, a_started: bool):
    """ANY_OF of two ALL_OF sequences (the B.S. science shape)."""
    def leaf(code, done):
        return _node(code, "course", "provisionally_satisfied" if done else "unsatisfied",
                     satisfied=1 if done else 0)

    a = _node("SCI_A", "all_of", children=[leaf("SCI_A_1", a_started), leaf("SCI_A_2", False)])
    b = _node("SCI_B", "all_of", "provisionally_satisfied" if b_done else "unsatisfied",
              children=[leaf("SCI_B_1", b_done), leaf("SCI_B_2", b_done)])
    root = _node("SCI", "any_of", "provisionally_satisfied" if b_done else "unsatisfied",
                 children=[a, b])
    return SimpleNamespace(requirements=[root])


def test_an_unneeded_alternative_is_useless_only_once_the_choice_is_met():
    from app.services.planning.engine import progress

    # Met by sequence B: progress on A is not useful (pruning removes it) ...
    met_with_a = _science(b_done=True, a_started=True)
    met_alone = _science(b_done=True, a_started=False)
    assert progress(met_with_a, useful_only=True) == progress(met_alone, useful_only=True)
    # ... while the search still sees it, so a dead-end first choice (an
    # unplannable lab) never strands the plan.
    assert progress(met_with_a) > progress(met_alone)
    # Not met yet: every alternative stays useful.
    open_with_a = _science(b_done=False, a_started=True)
    open_alone = _science(b_done=False, a_started=False)
    assert progress(open_with_a, useful_only=True) > progress(open_alone, useful_only=True)


# --------------------------------------------------------------------------
# 9. undeclared students planning toward an intended major
# --------------------------------------------------------------------------


def test_an_undeclared_student_plans_toward_the_intended_major(world):
    history = [*HISTORY[:4], ("01:640:152", "20261", "D"), HISTORY[5]]
    plan = _plan(world, _student(world, history, degree="BS", declaration="undeclared"))
    d = plan.declaration
    assert d.label == "Planning toward Computer Science B.S. (not yet declared)"
    assert plan.assumptions[0].code == "planning_toward_undeclared_major"
    assert "admission_not_modeled" not in {a.code for a in plan.assumptions}
    five = next(r for r in d.requirements if r.code == "DECLARE_FIVE_COURSES_C_OR_BETTER")
    assert five.min_grade == "C" and five.status == "not_met"
    assert five.courses == {"01:640:151": "met", "01:640:152": "below_minimum",
                            "01:198:111": "met", "01:198:112": "met", "01:198:205": "met"}
    assert set(five.sources) == {"cs_department", "sas_catalog"}
    urls = {s.id: s.url for s in d.sources}
    assert urls["cs_department"] == "https://www.cs.rutgers.edu/undergraduate/admission-major"
    assert urls["sas_catalog"].startswith("https://newbrunswick-26-27-undergrad.catalogs.rutgers.edu/")
    assert d.conflicts and "two" in d.conflicts[0] and "three" in d.conflicts[0]
    # No grade is repeated back anywhere in the declaration block.
    assert '"D"' not in d.model_dump_json()


def test_without_an_official_source_nothing_is_invented(world, tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.declaration.declaration_root", lambda: tmp_path)
    plan = _plan(world, _student(world, declaration="undeclared"))
    assert plan.declaration.requirements == [] and plan.declaration.sources == []
    assert "no official source" in plan.declaration.message


def test_a_declared_student_has_no_declaration_block(world):
    assert _plan(world, _student(world)).declaration is None
    assert _plan(world, _student(world, declaration="declared")).declaration is None


def _archive_text(path: pathlib.Path) -> str:
    raw = path.read_text(encoding="utf-8")
    raw = raw.replace("\\u003C", "<").replace("\\u003E", ">").replace('\\"', '"')
    text = html_lib.unescape(re.sub(r"<[^>]+>", " ", raw))
    return re.sub(r"\s+", " ", text)


def test_every_declaration_quote_is_verbatim_in_its_archived_page():
    data = json.loads((CURATED_DIR / "declaration" / "sas-198.json").read_bytes())
    sources = {s["id"]: s for s in data["sources"]}
    checked = 0
    for item in data["requirements"]:
        assert item["quotes"], item["code"]               # every condition is cited
        for q in item["quotes"]:
            source = sources[q["source"]]
            archive = ROOT / source["archive"]
            if not archive.exists():
                continue                                  # local evidence (data/raw is gitignored)
            digest = hashlib.sha256(archive.read_text(encoding="utf-8").encode()).hexdigest()
            assert digest.startswith(source["archive_sha256_prefix"]), source["id"]
            assert re.sub(r"\s+", " ", q["quote"]) in _archive_text(archive), q["quote"][:60]
            checked += 1
    if not checked:
        pytest.skip("declaration archives are not present on this machine")


# --------------------------------------------------------------------------
# 10. end to end, clock frozen in October 2026
# --------------------------------------------------------------------------


def test_end_to_end_october_2026_every_spring_2027_pick_is_traced(world, monkeypatch):
    from app.core.config import get_settings
    from app.services.planning.calendar import default_start
    from app.services.planning.service import latest_history_term

    monkeypatch.setenv("ACADEMIC_TODAY", "2026-10-15")
    get_settings.cache_clear()
    try:
        student = _dn_student(world)                       # synthetic DN + WebReg, CS B.A.
        start = default_start(latest_history_term(world, student))
        assert start == SPRING27
        plan = _plan(world, student, start_term=start)
    finally:
        monkeypatch.delenv("ACADEMIC_TODAY")
        get_settings.cache_clear()

    spring = _term(plan)
    assert spring is not None and spring.courses
    placed = _placed(plan)
    for pc in spring.courses:
        t = pc.trace
        assert t is not None, pc.course
        # traces to a requirement - its own, or one of a course it unlocks
        assert t.requirements or any(placed[u].requirements for u in t.unlocks_for
                                     if u in placed), pc.course
        for r in t.requirements:
            assert r.status_source in ("degree_navigator", "estimated")
        # ...to an offering in Spring 2027's own published schedule
        assert t.offering_kind is OfferingEvidenceKind.CONFIRMED_IN_TERM, pc.course
        offering = next(s for s in t.sources if s.fact == "offering")
        assert (offering.term_code, offering.source) == (SPRING27, "soc_courses_json")
        assert offering.data_source_id and offering.fetched_at
        # ...and to a prerequisite check
        assert t.prerequisite_state in (EligibilityState.SATISFIED_BY_HISTORY,
                                        EligibilityState.CONDITIONAL_ON_PLAN), pc.course
        if t.prerequisite_rules_term:
            assert any(s.fact == "prerequisite_rules" for s in t.sources)
        assert t.restriction is not None and t.restriction.outcome != "not_satisfied"
    # The DN audit decided the Core nodes; the WebReg courses unlocked spring work.
    assert any(r.status_source == "degree_navigator" for pc in spring.courses
               for r in pc.trace.requirements)
    assert any(d.source == "in_progress" for pc in spring.courses
               for d in pc.prerequisite.conditional_on)
    assert "DN_REQUIREMENT_OPEN_UNMAPPED" in {i.code for i in plan.issues}
    assert not (world.new or world.dirty or world.deleted)
