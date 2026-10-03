"""Degree-completion grade semantics through the REAL engine (Phase 6.4).

Real curated definitions (Mathematics Option A, Computer Science, Economics)
and real archived SOC course records. The questions:

  * does a completed course COUNT toward a requirement with a minimum grade?
  * is a grade quota ("all but one ... C or better") respected - by allocation,
    with each retaken course counted once?
  * is a sequence ("411-412") both courses, not either?
  * is a GPA rule computed only where Rutgers defines its scope?
  * is a program with options never silently resolved to one option?
"""

from __future__ import annotations

import ast
import json
import pathlib

import pytest
from app.models import Course, ProgramVersion, Student, StudentCourse
from app.services.audit.engine import DegreeAuditEngine
from sqlalchemy import select

from coursepilot_ingestion.loaders.requirements import RequirementLoader
from coursepilot_ingestion.pipelines.courses import CourseIngestionPipeline
from coursepilot_ingestion.sources.soc import SocQuery

from .conftest import CS_REQUIREMENTS, CURATED_DIR, FIXTURE_DIR, MATH_REQUIREMENTS

ROOT = pathlib.Path(__file__).resolve().parents[2]
FALL_26 = SocQuery(year=2026, term="9", campus="NB")
UPPER = ["01:640:300", "01:640:321", "01:640:325", "01:640:336", "01:640:338", "01:640:348",
         "01:640:354", "01:640:356", "01:640:357", "01:640:361"]


def _soc(session, tmp_path, fixture):
    (tmp_path / f"soc_courses_{FALL_26.year}_{FALL_26.term}_{FALL_26.campus}.json").write_bytes(
        (FIXTURE_DIR / fixture).read_bytes())
    CourseIngestionPipeline(session, tmp_path).run(FALL_26)


@pytest.fixture
def math_world(session, tmp_path):
    _soc(session, tmp_path, "soc_math_courses_sample.json")
    RequirementLoader(session).load_file(MATH_REQUIREMENTS)
    session.commit()
    return session


@pytest.fixture
def cs_world(session, tmp_path):
    _soc(session, tmp_path, "soc_cs_courses_sample.json")
    RequirementLoader(session).load_file(CS_REQUIREMENTS)
    session.commit()
    return session


def _student(session, records, program_code="640"):
    version = next(v for v in session.scalars(select(ProgramVersion))
                   if v.program.code == program_code)
    student = Student(external_ref=f"g-{len(records)}", catalog_year=version.catalog_year,
                      program_version_id=version.id)
    session.add(student)
    session.flush()
    for course_string, term, status, grade, *origin in records:
        course = session.scalar(select(Course).where(Course.course_string == course_string,
                                                     Course.supplement_code == ""))
        assert course is not None, course_string
        session.add(StudentCourse(student_id=student.id, course_id=course.id, term_code=term,
                                  status=status, grade=grade, credits_earned=course.credits,
                                  credit_origin=origin[0] if origin else "rutgers"))
    session.commit()
    return student


def _audit(session, student):
    return DegreeAuditEngine(session).audit(student)


def _node(result, code):
    def walk(nodes):
        for n in nodes:
            if n.requirement_code == code:
                return n
            hit = walk(n.children)
            if hit:
                return hit
    return walk(result.requirements)


def _evidence(result, code, kind="minimum_grade"):
    return [g for g in result.grade_evaluations if g.requirement_code == code and g.kind == kind]


# ==========================================================================
# degree minimum grades
# ==========================================================================


@pytest.mark.parametrize(("grade", "status", "result"), [
    ("A", "satisfied", "satisfied"),
    ("C", "satisfied", "satisfied"),            # exactly the threshold
    ("D", "unsatisfied", "unsatisfied"),        # passed, but below "C or better"
    ("P", "satisfied", "satisfied"),            # Pass = A..C (SAS Grades and Records)
    ("TB", "provisionally_satisfied", "unknown"),  # temporary grade: not final
])
def test_minimum_grade_participates_in_allocation(math_world, grade, status, result) -> None:
    student = _student(math_world, [("01:640:250", "20259", "completed", grade)])
    audit = _audit(math_world, student)
    assert _node(audit, "MATH_250").status.value == status
    [ev] = _evidence(audit, "MATH_250")
    assert (ev.required_grade, ev.earned_grade, ev.result) == ("C", grade, result)
    assert ev.course.course_string == "01:640:250" and ev.term_code == "20259"
    if status == "satisfied":
        [alloc] = [a for a in audit.allocation if a.requirement_code == "MATH_250"]
        assert (alloc.earned_grade, alloc.required_grade) == (grade, "C")


