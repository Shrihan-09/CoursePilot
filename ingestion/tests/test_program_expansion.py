"""Prompt 08 B1: the engine and data model for every NB undergrad school.

World: REAL public SOC course records (fixtures/soc_program_expansion_terms.json,
Fall 2026 + Spring 2027: the RBS business core, the SoE first-year program,
a few certified courses per Core/H&SS code) with the REAL school components
(data/programs/.../components/*.json, pending review). Every PROGRAM below is
SYNTHETIC (curation_status "synthetic", codes 99x) and every student is
FABRICATED; the clock is frozen in October 2026 and plans start Spring 2027.

Covers: linked components and school gen-ed composition, a shared major in
two schools, entry stages and "after admission" labels, inherited grade
floors, per-course credit minimums, group caps, class-year curricula,
school-level rules, degree totals, certification-code eligibility, the
loaders' refusals, and the course knowledge index.
"""

from __future__ import annotations

import copy
import json
from decimal import Decimal

import pytest
from app.domain.grades import Tri
from app.domain.planning import FillerPreference, Pace, PlanningConstraints, WorkloadLevel
from app.models import (
    Course,
    Program,
    ProgramVersion,
    SchoolRequirementSetVersion,
    Student,
    StudentAcademicEvidence,
    StudentCourse,
)
from app.services import knowledge
from app.services.academic_context import (
    academic_evidence,
    attempt_eligibility,
    composed_requirements,
    composed_rules,
    curriculum_applies,
    passed_stages,
    requirement_exemptions,
    school_requirement_sets,
)
from app.services.audit.engine import DegreeAuditEngine, _constraint_checks
from app.services.planning.degree_total import degree_total_for
from app.services.planning.service import generate_plan
from sqlalchemy import select

from coursepilot_ingestion.loaders.components import ComponentLoader
from coursepilot_ingestion.loaders.requirements import RequirementLoader
from coursepilot_ingestion.pipelines.courses import CourseIngestionPipeline
from coursepilot_ingestion.pipelines.sections import SectionIngestionPipeline
from coursepilot_ingestion.sources.soc import SocQuery

from .conftest import CURATED_DIR, FIXTURE_DIR

SPRING27, FALL26 = "20271", "20269"
COMPONENTS = CURATED_DIR / "components"
LOAD_ORDER = ["sebs-core", "rbs-core", "soe-school", "rbs-business-core", "rbs-entry",
              "soe-first-year", "soe-first-year-chem-ii", "soe-major-declaration"]


@pytest.fixture
def today(monkeypatch):
    monkeypatch.setenv("ACADEMIC_TODAY", "2026-10-15")


def _program(school, code, name, nodes, *, components=(), stage=None, total=None, key="",
             class_years=None):
    """A SYNTHETIC program definition (never real Rutgers data)."""
    schools = {"RBSNB": "Rutgers Business School-New Brunswick", "SOE": "School of Engineering",
               "SEBS": "School of Environmental and Biological Sciences",
               "SAS": "School of Arts and Sciences"}
    version = {"catalog_year": "2026-2027", "curriculum_key": key}
    if stage:
        version["admission_stage"] = stage
    if total:
        version["degree_credits"] = {"credits": total, "capture": None, "quote": "SYNTHETIC"}
    if class_years:
        version["curriculum_class_years"] = class_years
    return {
        "source": {"url": "https://example.invalid/synthetic", "catalog_year": "2026-2027",
                   "retrieved_at": "2026-11-11", "kind": "synthetic",
                   "curation_status": "unverified"},
        "school": {"code": school, "name": schools[school], "campus_code": "NB"},
        "program": {"code": code, "name": name, "degree_type": "BS"},
        "program_version": version,
        "curation": {"catalog_page": "/synthetic", "curated_by": "test"},
        "requirements": nodes,
        "components": list(components),
    }


