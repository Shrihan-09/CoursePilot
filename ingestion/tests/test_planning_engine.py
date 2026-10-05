"""The Planning Engine through the REAL ingestion pipeline (Phase 6.5).

Real archived SOC records (CS and Math samples) loaded for several terms by
the production pipeline, with the real curated CS and Math Option A
programs. Where a scenario needs a rule the samples do not contain, a copy
of a real record is modified and the change is labelled SYNTHETIC with the
real Rutgers wording it imitates.

Terms loaded (the planner's offering evidence):

    20259 Fall 2025    every sample record        coverage: complete
    20261 Spring 2026  all but 352, 416 (CS)      coverage: complete
    20269 Fall 2026    every sample record        coverage: complete
    20271 Spring 2027  only 314 and 336 (CS)      coverage: partial

Plans start in Spring 2027: 314/336 are CONFIRMED there, the rest of the
spring candidates are HISTORICAL (20261), and 352/416 have been offered only
in fall.
"""

from __future__ import annotations

import hashlib
import json

import pytest
from app.domain.planning import (
    EligibilityState,
    OfferingEvidenceKind,
    PlanningConstraints,
    PlanStatus,
    Reason,
    Severity,
)
from app.models import Course, ProgramVersion, Student, StudentCourse
from app.services.audit.engine import DegreeAuditEngine
from app.services.planning.offerings import PLACEABLE, OfferingIndex
from app.services.planning.service import InvalidStartTerm, generate_plan
from app.services.scenarios import ProgramChoiceRequired
from sqlalchemy import select, text

from coursepilot_ingestion.loaders.requirements import RequirementLoader
from coursepilot_ingestion.pipelines.courses import CourseIngestionPipeline
from coursepilot_ingestion.sources.soc import SocQuery

from .conftest import CS_REQUIREMENTS, FIXTURE_DIR, MATH_REQUIREMENTS

START = "20271"
CS_DONE = [("01:198:111", "20259"), ("01:198:112", "20261"), ("01:198:205", "20259"),
           ("01:198:206", "20261"), ("01:198:211", "20261"), ("01:198:314", "20269"),
           ("01:198:336", "20269"), ("01:640:151", "20259"), ("01:640:152", "20261"),
           ("01:640:250", "20261")]


def _load(session, tmp_path, records, year, term, coverage):
    query = SocQuery(year=year, term=term, campus="NB")
    folder = tmp_path / f"{year}{term}"
    folder.mkdir(exist_ok=True)
    (folder / f"soc_courses_{year}_{term}_NB.json").write_text(json.dumps(records),
                                                                encoding="utf-8")
    CourseIngestionPipeline(session, folder).run(query, coverage=coverage)


def _records(name):
    return json.loads((FIXTURE_DIR / name).read_bytes())


def _terms(session, tmp_path, records, *, spring_omit=(), spring27=()):
    _load(session, tmp_path, records, 2025, "9", "complete")
    _load(session, tmp_path, [r for r in records if r["courseString"] not in spring_omit],
          2026, "1", "complete")
    _load(session, tmp_path, records, 2026, "9", "complete")
    if spring27:
        _load(session, tmp_path, [r for r in records if r["courseString"] in spring27],
              2027, "1", "partial")


@pytest.fixture
def cs(session, tmp_path):
    _terms(session, tmp_path, _records("soc_cs_courses_sample.json"),
           spring_omit={"01:198:352", "01:198:416"}, spring27={"01:198:314", "01:198:336"})
    RequirementLoader(session).load_file(CS_REQUIREMENTS)
    session.commit()
    return session


def _cs_with(session, tmp_path, edit):
    """CS world whose records were changed by `edit` (SYNTHETIC - labelled at use)."""
    records = _records("soc_cs_courses_sample.json")
    edit({r["courseString"]: r for r in records})
    _terms(session, tmp_path, records, spring_omit={"01:198:352", "01:198:416"})
    RequirementLoader(session).load_file(CS_REQUIREMENTS)
    session.commit()
    return session


@pytest.fixture
def math(session, tmp_path):
    _terms(session, tmp_path, _records("soc_math_courses_sample.json"))
    RequirementLoader(session).load_file(MATH_REQUIREMENTS)
    session.commit()
    return session