def test_a_d_still_counts_where_no_minimum_applies(math_world) -> None:
    """MATH_151 states no minimum grade: a D there earns credit and counts."""
    student = _student(math_world, [("01:640:151", "20259", "completed", "D")])
    audit = _audit(math_world, student)
    assert _node(audit, "MATH_151").status.value == "satisfied"
    assert _evidence(audit, "MATH_151") == []


def test_transfer_credit_completes_but_cannot_prove_a_grade(math_world) -> None:
    student = _student(math_world, [("01:640:250", "20259", "completed", None, "transfer"),
                                    ("01:640:151", "20259", "completed", None, "transfer")])
    audit = _audit(math_world, student)
    assert _node(audit, "MATH_151").status.value == "satisfied"            # no minimum
    assert _node(audit, "MATH_250").status.value == "provisionally_satisfied"
    [ev] = _evidence(audit, "MATH_250")
    assert (ev.result, ev.credit_origin) == ("unknown", "transfer")


@pytest.mark.parametrize(("attempts", "status", "term"), [
    ([("D", "20259"), ("B", "20261")], "satisfied", "20261"),      # low -> qualifying
    ([("F", "20259"), ("C", "20261")], "satisfied", "20261"),      # fail -> pass
    ([("C", "20259"), ("D", "20261")], "satisfied", "20259"),      # qualifying -> later low (E credit)
    ([("D", "20259"), (None, "20269")], "provisionally_satisfied", "20269"),  # in-progress retake
])
def test_retakes_under_a_minimum_grade(math_world, attempts, status, term) -> None:
    records = [("01:640:250", t, "in_progress" if g is None else "completed", g)
               for g, t in attempts]
    audit = _audit(math_world, _student(math_world, records))
    assert _node(audit, "MATH_250").status.value == status
    [ev] = _evidence(audit, "MATH_250")
    assert ev.term_code == term
    assert len([a for a in audit.allocation if a.course.course_string == "01:640:250"]) <= 1


# ==========================================================================
# grade quota: "All but one of these courses ... C or better"
# ==========================================================================


def _upper(math_world, grades):
    records = [(c, "20259", "completed", g) for c, g in zip(UPPER, grades, strict=False)]
    records += [("01:640:311", "20259", "completed", "B"), ("01:640:350", "20259", "completed", "B")]
    return _audit(math_world, _student(math_world, records))


def test_quota_allows_exactly_one_d(math_world) -> None:
    audit = _upper(math_world, ["D", "B", "B", "B", "B", "B"])
    node = _node(audit, "MATH_UPPER")
    assert node.status.value == "satisfied"
    counted = [g for g in _evidence(audit, "MATH_UPPER", "grade_quota") if g.earned_grade == "D"]
    assert len(counted) == 1 and counted[0].result == "satisfied"


def test_quota_violation_is_reported_with_evidence(math_world) -> None:
    audit = _upper(math_world, ["D", "D", "B", "B", "B", "B"])
    node = _node(audit, "MATH_UPPER")
    assert node.status.value == "partially_satisfied"
    assert "at most 1 course(s) with a grade of D or lower" in node.reason
    assert sum(g.result == "unsatisfied" for g in _evidence(audit, "MATH_UPPER", "grade_quota")) == 2


def test_quota_repair_prefers_an_unused_qualifying_course(math_world) -> None:
    """Nine eligible courses, two D's: allocation swaps a D for the spare B."""
    audit = _upper(math_world, ["D", "D", "B", "B", "B", "B", "B"])
    node = _node(audit, "MATH_UPPER")
    held = [g for g in _evidence(audit, "MATH_UPPER", "grade_quota")]
    assert node.status.value == "satisfied"
    assert sum(g.earned_grade == "D" for g in held) == 1
    assert len({g.course.course_string for g in held}) == 8


def test_a_retaken_course_counts_once_toward_a_quota(math_world) -> None:
    records = [(c, "20259", "completed", "B") for c in UPPER[:6]]
    records += [("01:640:311", "20259", "completed", "D"), ("01:640:311", "20261", "completed", "D"),
                ("01:640:350", "20259", "completed", "B")]
    audit = _audit(math_world, _student(math_world, records))
    quota = [g for g in _evidence(audit, "MATH_UPPER", "grade_quota") if g.earned_grade == "D"]
    assert len(quota) == 1                                  # one course, one entry
    assert _node(audit, "MATH_UPPER").status.value == "satisfied"