def _load_program(session, definition):
    RequirementLoader(session).load(definition, json.dumps(definition).encode())
    session.flush()
    from app.models import School
    return session.scalar(select(ProgramVersion).join(Program).join(
        School, School.id == Program.school_id).where(
        School.code == definition["school"]["code"],
        Program.code == definition["program"]["code"],
        ProgramVersion.curriculum_key == definition["program_version"].get("curriculum_key", "")))


RBS_MAJOR = _program("RBSNB", "990", "Synthetic Business Major (TEST)", [
    {"code": "SYN_BIZ", "name": "Synthetic business major", "requirement_type": "all_of",
     "parent": None},
    {"code": "SYN_BIZ_388", "name": "Foundations of Business Programming",
     "requirement_type": "course", "parent": "SYN_BIZ", "courses": ["33:136:388"]},
], components=["rbs-business-core", "rbs-entry"], stage="rbs-nb-admission")
SOE_MAJOR = _program("SOE", "991", "Synthetic Engineering Major (TEST)", [
    {"code": "SYN_ENG", "name": "Synthetic engineering major", "requirement_type": "all_of",
     "parent": None},
    {"code": "SYN_ENG_222", "name": "Engineering Mechanics: Dynamics",
     "requirement_type": "course", "parent": "SYN_ENG", "courses": ["14:440:222"]},
], components=["soe-first-year", "soe-first-year-chem-ii", "soe-major-declaration"],
    stage="soe-major-declaration", total=128)
SEBS_MAJOR = _program("SEBS", "992", "Synthetic Life Science Major (TEST)", [
    {"code": "SYN_BIO", "name": "Synthetic biology major", "requirement_type": "all_of",
     "parent": None},
    {"code": "SYN_BIO_115", "name": "General Biology I", "requirement_type": "course",
     "parent": "SYN_BIO", "courses": ["01:119:115"]},
    {"code": "SYN_BIO_116", "name": "General Biology II", "requirement_type": "course",
     "parent": "SYN_BIO", "courses": ["01:119:116"]},
])


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
    for code in LOAD_ORDER:
        ComponentLoader(session).load_file(COMPONENTS / f"{code}.json")
    session.commit()
    return session


@pytest.fixture
def programs(world):
    out = {k: _load_program(world, copy.deepcopy(d))
           for k, d in (("rbs", RBS_MAJOR), ("soe", SOE_MAJOR), ("sebs", SEBS_MAJOR))}
    world.commit()
    return world, out


def _student(session, version, history=(), ref="b1"):
    student = Student(external_ref=f"{ref}-{version.program.code}", catalog_year="2026-2027",
                      program_version_id=version.id, first_term="20269")
    session.add(student)
    session.flush()
    for key, term, grade in history:
        course = session.scalar(select(Course).where(Course.course_string == key,
                                                     Course.supplement_code == ""))
        session.add(StudentCourse(student_id=student.id, course_id=course.id, term_code=term,
                                  status="completed", grade=grade,
                                  credits_earned=course.credits if grade not in ("F",) else 0))
    session.commit()
    return student


def _evidence(session, student, kind, key, value, verified=True):
    session.add(StudentAcademicEvidence(student_id=student.id, evidence_kind=kind,
                                        evidence_key=key, value=value, verified=verified,
                                        source_reference="SYNTHETIC test record"))
    session.commit()


def _plan(session, student):
    return generate_plan(session, student, start_term=SPRING27, constraints=PlanningConstraints(
        workload_level=WorkloadLevel.SEMI_RIGOROUS, filler_preference=FillerPreference.FLEXIBLE,
        max_courses_per_term=6, pace=Pace.BALANCED))


def _codes(session, version):
    return {r.code for r in composed_requirements(session, version)}


# --------------------------------------------------------------------------
# composition
# --------------------------------------------------------------------------