def _student(session, records, program_code="198"):
    version = next(v for v in session.scalars(select(ProgramVersion))
                   if v.program.code == program_code)
    student = Student(external_ref=f"p-{len(records)}-{program_code}",
                      catalog_year=version.catalog_year, program_version_id=version.id)
    session.add(student)
    session.flush()
    for key, term, *rest in records:
        status = rest[0] if rest else "completed"
        grade = rest[1] if len(rest) > 1 else ("A" if status == "completed" else None)
        course = session.scalar(select(Course).where(Course.course_string == key,
                                                     Course.supplement_code == ""))
        assert course is not None, key
        session.add(StudentCourse(student_id=student.id, course_id=course.id, term_code=term,
                                  status=status, grade=grade, credits_earned=course.credits))
    session.commit()
    return student


def _plan(session, student, **kw):
    kw.setdefault("start_term", START)
    return generate_plan(session, student, **kw)


def _placed(plan):
    return {pc.course: pc for t in plan.terms for pc in t.courses}


def _verify_order(session, student, plan):
    """An independent oracle: re-check every placed course with Phase 6.4's
    check_proposal, assuming passed ONLY what the plan put in strictly
    earlier terms (plus in-progress work), with its same-term co-requisite
    group proposed alongside. Every placed course must be SATISFIED."""
    from app.domain.attempts import Attempt
    from app.domain.prerequisites import PrereqStatus
    from app.services.course_eligibility import check_proposal

    in_progress = [Attempt(key, term, "completed", "A")
                   for key, term in session.execute(
                       select(Course.course_string, StudentCourse.term_code)
                       .join(Course, Course.id == StudentCourse.course_id)
                       .where(StudentCourse.student_id == student.id,
                              StudentCourse.status == "in_progress"))]
    placed = _placed(plan)
    for pc in placed.values():
        earlier = [Attempt(k, p.term_code, "completed", "A")
                   for k, p in sorted(placed.items()) if p.term_code < pc.term_code]
        check = check_proposal(session, student, [pc.course], pc.offering.rules_term,
                               set(pc.corequisite_group) - {pc.course},
                               projected=earlier + in_progress, as_of_term=pc.term_code,
                               independent=True)[pc.course]
        assert check.status is PrereqStatus.SATISFIED, (pc.course, pc.term_code, check.status)


def _invariants(plan, student_courses=()):
    """What every plan must satisfy, whatever else a test checks."""
    placed = [pc for t in plan.terms for pc in t.courses]
    keys = [pc.course for pc in placed]
    assert len(keys) == len(set(keys)), "a course planned twice"
    for pc in placed:
        assert pc.reasons, pc.course                                 # reasons for every course
        assert pc.offering.kind in PLACEABLE                         # never without evidence
        assert pc.prerequisite.state in (EligibilityState.SATISFIED_BY_HISTORY,
                                         EligibilityState.CONDITIONAL_ON_PLAN)
        assert len(pc.requirements) <= 1                             # exclusive sharing
        assert pc.requirements or {Reason.REQUIRED_PREREQUISITE_FOR,  # earns its place
                                   Reason.REQUIRED_COREQUISITE_FOR} & set(pc.reasons), pc.course
        for dep in pc.prerequisite.conditional_on:                   # dependencies earlier
            if dep.source == "planned":
                assert dep.term_code < pc.term_code
    for t in plan.terms:
        assert t.credits <= plan.metadata.constraints.max_credits_per_term
        assert len(t.courses) <= plan.metadata.constraints.max_courses_per_term
    for key in student_courses:
        assert key not in keys or any(
            r is Reason.REQUIRED_PREREQUISITE_FOR for r in _placed(plan)[key].reasons)


# ==========================================================================
# A. the CS development student (the real record shape)
# ==========================================================================


def test_a_cs_student_plan(cs) -> None:
    student = _student(cs, [*CS_DONE, ("01:198:344", "20269", "in_progress")])
    plan = _plan(cs, student)
    _invariants(plan, [k for k, _ in CS_DONE])
    _verify_order(cs, student, plan)
    placed = _placed(plan)
    assert plan.status is PlanStatus.COVERS_ALL_REQUIREMENTS
    electives = [pc for pc in placed.values()
                 if [r.requirement_code for r in pc.requirements] == ["CS_ELECTIVES"]]
    assert len(electives) == 5 - 2                     # 314 and 336 already count
    assert "01:198:344" not in placed                  # in progress: never planned again
    assert all(r.provisional for pc in placed.values() for r in pc.requirements)
    codes = {i.code for i in plan.issues}
    assert "IN_PROGRESS_OUTCOME_ASSUMED_PENDING" in codes
    assert plan.metadata.planning_engine_version == "6.5.0"
    assert plan.metadata.constraints_source.endswith("not_rutgers_policy")