# ==========================================================================
# sequences: "one of 01:640:311,312, 411-412"
# ==========================================================================


def _categories(math_world, analysis):
    records = [(c, "20259", "completed", "B") for c in UPPER[:6]]
    records += [(c, "20259", "completed", "B") for c in analysis]
    records += [("01:640:350", "20259", "completed", "B")]
    return _node(_audit(math_world, _student(math_world, records)), "MATH_UPPER")


def test_one_course_of_a_sequence_does_not_cover_its_category(math_world) -> None:
    node = _categories(math_world, ["01:640:411"])
    assert node.distinct_categories == 1
    assert node.status.value == "partially_satisfied"


def test_the_complete_sequence_covers_its_category(math_world) -> None:
    node = _categories(math_world, ["01:640:411", "01:640:412"])
    assert node.distinct_categories == 2


def test_a_single_course_member_still_covers_its_category(math_world) -> None:
    assert _categories(math_world, ["01:640:312"]).distinct_categories == 2


# ==========================================================================
# program-level grade quota scope (CS "courses required for the major")
# ==========================================================================


def _cs_rule(audit, code="CS_MAX_D"):
    return next(r for r in audit.rules if r.rule_code == code)


def test_a_d_outside_the_major_is_not_counted(cs_world) -> None:
    student = _student(cs_world, [("01:198:111", "20259", "completed", "D"),
                                  ("01:070:102", "20259", "completed", "D")], "198")
    rule = _cs_rule(_audit(cs_world, student))
    assert rule.status.value == "satisfied" and rule.observed_count == 1
    assert rule.evidence["counted"] == ["01:198:111"]


def test_two_ds_applied_to_the_major_violate(cs_world) -> None:
    student = _student(cs_world, [("01:198:111", "20259", "completed", "D"),
                                  ("01:198:112", "20259", "completed", "D")], "198")
    assert _cs_rule(_audit(cs_world, student)).status.value == "unsatisfied"


def test_a_retaken_d_is_not_counted_twice(cs_world) -> None:
    student = _student(cs_world, [("01:198:111", "20259", "completed", "D"),
                                  ("01:198:111", "20261", "completed", "B"),
                                  ("01:198:112", "20259", "completed", "D")], "198")
    rule = _cs_rule(_audit(cs_world, student))
    assert rule.status.value == "satisfied" and rule.evidence["counted"] == ["01:198:112"]


# ==========================================================================
# GPA rules
# ==========================================================================


def test_gpa_in_the_major_is_never_computed(session, tmp_path) -> None:
    RequirementLoader(session).load_file(CURATED_DIR / "sas-220-major.json")
    session.commit()
    student = _student(session, [], "220")
    rule = _cs_rule(_audit(session, student), "ECON_MAJOR_GPA")
    assert rule.status.value == "not_evaluable"
    assert "'major' cannot be computed" in rule.reason


def test_cumulative_gpa_is_computed_but_not_asserted(math_world) -> None:
    definition = json.loads(MATH_REQUIREMENTS.read_bytes())
    definition["program_rules"].append({
        "code": "TEST_CUM_GPA", "name": "test", "rule_type": "min_gpa", "min_gpa": 2.0,
        "gpa_scope": "cumulative", "is_evaluable": True, "source_prose": "test"})
    RequirementLoader(math_world).load(definition, json.dumps(definition).encode())
    math_world.commit()
    student = _student(math_world, [("01:640:250", "20259", "completed", "B"),
                                    ("01:640:251", "20259", "completed", "C")])
    rule = _cs_rule(_audit(math_world, student), "TEST_CUM_GPA")
    assert rule.status.value == "not_evaluable"
    # Credit-weighted: 01:640:250 (B, 3 credits) and 01:640:251 (C, 4 credits)
    # -> (9 + 8) / 7 = 2.429. The formula is SAS "Grades and Records".
    assert rule.evidence["computed_gpa"] == "2.429"
    assert "not known to be complete" in rule.reason


# ==========================================================================
# options / variants
# ==========================================================================