def test_school_gen_ed_composes_by_school_and_linked_components_only_by_link(programs):
    session, p = programs
    rbs = _codes(session, p["rbs"])
    assert {"RBS_CORE", "RBS_BUSINESS_CORE", "RBS_FOUNDATIONS", "SYN_BIZ"} <= rbs
    assert not rbs & {"SEBS_CORE", "SOE_SCHOOL", "SOE_FY"}
    soe = _codes(session, p["soe"])
    assert {"SOE_SCHOOL", "SOE_FY", "SOE_FY_CHEM_160", "SYN_ENG"} <= soe
    assert "RBS_BUSINESS_CORE" not in soe
    # A second RBS program that links nothing gets the school gen-ed only.
    bare = _load_program(session, _program("RBSNB", "993", "Synthetic minor-like (TEST)", [
        {"code": "SYN_X", "name": "x", "requirement_type": "course", "parent": None,
         "courses": ["33:136:388"]}]))
    codes = _codes(session, bare)
    assert "RBS_CORE" in codes and "RBS_BUSINESS_CORE" not in codes


def test_a_linked_major_component_never_displaces_the_programs_own_nodes(programs):
    """A shared major is authored once and linked; the program keeps its own
    'major' nodes (the old supersede-by-system rule applies to gen-ed only)."""
    session, p = programs
    codes = _codes(session, p["rbs"])
    assert "SYN_BIZ" in codes and "RBS_BUSINESS_CORE" in codes


def test_one_major_under_two_schools_keeps_each_schools_gen_ed(world):
    shared = {
        "format": "component/1",
        "component": {"code": "syn-shared-bio", "name": "Synthetic shared major (TEST)",
                      "kind": "shared_major", "composition": "linked",
                      "school": {"code": "SEBS", "name": "x", "campus_code": "NB"},
                      "catalog_year": "2026-2027", "source_url": "https://example.invalid",
                      "requirement_system": "major"},
        "requirements": [{"code": "SHARED_BIO", "name": "Shared", "requirement_type": "course",
                          "parent": None, "courses": ["01:119:115"]}],
        "curation": {"encoded_at": "2026-11-11"},
    }
    ComponentLoader(world).load(shared, json.dumps(shared).encode())
    sas = _load_program(world, _program("SAS", "994", "Shared (SAS, TEST)", [
        {"code": "SAS_OWN", "name": "own", "requirement_type": "course", "parent": None,
         "courses": ["01:119:116"]}], components=["syn-shared-bio"]))
    sebs = _load_program(world, _program("SEBS", "994", "Shared (SEBS, TEST)", [
        {"code": "SEBS_OWN", "name": "own", "requirement_type": "course", "parent": None,
         "courses": ["01:119:116"]}], components=["syn-shared-bio"]))
    a, b = _codes(world, sas), _codes(world, sebs)
    assert "SHARED_BIO" in a and "SHARED_BIO" in b              # authored once, used twice
    assert "SEBS_CORE" in b and "SEBS_CORE" not in a            # gen-ed by the PROGRAM's school
    shared_rows = world.scalars(select(SchoolRequirementSetVersion).where(
        SchoolRequirementSetVersion.code == "syn-shared-bio")).all()
    assert len(shared_rows) == 1


def test_a_named_component_that_is_not_loaded_is_refused(world):
    bad = _program("SOE", "995", "x", [{"code": "Q", "name": "q", "requirement_type": "course",
                                         "parent": None, "courses": ["14:440:222"]}],
                   components=["no-such-component"])
    with pytest.raises(ValueError, match="not loaded"):
        RequirementLoader(world).load(bad, json.dumps(bad).encode())


# --------------------------------------------------------------------------
# stages: entry requirements in the plan, "after admission" labels
# --------------------------------------------------------------------------