# ==========================================================================
# B. Math Option A (sequences, categories, minimum grades, grade quota)
# ==========================================================================


def test_b_math_option_a(math) -> None:
    student = _student(math, [("01:640:151", "20259"), ("01:640:152", "20261"),
                              ("01:640:250", "20261")], program_code="640")
    plan = _plan(math, student, constraints=PlanningConstraints(max_terms=10))
    _invariants(plan)
    _verify_order(math, student, plan)
    placed = _placed(plan)
    assert "01:640:251" in placed and "01:640:244" in placed
    assert placed["01:640:244"].term_code > placed["01:640:251"].term_code   # 244 needs 251
    after = {r.requirement_code: r.status_after for r in plan.requirements}
    assert after["MATH_UPPER"] in ("provisionally_satisfied", "partially_satisfied")
    # J. sequences: a planned half of 411-412 / 451-452 never stands alone
    # when the category depends on it (the Degree Engine decides that).
    for a, b in (("01:640:411", "01:640:412"), ("01:640:451", "01:640:452")):
        if b in placed:
            assert a in placed
            assert any(d.course == a for d in placed[b].prerequisite.conditional_on)


# ==========================================================================
# C. D. prerequisite chains; nested AND/OR
# ==========================================================================


def test_c_prerequisite_chain_is_ordered(cs) -> None:
    student = _student(cs, [("01:198:111", "20259"), ("01:640:151", "20259")])
    plan = _plan(cs, student, constraints=PlanningConstraints(max_terms=10))
    _invariants(plan)
    _verify_order(cs, student, plan)
    placed = _placed(plan)
    order = ["01:198:112", "01:198:211", "01:198:314"]
    assert all(k in placed for k in order)
    assert [placed[k].term_code for k in order] == sorted({placed[k].term_code for k in order})
    assert placed["01:198:211"].prerequisite.state is EligibilityState.CONDITIONAL_ON_PLAN
    assert placed["01:198:205"].term_code < placed["01:198:206"].term_code


def test_d_nested_and_or_takes_one_branch(cs) -> None:
    """344: (112 or 14:332:351) and (206 or ...): the 14: alternatives are not
    in CoursePilot's data, so the plan uses the 01: branch - never both."""
    student = _student(cs, [("01:198:111", "20259"), ("01:640:151", "20259"),
                            ("01:198:205", "20261")])
    plan = _plan(cs, student, constraints=PlanningConstraints(max_terms=10))
    _invariants(plan)
    _verify_order(cs, student, plan)
    placed = _placed(plan)
    assert not any(k.startswith("14:") for k in placed)
    deps = {d.course for d in placed["01:198:344"].prerequisite.conditional_on}
    assert {"01:198:112", "01:198:206"} <= deps


# ==========================================================================
# E. F. minimum grade; a future prerequisite is conditional, never satisfied
# ==========================================================================


def test_e_minimum_grade_plans_a_retake(cs) -> None:
    """A D in 112; 211 needs C or better in it (real courseNotes)."""
    student = _student(cs, [("01:198:111", "20259"), ("01:640:151", "20259"),
                            ("01:198:112", "20261", "completed", "D")])
    plan = _plan(cs, student)
    _invariants(plan)
    _verify_order(cs, student, plan)
    placed = _placed(plan)
    assert "01:198:112" in placed                                   # the retake
    assert Reason.REQUIRED_PREREQUISITE_FOR in placed["01:198:112"].reasons
    dep = next(d for d in placed["01:198:211"].prerequisite.conditional_on
               if d.course == "01:198:112")
    assert (dep.source, dep.required_grade) == ("planned", "C")


def test_f_future_prerequisite_is_conditional(cs) -> None:
    student = _student(cs, [("01:198:111", "20259"), ("01:640:151", "20259"),
                            ("01:198:112", "20269", "in_progress")])
    plan = _plan(cs, student)
    placed = _placed(plan)
    pc = placed["01:198:211"]
    assert pc.prerequisite.state is EligibilityState.CONDITIONAL_ON_PLAN
    assert Reason.PREREQUISITES_SATISFIED_BY_HISTORY not in pc.reasons
    [dep] = [d for d in pc.prerequisite.conditional_on if d.course == "01:198:112"]
    assert (dep.source, dep.required_grade) == ("in_progress", "C")