def test_options_are_never_silently_chosen(math_world) -> None:
    from app.services.programs import ProgramVariantRequired, find_program

    other = json.loads(MATH_REQUIREMENTS.read_bytes())
    other["program"]["variant"] = "option-b"
    other["requirements"] = [r for r in other["requirements"] if r["code"] != "MATH_UPPER"]
    RequirementLoader(math_world).load(other, json.dumps(other).encode())
    math_world.commit()

    with pytest.raises(ProgramVariantRequired) as exc:
        find_program(math_world, "sas-640-ba")
    assert exc.value.variants == ["sas-640-ba-option-a", "sas-640-ba-option-b"]
    a, _ = find_program(math_world, "sas-640-ba-option-a")
    b, _ = find_program(math_world, "sas-640-ba-option-b")
    assert a.id != b.id
    codes = {v.program.variant: {r.code for r in v.requirements} for v in
             math_world.scalars(select(ProgramVersion))}
    assert "MATH_UPPER" in codes["option-a"] and "MATH_UPPER" not in codes["option-b"]


def test_options_are_catalog_year_scoped(math_world) -> None:
    older = json.loads(MATH_REQUIREMENTS.read_bytes())
    older["program_version"]["catalog_year"] = "2025-2026"
    older["requirements"][-1]["min_count"] = 7
    RequirementLoader(math_world).load(older, json.dumps(older).encode())
    math_world.commit()
    versions = {v.catalog_year: v for v in math_world.scalars(select(ProgramVersion))}
    assert set(versions) == {"2025-2026", "2026-2027"}
    assert versions["2025-2026"].program_id == versions["2026-2027"].program_id
    upper = {y: next(r for r in v.requirements if r.code == "MATH_UPPER").min_count
             for y, v in versions.items()}
    assert upper == {"2025-2026": 7, "2026-2027": 8}


# ==========================================================================
# loader round trip and genericity
# ==========================================================================


def test_grade_fields_load_idempotently(math_world) -> None:
    from app.models import Requirement

    def snapshot():
        return sorted((r.code, r.min_grade, r.grade_quota_max_count, r.grade_quota_at_most,
                       json.dumps(r.category_sequences, sort_keys=True))
                      for r in math_world.scalars(select(Requirement)))
    before = snapshot()
    RequirementLoader(math_world).load_file(MATH_REQUIREMENTS)
    math_world.commit()
    assert snapshot() == before
    upper = next(r for r in before if r[0] == "MATH_UPPER")
    assert upper[2:4] == (1, "D") and "01:640:411" in upper[4]


def test_invalid_grade_fields_are_refused(session) -> None:
    bad = json.loads(MATH_REQUIREMENTS.read_bytes())
    bad["requirements"][1]["min_grade"] = "C-"
    with pytest.raises(ValueError):
        RequirementLoader(session).load(bad, json.dumps(bad).encode())


def test_no_program_specific_code_in_the_rule_engines() -> None:
    """Grade, attempt, GPA, condition and engine modules name no program,
    subject or course: every rule arrives as data."""
    curated_codes = {json.loads(f.read_bytes())["program"]["code"]
                     for f in CURATED_DIR.glob("sas-*.json") if "program" in json.loads(f.read_bytes())}
    modules = ["app/domain/grades.py", "app/domain/attempts.py", "app/domain/gpa.py",
               "app/domain/conditions.py", "app/domain/corequisites.py",
               "app/services/audit/engine.py", "app/services/audit/rules.py",
               "app/services/course_eligibility.py", "app/services/prerequisites.py"]
    offenders = []
    for rel in modules:
        tree = ast.parse((ROOT / "backend" / rel).read_text(encoding="utf-8"))
        docstrings = {id(n.body[0].value) for n in ast.walk(tree)
                      if isinstance(n, ast.Module | ast.FunctionDef | ast.ClassDef)
                      and n.body and isinstance(n.body[0], ast.Expr)
                      and isinstance(n.body[0].value, ast.Constant)}
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                    and id(node) not in docstrings:
                if node.value in curated_codes or ":" in node.value and node.value.count(":") == 2 \
                        and all(p.isdigit() for p in node.value.split(":")):
                    offenders.append(f"{rel}: {node.value!r}")
    assert offenders == []


def test_degree_and_eligibility_domains_stay_separate() -> None:
    """The Degree Engine never imports the prerequisite/co-requisite modules,
    and course eligibility never imports the Degree Engine."""
    engine = (ROOT / "backend/app/services/audit/engine.py").read_text(encoding="utf-8")
    eligibility = (ROOT / "backend/app/services/course_eligibility.py").read_text(encoding="utf-8")
    assert "prerequisites" not in engine and "corequisites" not in engine
    assert "audit" not in eligibility.split('"""', 2)[2]