def test_entry_stage_requirements_apply_until_admission_is_verified(programs):
    session, p = programs
    student = _student(session, p["rbs"], ref="pre")
    reqs = composed_requirements(session, p["rbs"])
    assert "RBS_FOUNDATIONS" not in requirement_exemptions(reqs, academic_evidence(session, student))
    # An UNVERIFIED claim changes nothing; course completion never grants admission.
    _evidence(session, student, "admission", "rbs-nb-admission", {"status": "admitted"},
              verified=False)
    assert passed_stages(academic_evidence(session, student)) == set()
    _evidence(session, student, "admission", "rbs-nb-admission", {"status": "admitted"})
    assert "RBS_FOUNDATIONS" in requirement_exemptions(reqs, academic_evidence(session, student))


def test_pre_business_plan_includes_entry_requirements_and_never_assumes_admission(programs,
                                                                                    today):
    session, p = programs
    student = _student(session, p["rbs"], [("33:010:272", "20269", "A")], ref="prebiz")
    plan = _plan(session, student)
    planned = [pc for t in plan.terms for pc in t.courses]
    business = [pc for pc in planned if any(r.requirement_code.startswith(("RBS_BC", "SYN_BIZ"))
                                            for r in pc.requirements)]
    assert business, "the plan places business core courses"
    for pc in business:
        assert pc.after_stage == "rbs-nb-admission"
        assert pc.after_stage_label == "after admission to RBS"
        assert any("does not assume" in n for n in pc.notes)
    # The foundations are part of the plan's requirements (unknown lists stay open).
    audit = DegreeAuditEngine(session).audit(student, program_version=p["rbs"])
    found = {r.requirement_code for r in _walk(audit.requirements)}
    assert {"RBS_FOUNDATION_MICRO", "RBS_FOUNDATION_CALCULUS"} <= found


def test_admitted_student_gets_no_admission_labels(programs, today):
    session, p = programs
    student = _student(session, p["rbs"], [("33:010:272", "20269", "A")], ref="admitted")
    _evidence(session, student, "admission", "rbs-nb-admission", {"status": "admitted"})
    planned = [pc for t in _plan(session, student).terms for pc in t.courses]
    assert planned and all(pc.after_stage is None for pc in planned)


def test_first_year_engineer_plan_labels_major_courses_after_declaration(programs, today):
    session, p = programs
    student = _student(session, p["soe"], [("01:640:151", "20269", "B"),
                                           ("01:750:123", "20269", "B"),
                                           ("14:440:101", "20269", "A")], ref="fy")
    planned = {pc.course: pc for t in _plan(session, student).terms for pc in t.courses}
    first_year = [pc for c, pc in planned.items() if c in {"01:640:152", "01:750:124",
                                                           "14:440:102", "14:440:221"}]
    assert first_year, "the first-year program is planned"
    assert all(pc.after_stage is None for pc in first_year)   # 004 comes BEFORE declaration
    if "14:440:222" in planned:
        assert planned["14:440:222"].after_stage_label == "after major declaration"


# --------------------------------------------------------------------------
# grade floors, credit minimums, group caps
# --------------------------------------------------------------------------


def _walk(nodes):
    for n in nodes:
        yield n
        yield from _walk(n.children)


def test_c_or_better_on_a_linked_core_is_inherited_by_every_course(programs):
    session, p = programs
    student = _student(session, p["rbs"], [("33:010:272", "20269", "D")], ref="dgrade")
    _evidence(session, student, "admission", "rbs-nb-admission", {"status": "admitted"})
    audit = DegreeAuditEngine(session).audit(student, program_version=p["rbs"])
    node = next(n for n in _walk(audit.requirements) if n.requirement_code == "RBS_BC_010_272")
    assert node.status.value not in ("satisfied", "provisionally_satisfied")


class _Attempt:
    def __init__(self, grade="A"):
        self.credit_origin, self.status, self.grade, self.term_code = "rutgers", "completed", \
            grade, SPRING27