# ==========================================================================
# G. H. P. co-requisites (SYNTHETIC rules, verbatim Rutgers wording)
# ==========================================================================


def _coreq_211_on_205(by_key):
    # SYNTHETIC: every section of 211 says "CO-REQ: 01:198:205" (wording of
    # 14:332:221's "CO-REQ: 14:332:223").
    for section in by_key["01:198:211"]["sections"]:
        section["sectionNotes"] = "CO-REQ: 01:198:205"


def test_g_same_term_corequisite_is_grouped(session, tmp_path) -> None:
    world = _cs_with(session, tmp_path, _coreq_211_on_205)
    student = _student(world, [("01:198:111", "20259"), ("01:198:112", "20261"),
                               ("01:640:151", "20259")])
    plan = _plan(world, student)
    _invariants(plan)
    _verify_order(world, student, plan)
    placed = _placed(plan)
    assert placed["01:198:211"].term_code == placed["01:198:205"].term_code
    assert placed["01:198:211"].corequisite_group == ["01:198:205", "01:198:211"]
    assert Reason.REQUIRED_COREQUISITE_FOR in placed["01:198:205"].reasons


def _pre_or_coreq_112_on_111(by_key):
    # SYNTHETIC: "PREREQ OR COREQ: 01:198:111" on 112 (wording of 01:830:341).
    by_key["01:198:112"]["courseNotes"] += " PREREQ OR COREQ: 01:198:111"


def test_h_pre_or_coreq_never_places_the_course_first(session, tmp_path) -> None:
    world = _cs_with(session, tmp_path, _pre_or_coreq_112_on_111)
    student = _student(world, [("01:640:151", "20259")])
    plan = _plan(world, student, constraints=PlanningConstraints(max_terms=10))
    placed = _placed(plan)
    if "01:198:112" in placed and "01:198:111" in placed:
        assert placed["01:198:111"].term_code <= placed["01:198:112"].term_code


def _unsupported_coreq_on_336(by_key):
    # SYNTHETIC: a co-requisite note CoursePilot cannot interpret (wording of
    # 01:119:116's "COREQ: SEE DEPARTMENT").
    for section in by_key["01:198:336"]["sections"]:
        section["sectionNotes"] = "COREQ: SEE DEPARTMENT FOR LAB REQUIREMENTS"


def test_p_unsupported_corequisite_is_never_planned(session, tmp_path) -> None:
    world = _cs_with(session, tmp_path, _unsupported_coreq_on_336)
    student = _student(world, [*[c for c in CS_DONE if c[0] != "01:198:336"],
                               ("01:198:344", "20269", "in_progress")])
    plan = _plan(world, student)
    _invariants(plan)
    _verify_order(world, student, plan)
    assert "01:198:336" not in _placed(plan)


# ==========================================================================
# I. no double counting; K. retake (covered in E); L. M. N. offerings
# ==========================================================================


def test_i_every_course_counts_once(cs) -> None:
    student = _student(cs, [("01:198:111", "20259"), ("01:640:151", "20259")])
    plan = _plan(cs, student, constraints=PlanningConstraints(max_terms=10))
    _invariants(plan)
    _verify_order(cs, student, plan)
    projected = [(pc.course, r.requirement_code) for t in plan.terms for pc in t.courses
                 for r in pc.requirements]
    assert len({c for c, _ in projected}) == len(projected)


def test_l_m_confirmed_and_historical_offerings(cs) -> None:
    student = _student(cs, [*[c for c in CS_DONE if c[0] not in ("01:198:314", "01:198:336")],
                            ("01:198:344", "20269", "in_progress")])
    plan = _plan(cs, student)
    placed = _placed(plan)
    for key in ("01:198:314", "01:198:336"):
        pc = placed[key]
        assert pc.term_code == START
        assert pc.offering.kind is OfferingEvidenceKind.CONFIRMED_IN_TERM
        assert Reason.CONFIRMED_OFFERING_IN_TERM in pc.reasons
    historical = [pc for pc in placed.values()
                  if pc.offering.kind is OfferingEvidenceKind.HISTORICAL_SAME_SEASON]
    assert historical
    for pc in historical:
        assert Reason.CONFIRMED_OFFERING_IN_TERM not in pc.reasons
        assert pc.offering.rules_term in pc.offering.same_season_terms
        assert pc.offering.rules_term != pc.term_code
        assert Reason.RULES_FROM_EARLIER_TERM in pc.reasons


