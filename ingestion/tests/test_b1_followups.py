"""Prompt 08 B1 follow-ups (owner decisions 2026-11-12):

(a) class-year choice: a class-scoped program needs the student's class year,
    which counts when the student states it, and is never guessed;
(b) a pre-business student still in SAS: SAS Core stays the requirement of
    record until admission is VERIFIED, RBS's core is shown alongside, gen-ed
    courses are labelled, and after admission the program school's gen-ed
    applies with what carried over;
(c) component review: a named human approves a component against its
    validated encoding; publication needs an approval of the current definition.

World: real public SOC records and real components (see test_program_expansion);
every program is SYNTHETIC and every student FABRICATED.
"""

from __future__ import annotations

import json

import pytest
from app.domain.course_ranking import CourseRankingEvidence
from app.services import program_lifecycle
from app.services.academic_context import (
    academic_evidence,
    composed_requirements,
    curriculum_applies,
    gen_ed_transition,
)
from app.services.onboarding import record_context
from app.services.planning.ranking import rank_key
from app.services.scenarios import CurriculumClassYearRequired, resolve_version
from sqlalchemy import select

from coursepilot_ingestion.loaders.components import ComponentLoader
from coursepilot_ingestion.pipelines.core import CoreIngestionPipeline

from . import test_program_expansion as tpe
from .conftest import CURATED_DIR, FIXTURE_DIR, SAS_CORE
from .test_program_expansion import _evidence, _load_program, _plan, _program, _student

world = tpe.world          # the same fixture
programs = tpe.programs
today = tpe.today


@pytest.fixture
def with_sas_core(programs, tmp_path):
    """SAS (the school) and SAS Core loaded into the B1 world."""
    session, p = programs
    _load_program(session, _program("SAS", "998", "Synthetic SAS program (TEST)", [
        {"code": "SYN_SAS", "name": "x", "requirement_type": "course", "parent": None,
         "courses": ["01:119:115"]}]))
    session.commit()
    terms = json.loads((FIXTURE_DIR / "soc_program_expansion_terms.json").read_bytes())
    combined = {r["courseString"]: r for records in terms.values() for r in records}
    archive = tmp_path / "combined.json"
    archive.write_text(json.dumps(list(combined.values())), "utf-8")
    CoreIngestionPipeline(session).run(SAS_CORE, archive, observed_term_code=tpe.SPRING27)
    session.commit()
    return session, p


# --------------------------------------------------------------------------
# (a) class year
# --------------------------------------------------------------------------


@pytest.fixture
def curricula(world):
    old = _load_program(world, _program("SOE", "996", "Synthetic curriculum (TEST)", [
        {"code": "C_OLD", "name": "old", "requirement_type": "course", "parent": None,
         "courses": ["01:640:152"]}], key="classes-2026-2029", class_years=[2026, 2027, 2028, 2029]))
    new = _load_program(world, _program("SOE", "996", "Synthetic curriculum (TEST)", [
        {"code": "C_NEW", "name": "new", "requirement_type": "course", "parent": None,
         "courses": ["14:440:222"]}], key="class-2030", class_years=[2030]))
    world.commit()
    return world, old, new


def test_a_class_scoped_program_asks_for_the_class_year(curricula):
    session, old, new = curricula
    with pytest.raises(CurriculumClassYearRequired) as exc:
        resolve_version(session, "soe-996-bs", "2026-2027")
    assert exc.value.class_years == [2026, 2027, 2028, 2029, 2030]
    assert resolve_version(session, "soe-996-bs", "2026-2027", 2030)[0].id == new.id
    assert resolve_version(session, "soe-996-bs", "2026-2027", 2027)[0].id == old.id
    with pytest.raises(CurriculumClassYearRequired, match="no curriculum"):
        resolve_version(session, "soe-996-bs", "2026-2027", 2031)     # never extrapolated


def test_the_students_stated_class_year_governs(curricula):
    session, old, new = curricula
    student = _student(session, new, ref="cy-stated")
    record_context(session, student, new, program_key="soe-996-bs", class_year=2030,
                   home_school_code=None)
    session.commit()
    ev = academic_evidence(session, student)
    ok, why = curriculum_applies(new, ev)
    assert ok is True and "you stated" in why
    assert curriculum_applies(old, ev)[0] is False
    # Restating replaces the earlier statement (never two competing claims).
    record_context(session, student, old, program_key="soe-996-bs", class_year=2027,
                   home_school_code=None)
    session.commit()
    years = [c.value["class_year"] for c in academic_evidence(session, student)
             if c.evidence_kind == "curriculum_class_year"]
    assert years == [2027]


# --------------------------------------------------------------------------
# (b) pre-business students still in SAS
# --------------------------------------------------------------------------


def test_sas_core_is_the_requirement_of_record_until_admission_is_verified(with_sas_core):
    session, p = with_sas_core
    rbs = p["rbs"]
    student = _student(session, rbs, ref="prebiz-sas")
    student.home_school_code = "SAS"
    session.commit()
    codes = {r.code for r in composed_requirements(session, rbs, student)}
    assert "SAS_CORE" in codes and "RBS_CORE" not in codes
    assert {"RBS_BUSINESS_CORE", "RBS_FOUNDATIONS"} <= codes        # linked parts unchanged
    t = gen_ed_transition(session, rbs, student)
    assert t["state"] == "before_admission"
    assert [s.code for s in t["record"]] == ["sas-core"]
    assert [s.code for s in t["target"]] == ["rbs-core"]
    # A self-statement or an unverified record is not admission.
    _evidence(session, student, "admission", "rbs-nb-admission", {"status": "admitted"},
              verified=False)
    assert "SAS_CORE" in {r.code for r in composed_requirements(session, rbs, student)}
    _evidence(session, student, "admission", "rbs-nb-admission", {"status": "admitted"})
    codes = {r.code for r in composed_requirements(session, rbs, student)}
    assert "RBS_CORE" in codes and "SAS_CORE" not in codes
    assert gen_ed_transition(session, rbs, student)["state"] == "admitted"