def test_a_course_below_the_per_course_credit_minimum_never_counts(programs):
    session, p = programs
    root = next(r for r in composed_requirements(session, p["sebs"]) if r.code == "SEBS_CORE")
    assert root.min_course_credits == Decimal(3)
    one_credit = type("C", (), {"credits": Decimal(1), "course_string": "x"})()
    three = type("C", (), {"credits": Decimal(3), "course_string": "x"})()
    assert attempt_eligibility(root, _Attempt(), one_credit)[0] is Tri.NO
    assert attempt_eligibility(root, _Attempt(), three)[0] is Tri.YES


def test_group_cap_limits_independent_study(programs):
    req = type("R", (), {"max_outside_subject": None, "constraint_subject_code": None,
                         "min_at_level": None, "min_at_level_count": None,
                         "group_caps": [{"courses": ["01:198:493", "01:198:494"], "max": 1}]})()
    two = [{"course_string": "01:198:493", "subject_code": "198", "course_number": "493"},
           {"course_string": "01:198:494", "subject_code": "198", "course_number": "494"}]
    assert _constraint_checks(req, two[:1]) == []
    assert "at most 1" in _constraint_checks(req, two)[0]


# --------------------------------------------------------------------------
# class-year curricula, school rules, totals
# --------------------------------------------------------------------------


def test_class_year_curricula_coexist_and_are_never_inferred(world):
    old = _load_program(world, _program("SOE", "996", "Synthetic curriculum (TEST)", [
        {"code": "C_OLD", "name": "old", "requirement_type": "course", "parent": None,
         "courses": ["01:160:160"]}], key="classes-2026-2029", class_years=[2026, 2027, 2028, 2029]))
    new = _load_program(world, _program("SOE", "996", "Synthetic curriculum (TEST)", [
        {"code": "C_NEW", "name": "new", "requirement_type": "course", "parent": None,
         "courses": ["14:440:222"]}], key="class-2030", class_years=[2030]))
    assert old.id != new.id and old.program_id == new.program_id
    student = _student(world, new, ref="cy")
    ev = academic_evidence(world, student)
    assert curriculum_applies(new, ev)[0] is None              # unknown: never guessed
    _evidence(world, student, "curriculum_class_year", "soe-996", {"class_year": 2030},
              verified=False)
    assert curriculum_applies(new, academic_evidence(world, student))[0] is None
    _evidence(world, student, "curriculum_class_year", "soe-996", {"class_year": 2030})
    ev = academic_evidence(world, student)
    assert curriculum_applies(new, ev)[0] is True and curriculum_applies(old, ev)[0] is False


def test_school_rules_attach_once_and_unevaluated_ones_say_why(programs):
    session, p = programs
    soe_rules = {r.code: r for r in composed_rules(session, p["soe"])}
    assert {"SOE_MIN_GPA", "SOE_LAST_42", "SOE_MAJOR_GPA"} <= set(soe_rules)
    assert soe_rules["SOE_LAST_42"].is_evaluable is False
    assert soe_rules["SOE_LAST_42"].not_evaluable_reason
    student = _student(session, p["soe"], ref="rules")
    audit = DegreeAuditEngine(session).audit(student, program_version=p["soe"])
    statuses = {r.rule_code: r.status.value for r in audit.rules}
    assert statuses["SOE_LAST_42"] == "not_evaluable"
    assert "RBS_MIN_GPA" not in statuses


def test_unevaluated_rule_types_must_be_marked_not_evaluable():
    from coursepilot_ingestion.loaders.components import validate_component_shape

    d = json.loads((COMPONENTS / "soe-school.json").read_text("utf-8"))
    d["school_rules"][2]["is_evaluable"] = True
    assert any("not evaluated yet" in e for e in validate_component_shape(d))
    d = json.loads((COMPONENTS / "rbs-entry.json").read_text("utf-8"))
    del d["component"]["stage"]
    assert any("stage" in e for e in validate_component_shape(d))
    d = json.loads((COMPONENTS / "sebs-core.json").read_text("utf-8"))
    d["component"]["applicability"]["entry_gpa"] = 3
    assert any("applicability" in e for e in validate_component_shape(d))