def test_n_offering_evidence_kinds(cs) -> None:
    index = OfferingIndex(cs)
    assert index.evidence("01:198:314", START).kind is OfferingEvidenceKind.CONFIRMED_IN_TERM
    assert index.evidence("01:198:345", START).kind is OfferingEvidenceKind.HISTORICAL_SAME_SEASON
    assert index.evidence("01:198:352", START).kind is OfferingEvidenceKind.OTHER_SEASONS_ONLY
    assert index.evidence("01:198:352", "20279").kind is OfferingEvidenceKind.HISTORICAL_SAME_SEASON
    # complete SOC data for a term that omits the course: not offered then.
    assert index.evidence("01:198:352", "20261").kind is OfferingEvidenceKind.NOT_OFFERED_IN_TERM
    assert index.evidence("01:999:999", START).kind is OfferingEvidenceKind.NO_EVIDENCE


def test_fall_only_course_is_never_placed_in_spring(cs) -> None:
    student = _student(cs, [*CS_DONE, ("01:198:344", "20269", "in_progress")])
    for pc in _placed(_plan(cs, student)).values():
        if pc.course in ("01:198:352", "01:198:416"):
            assert pc.term_code.endswith("9")


# ==========================================================================
# O. UNKNOWN prerequisite; R. dead end and horizon
# ==========================================================================


def test_o_unknown_prerequisite_needs_confirmation(cs) -> None:
    """111's real prerequisite ("Any Course EQUAL or GREATER Than: 640:112")
    is not something CoursePilot evaluates: 111 is never planned for a
    student without it, and the plan says why."""
    student = _student(cs, [("01:640:151", "20259")])
    plan = _plan(cs, student)
    _invariants(plan)
    _verify_order(cs, student, plan)
    assert "01:198:111" not in _placed(plan)
    issue = next(i for i in plan.issues if i.requirement_code == "CS_111"
                 and i.code == "ELIGIBILITY_UNKNOWN")
    assert issue.severity is Severity.NEEDS_CONFIRMATION
    assert plan.status is PlanStatus.PARTIAL


def test_r_dead_end_and_horizon(cs) -> None:
    student = _student(cs, [("01:640:151", "20259")])
    plan = _plan(cs, student)
    blockers = {i.requirement_code for i in plan.issues if i.severity is Severity.BLOCKER}
    assert {"CS_111", "CS_112", "CS_211"} <= blockers            # everything behind 111
    short = _plan(cs, _student(cs, [("01:198:111", "20259"), ("01:640:151", "20259")]),
                  constraints=PlanningConstraints(max_terms=1))
    assert len(short.terms) == 1
    assert any(i.code == "PLAN_HORIZON_REACHED" for i in short.issues)


def test_start_term_must_follow_the_record(cs) -> None:
    student = _student(cs, [("01:198:111", "20269")])
    with pytest.raises(InvalidStartTerm):
        _plan(cs, student, start_term="20269")
    with pytest.raises(InvalidStartTerm):
        _plan(cs, student, start_term="20275")


# ==========================================================================
# Q. explicit variants; S. read-only what-if; T. determinism; U. V. isolation
# ==========================================================================


def test_q_variant_must_be_explicit(math) -> None:
    student = _student(math, [("01:640:151", "20259")], program_code="640")
    with pytest.raises(ProgramChoiceRequired) as exc:
        _plan(math, student, program_key_="sas-640-ba")
    assert exc.value.variants == ["sas-640-ba-option-a"]


def _table_hashes(session):
    """A content hash of EVERY table in the schema."""
    from app.db.base import Base

    out = {}
    for table in Base.metadata.sorted_tables:
        rows = session.execute(table.select()).all()
        out[table.name] = hashlib.sha256(repr(sorted(map(repr, rows))).encode()).hexdigest()
    return out