def test_a_pre_business_plan_shows_both_systems_and_labels_gen_ed(with_sas_core, today):
    session, p = with_sas_core
    student = _student(session, p["rbs"], [("33:010:272", "20269", "A")], ref="prebiz-plan")
    student.home_school_code = "SAS"
    session.commit()
    plan = _plan(session, student)
    assert plan.gen_ed is not None and plan.gen_ed.state == "before_admission"
    assert plan.gen_ed.record == ["SAS Core Curriculum"]
    assert plan.gen_ed.target_lists_known is False             # RBS lists are unknown
    assert "stays your requirement of record" in plan.gen_ed.message
    assert plan.school_core.status == "encoded" and "school you are in now" in \
        plan.school_core.message
    labelled = [pc for t in plan.terms for pc in t.courses if pc.gen_ed_label]
    for pc in labelled:
        assert "unknown" in pc.gen_ed_label                    # never guessed "both"
    # A student IN the program's school sees no transition at all.
    plain = _student(session, p["rbs"], [("33:010:272", "20269", "A")], ref="in-rbs")
    assert _plan(session, plain).gen_ed is None


def test_known_target_lists_label_both_and_prefer_both(with_sas_core):
    session, _ = with_sas_core
    # A SYNTHETIC SEBS program with an admission stage: SEBS Core's lists are
    # SOC certifications, so "counts for both" can be decided.
    d = _program("SEBS", "995", "Synthetic SEBS transfer program (TEST)", [
        {"code": "SYN_T", "name": "x", "requirement_type": "course", "parent": None,
         "courses": ["01:119:116"]}], stage="sebs-test-stage")
    version = _load_program(session, d)
    session.commit()
    student = _student(session, version, ref="sas-to-sebs")
    student.home_school_code = "SAS"
    session.commit()
    from app.services.planning.service import gen_ed_context, label_gen_ed
    ctx, record_codes, target_courses, known = gen_ed_context(session, student, version)
    assert known is True and ctx.target == ["SEBS Core Curriculum"]

    class _R:   # a minimal plan: one SAS Core course that SEBS Core also certifies
        def __init__(self, course, code):
            from types import SimpleNamespace
            self.terms = [SimpleNamespace(courses=[SimpleNamespace(
                course=course, gen_ed_label=None,
                requirements=[SimpleNamespace(requirement_code=code)])])]
    both = sorted(target_courses)[0]
    plan = _R(both, next(iter(record_codes)))
    label_gen_ed(plan, ctx, record_codes, target_courses, known)
    assert plan.terms[0].courses[0].gen_ed_label == "counts for both"
    only = _R("99:999:999", next(iter(record_codes)))
    label_gen_ed(only, ctx, record_codes, target_courses, known)
    assert only.terms[0].courses[0].gen_ed_label == "SAS Core Curriculum only"
    # Ranking: all else equal, the course counting for both comes first.
    a = CourseRankingEvidence(counts_for_target_gen_ed=True)
    b = CourseRankingEvidence(counts_for_target_gen_ed=False)
    assert rank_key("01:000:999", a) < rank_key("01:000:001", b)


# --------------------------------------------------------------------------
# (c) component review
# --------------------------------------------------------------------------


def _comp(session, code):
    from app.models import SchoolRequirementSetVersion
    return session.scalar(select(SchoolRequirementSetVersion).where(
        SchoolRequirementSetVersion.code == code))


def test_a_named_human_reviews_and_publishes_a_component(world):
    comp = _comp(world, "soe-first-year")
    with pytest.raises(program_lifecycle.LifecycleError, match="not a human"):
        program_lifecycle.record_component_review(world, comp, reviewer="Claude",
                                                  decision="approved", machine_checks_passed=True)
    with pytest.raises(program_lifecycle.LifecycleError, match="does not pass validation"):
        program_lifecycle.record_component_review(world, comp, reviewer="Jane Reviewer",
                                                  decision="approved", machine_checks_passed=False)
    with pytest.raises(program_lifecycle.LifecycleError):
        program_lifecycle.publish_component(world, comp)          # nothing approved yet
    program_lifecycle.record_component_review(world, comp, reviewer="Jane Reviewer",
                                              decision="approved", machine_checks_passed=True)
    assert comp.lifecycle_state == "reviewed"
    program_lifecycle.publish_component(world, comp)
    assert comp.lifecycle_state == "published"
    # A changed definition needs re-review; the old approval cannot publish it.
    d = json.loads((CURATED_DIR / "components" / "soe-first-year.json").read_text("utf-8"))
    d["requirements"][1]["name"] = "General Chemistry for Engineers I (renamed)"
    ComponentLoader(world).load(d, json.dumps(d).encode())
    assert comp.lifecycle_state == "needs_rereview"
    with pytest.raises(program_lifecycle.LifecycleError):
        program_lifecycle.publish_component(world, comp)


def test_changes_requested_keeps_a_component_unpublished(world):
    comp = _comp(world, "rbs-entry")
    program_lifecycle.record_component_review(world, comp, reviewer="Jane Reviewer",
                                              decision="changes_requested",
                                              machine_checks_passed=True, notes="numbers?")
    assert comp.lifecycle_state == "pending_review"
    with pytest.raises(program_lifecycle.LifecycleError):
        program_lifecycle.publish_component(world, comp)