def test_degree_totals_come_from_the_program_then_the_school_and_are_never_assumed(programs):
    session, p = programs
    assert degree_total_for(session, p["soe"]).credits == Decimal(128)      # program's own
    assert degree_total_for(session, p["rbs"]).credits == Decimal(120)      # RBS school total
    assert "120 degree credits" in degree_total_for(session, p["rbs"]).quote
    assert degree_total_for(session, p["sebs"]).credits == Decimal(120)
    bare = _load_program(session, _program("SOE", "997", "No total (TEST)", [
        {"code": "Z", "name": "z", "requirement_type": "course", "parent": None,
         "courses": ["14:440:222"]}]))
    assert degree_total_for(session, bare) is None                          # SoE: per major


def test_certified_nodes_draw_eligibility_from_soc_certifications(programs):
    session, p = programs
    soehs = next(r for r in composed_requirements(session, p["soe"])
                 if r.code == "SOE_HSS_FIRST_YEAR")
    from app.models import RequirementCourseOption
    rows = session.scalars(select(RequirementCourseOption).where(
        RequirementCourseOption.requirement_id == soehs.id)).all()
    assert rows and {r.category for r in rows} == {"SOEHS"}
    # Never a course that is not certified SOEHS in some loaded term.
    from app.models.core_codes import OfferingCoreCode
    certified = set(session.scalars(select(OfferingCoreCode.course_id).where(
        OfferingCoreCode.code == "SOEHS")))
    assert {r.course_id for r in rows} <= certified


def test_component_loader_is_idempotent_and_a_change_resets_review(world):
    comp = world.scalar(select(SchoolRequirementSetVersion).where(
        SchoolRequirementSetVersion.code == "sebs-core"))
    comp.lifecycle_state = "published"
    world.commit()
    again = ComponentLoader(world).load_file(COMPONENTS / "sebs-core.json")
    assert not again.inserted and not again.definition_changed and again.requirements_inserted == 0
    assert comp.lifecycle_state == "published"
    d = json.loads((COMPONENTS / "sebs-core.json").read_text("utf-8"))
    d["requirements"][7]["min_count"] = 3
    changed = ComponentLoader(world).load(d, json.dumps(d).encode())
    assert changed.definition_changed and comp.lifecycle_state != "published"


# --------------------------------------------------------------------------
# the course knowledge index
# --------------------------------------------------------------------------


def test_index_is_term_scoped_labelled_and_honest_about_absence(programs):
    session, p = programs
    build = knowledge.build_index(session)
    session.commit()
    assert build.state == "ready" and build.counts["memberships"] > 0
    view = knowledge.KnowledgeView(session, p["rbs"], SPRING27)
    core = view.judge("33:630:301")
    assert core.status == "mapped"
    fill = next(f for f in core.fills if f.requirement_code == "RBS_BC_630_301")
    assert fill.after_stage == "rbs-nb-admission" and fill.min_grade == "C"
    assert fill.pending_review                                # components are not published
    ethics_law = view.judge("33:140:320")
    assert ethics_law.status == "mapped"
    # A course no node maps: UNKNOWN (coverage incomplete), never "free elective only".
    other = view.judge("01:119:115")
    assert other.status == "unknown" and other.reasons
    # Term scoping: SOEHS membership exists only for terms that certify the course.
    soe = knowledge.KnowledgeView(session, p["soe"], SPRING27)
    from app.models.core_codes import OfferingCoreCode
    spring_soehs = set(session.scalars(select(Course.course_string).join(
        OfferingCoreCode, OfferingCoreCode.course_id == Course.id).where(
        OfferingCoreCode.code == "SOEHS", OfferingCoreCode.term_code == SPRING27)))
    for course in spring_soehs:
        assert any(f.requirement_code == "SOE_HSS_FIRST_YEAR" for f in soe.judge(course).fills)
    fall_only = set(session.scalars(select(Course.course_string).join(
        OfferingCoreCode, OfferingCoreCode.course_id == Course.id).where(
        OfferingCoreCode.code == "SOEHS", OfferingCoreCode.term_code == FALL26))) - spring_soehs
    for course in fall_only:
        assert not any(f.requirement_code == "SOE_HSS_FIRST_YEAR"
                       for f in soe.judge(course).fills)