def test_s_what_if_plan_is_read_only(session, tmp_path) -> None:
    _terms(session, tmp_path, _records("soc_math_courses_sample.json")
           + [r for r in _records("soc_cs_courses_sample.json")
              if r["courseString"] not in {x["courseString"] for x in
                                           _records("soc_math_courses_sample.json")}])
    RequirementLoader(session).load_file(CS_REQUIREMENTS)
    RequirementLoader(session).load_file(MATH_REQUIREMENTS)
    session.commit()
    student = _student(session, CS_DONE)
    before = _table_hashes(session)
    plan = _plan(session, student, program_key_="sas-640-ba-option-a")
    assert plan.target.is_current_program is False
    assert {a.code for a in plan.assumptions} >= {"plan_is_a_projection", "hypothetical"}
    assert not (session.new or session.dirty or session.deleted)
    session.commit()
    assert _table_hashes(session) == before


def test_t_determinism(cs) -> None:
    student = _student(cs, [("01:198:111", "20259"), ("01:640:151", "20259")])
    first = _plan(cs, student).canonical_json()
    cs.expire_all()
    assert _plan(cs, student).canonical_json() == first


def test_u_catalog_year_isolation(cs) -> None:
    older = json.loads(CS_REQUIREMENTS.read_bytes())
    older["program_version"]["catalog_year"] = "2025-2026"
    for r in older["requirements"]:
        if r["code"] == "CS_ELECTIVES":
            r["min_count"] = 3
    RequirementLoader(cs).load(older, json.dumps(older).encode())
    cs.commit()
    student = _student(cs, [*CS_DONE, ("01:198:344", "20269", "in_progress")])
    current = _plan(cs, student)
    old = _plan(cs, student, program_key_="sas-198-ba", catalog_year="2025-2026")

    def count(plan):
        return sum(1 for pc in _placed(plan).values()
                   if [r.requirement_code for r in pc.requirements] == ["CS_ELECTIVES"])

    assert (count(current), count(old)) == (3, 1)
    assert old.target.catalog_year == "2025-2026"
    assert old.metadata.rules_fingerprint != current.metadata.rules_fingerprint


def test_v_program_version_isolation(session, tmp_path) -> None:
    _terms(session, tmp_path, _records("soc_cs_courses_sample.json"))
    RequirementLoader(session).load_file(CS_REQUIREMENTS)
    RequirementLoader(session).load_file(MATH_REQUIREMENTS)
    session.commit()
    student = _student(session, CS_DONE)
    math_plan = _plan(session, student, program_key_="sas-640-ba-option-a")
    codes = {r.requirement_code for r in math_plan.requirements}
    assert codes and not any(c.startswith("CS_") for c in codes)
    for pc in _placed(math_plan).values():
        assert all(not r.requirement_code.startswith("CS_") for r in pc.requirements)


def test_planned_courses_are_never_written(cs) -> None:
    student = _student(cs, [("01:198:111", "20259"), ("01:640:151", "20259")])
    before = cs.scalar(select(text("count(*)")).select_from(StudentCourse))
    _plan(cs, student)
    cs.commit()
    assert cs.scalar(select(text("count(*)")).select_from(StudentCourse)) == before
    # and the real audit is unchanged by the plan having been computed
    assert DegreeAuditEngine(cs).audit(student).model_dump_json() == \
        DegreeAuditEngine(cs).audit(student).model_dump_json()



def test_a_batch_of_alternatives_is_not_a_same_term_proposal(session, tmp_path) -> None:
    """The Phase 6.5 finding: evaluating candidates in one batch must not let
    205 - merely ANOTHER CANDIDATE - satisfy 211's co-requisite."""
    from app.domain.prerequisites import PrereqStatus
    from app.services.course_eligibility import check_proposal

    world = _cs_with(session, tmp_path, _coreq_211_on_205)
    student = _student(world, [("01:198:111", "20259"), ("01:198:112", "20261")])
    keys = ["01:198:205", "01:198:211"]
    together = check_proposal(world, student, keys, "20261")
    alone = check_proposal(world, student, keys, "20261", independent=True)
    paired = check_proposal(world, student, ["01:198:211"], "20261", {"01:198:205"},
                            independent=True)
    assert together["01:198:211"].status is PrereqStatus.SATISFIED      # one schedule
    assert alone["01:198:211"].status is PrereqStatus.UNSATISFIED       # alternatives
    assert paired["01:198:211"].status is PrereqStatus.SATISFIED