def test_free_elective_only_requires_complete_reviewed_coverage(programs):
    session, p = programs
    # Publish every owner of the SEBS context: still incomplete (open unknowns? none in SEBS
    # Core) -> only when nothing blocks may absence mean "free elective only".
    sebs = p["sebs"]
    sebs.lifecycle_state, sebs.publication_basis = "published", "human_review"
    for comp in school_requirement_sets(session, sebs):
        comp.lifecycle_state = "published"
    session.commit()
    knowledge.build_index(session)
    session.commit()
    view = knowledge.KnowledgeView(session, sebs, SPRING27)
    assert view.judge("33:136:388").status == "free_elective_only"
    # Unpublished again -> unknown, with the reason.
    sebs.lifecycle_state, sebs.publication_basis = "validated", None
    session.commit()
    stale = knowledge.KnowledgeView(session, sebs, SPRING27)
    assert stale.stale and stale.judge("33:136:388").status == "unknown"


def test_index_manifest_is_deterministic_and_detects_changes(programs):
    session, p = programs
    a = knowledge.manifest(session)
    assert knowledge.manifest(session)["sha256"] == a["sha256"]
    build = knowledge.ensure_fresh(session)
    session.commit()
    assert knowledge.ensure_fresh(session).id == build.id     # reused, not rebuilt
    comp = session.scalar(select(SchoolRequirementSetVersion).where(
        SchoolRequirementSetVersion.code == "rbs-core"))
    comp.lifecycle_state = "published"
    session.commit()
    assert not knowledge.is_fresh(session, build)
    rebuilt = knowledge.ensure_fresh(session)
    assert rebuilt.id != build.id and rebuilt.state == "ready"


def test_a_failed_build_never_replaces_the_ready_one(programs, monkeypatch):
    session, _ = programs
    good = knowledge.build_index(session)
    session.commit()

    def boom(*a, **k):
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(knowledge, "_populate", boom)
    with pytest.raises(RuntimeError):
        knowledge.build_index(session)
    session.commit()
    assert knowledge.current_build(session).id == good.id


# --------------------------------------------------------------------------
# the real encodings
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name", LOAD_ORDER)
def test_every_component_encoding_validates_against_its_archived_sources(name):
    from coursepilot_ingestion.encoding.sources import resolve, load_manifest
    from coursepilot_ingestion.encoding.validate import validate_file

    manifest = load_manifest()
    report = validate_file(COMPONENTS / f"{name}.json", manifest=manifest)
    assert report.ok, report.errors
    cited = json.loads((COMPONENTS / f"{name}.json").read_text("utf-8"))["sources"]
    present = [s for s in cited if s["id"] in manifest and resolve(manifest[s["id"]])]
    if present:                     # archives are local evidence (gitignored)
        assert report.quotes_found == report.quotes_checked > 0


def test_a_quote_that_is_not_on_the_page_fails_validation(tmp_path):
    from coursepilot_ingestion.encoding.sources import load_manifest, resolve
    from coursepilot_ingestion.encoding.validate import validate_file

    manifest = load_manifest()
    if not resolve(manifest.get("sebs-catalog-core", {})):
        pytest.skip("archived SEBS catalog page not present locally")
    d = json.loads((COMPONENTS / "sebs-core.json").read_text("utf-8"))
    d["requirements"][2]["quotes"][0]["quote"] = "Students must take three Diversity courses."
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(d), "utf-8")
    report = validate_file(path, manifest=manifest)
    assert not report.ok and any("not found verbatim" in e for e in report.errors)
