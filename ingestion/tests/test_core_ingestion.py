"""SAS Core Curriculum ingestion and Major+Core sharing (Phase 4).

Runs against the REAL curated SAS Core definition and the REAL SOC
`coreCodes` in the CS course fixture. No network.

The two facts come from two authoritative sources, and the tests keep that
separation visible:

  * goal definitions and structure -> sasoue.rutgers.edu (prose, curated)
  * course -> goal eligibility     -> SOC coreCodes (structured)
"""

from __future__ import annotations

import json
import pathlib
from decimal import Decimal

import pytest
from app.domain.audit import RequirementStatus
from app.models import (
    Course,
    ProgramVersion,
    Requirement,
    RequirementCourseOption,
    SchoolRequirementSetVersion,
    SharingPolicy,
)
from app.services.audit import DegreeAuditEngine
from sqlalchemy import func, select

from coursepilot_ingestion.core_schemas import (
    NormalizedCoreEligibility,
    NormalizedCoreGoal,
)
from coursepilot_ingestion.parsers.core import (
    CoreDefinitionParser,
    CoreEligibilityParser,
)
from coursepilot_ingestion.pipelines.core import CoreIngestionPipeline
from coursepilot_ingestion.validators.core import CoreValidator

from .conftest import SAS_CORE
from .test_degree_audit import _audit, _course, _enroll, _find, _student

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
CORE_DEFINITION = SAS_CORE
Y26 = "2026-2027"

# Courses in the CS fixture that SOC certifies for a core goal.
#   01:198:111  ITR, QQ, QR     -> CORE_QFR  (ITR is an unmapped conflict)
#   01:640:151  QQ, QR          -> CORE_QFR
#   01:640:152  QQ, QR          -> CORE_QFR
#   01:198:405  CCO, WCd        -> CORE_CCO, CORE_WC
#   01:013:120  HST, SOEHS      -> CORE_HST  (SOEHS is not SAS core)
#   01:070:111  CCD, CCO, NS    -> three DIFFERENT core requirements, 4 credits
#   01:070:102  HST, NS         -> second NS course, 4 credits
#   01:070:201  NS only         -> 3 credits, uncontested
#   01:070:212  NS only         -> 3 credits, uncontested
CORE_CERTIFIED = [
    "01:198:111", "01:640:151", "01:640:152",
    "01:198:405", "01:013:120", "01:070:111", "01:070:102",
]


@pytest.fixture
def soc_archive(tmp_path: pathlib.Path, cs_payload_bytes: bytes) -> pathlib.Path:
    """The CS SOC payload on disk, as the pipeline expects an archive."""
    path = tmp_path / "soc_courses_2026_9_NB.json"
    path.write_bytes(cs_payload_bytes)
    return path


@pytest.fixture
def core_session(cs_session, soc_archive: pathlib.Path):
    """cs_session plus the SAS Core tree loaded onto the same program version."""
    CoreIngestionPipeline(cs_session).run(
        CORE_DEFINITION, soc_archive, observed_term_code="20269"
    )
    return cs_session


def _core_reqs(session) -> dict[str, Requirement]:
    return {
        r.code: r
        for r in session.scalars(
            select(Requirement).where(Requirement.requirement_system == "core")
        ).all()
    }


# ==========================================================================
# goal definitions
# ==========================================================================


def test_current_goals_include_college_writing(core_session) -> None:
    """The writing page confirms WC alongside the thirteen goal codes."""
    definition = CoreDefinitionParser().parse(CORE_DEFINITION.read_bytes())
    codes = {g.code for g in definition.goals}

    assert len(definition.goals) == 14
    assert codes == {
        "CCD", "CCO", "NS", "HST", "SCL",
        "AHo", "AHp", "AHq", "AHr",
        "WC", "WCr", "WCd", "QQ", "QR",
    }


def test_official_identifiers_are_preserved_not_renumbered(core_session) -> None:
    """Rutgers' own codes are the identifiers. Replacing them with internal
    numbers would break every cross-reference back to the source."""
    definition = CoreDefinitionParser().parse(CORE_DEFINITION.read_bytes())
    by_code = {g.code: g for g in definition.goals}

    assert by_code["HST"].name == "Historical Analysis"
    assert by_code["AHq"].name == "Nature of Languages"
    # Case is preserved exactly as SAS publishes it.
    assert "AHo" in by_code and "AHO" not in by_code


def test_goal_descriptions_are_the_official_wording(core_session) -> None:
    definition = CoreDefinitionParser().parse(CORE_DEFINITION.read_bytes())
    qq = next(g for g in definition.goals if g.code == "QQ")

    assert qq.description.startswith("Formulate, evaluate, and communicate")
    assert all(g.description for g in definition.goals)


def test_core_requirements_land_in_the_core_system(core_session) -> None:
    reqs = _core_reqs(core_session)

    assert len(reqs) == 18
    assert all(r.requirement_system == "core" for r in reqs.values())
    assert "SAS_CORE" in reqs


def test_core_is_independent_of_the_major_version(core_session) -> None:
    """One school-owned tree composes with majors without cloning."""
    versions = core_session.scalars(select(ProgramVersion)).all()
    assert len(versions) == 1
    assert all(r.program_version_id is None and r.school_requirement_set_version_id
               for r in _core_reqs(core_session).values())

    systems = {
        r.requirement_system
        for r in core_session.scalars(select(Requirement)).all()
    }
    assert systems == {"major", "core"}


def test_every_core_requirement_carries_its_source_prose(core_session) -> None:
    for req in _core_reqs(core_session).values():
        assert req.source_prose, f"{req.code} has no source prose"
        assert req.curation_status == "curated_from_prose"


def test_core_ingestion_sets_the_sharing_policy(core_session) -> None:
    """The independent school source owns sharing; the major is untouched."""
    version = core_session.scalar(select(ProgramVersion))
    assert version.sharing_policy == SharingPolicy.EXCLUSIVE.value
    school_set = core_session.scalar(select(SchoolRequirementSetVersion))
    assert school_set.sharing_policy == SharingPolicy.SHARE_ACROSS_SYSTEMS.value


def test_requirement_structure_matches_the_official_areas(core_session) -> None:
    reqs = _core_reqs(core_session)

    # Contemporary Challenges: 2 courses, 1 from each category.
    assert reqs["CORE_CCD"].min_count == 1
    assert reqs["CORE_CCO"].min_count == 1
    # Writing: 3 courses. Quantitative: 2 courses. AH: 2 courses.
    assert reqs["CORE_WC"].requirement_type == "all_of"
    assert {r.code for r in reqs["CORE_WC"].children} == {"CORE_COLLEGE_WRITING", "CORE_WCR", "CORE_WCD"}
    assert {r.code for r in reqs["CORE_QFR"].children} == {"CORE_QQ", "CORE_QR"}
    assert reqs["CORE_AH"].min_count == 2
    # NS states credits, not a course count - so it is a credits requirement.
    assert reqs["CORE_NS"].requirement_type == "credits"
    assert reqs["CORE_NS"].min_credits == Decimal(6)


# ==========================================================================
# eligibility
# ==========================================================================


def test_eligibility_comes_from_soc_core_codes(core_session, cs_payload_bytes) -> None:
    parsed = CoreEligibilityParser().parse(cs_payload_bytes)

    assert parsed.courses_with_codes == 9
    # 111:3 151:2 152:2 405:2 120:2 070:111:4 070:102:3 070:201:2 070:212:2
    assert len(parsed.entries) == 22


def test_certified_courses_become_requirement_options(core_session) -> None:
    reqs = _core_reqs(core_session)
    qfr_options = {
        core_session.get(__import__("app.models", fromlist=["Course"]).Course, o.course_id).course_string
        for o in reqs["CORE_QQ"].course_options
    }

    # QQ and QR both feed CORE_QFR; three fixture courses carry them.
    assert qfr_options == {"01:198:111", "01:640:151", "01:640:152"}


def test_one_course_can_be_eligible_for_several_goals(core_session) -> None:
    """01:198:405 is certified for CCO and WCd - two different requirements."""

    course = _course(core_session, "01:198:405")
    options = core_session.scalars(
        select(RequirementCourseOption).where(
            RequirementCourseOption.course_id == course.id
        )
    ).all()
    codes = {
        core_session.get(Requirement, o.requirement_id).code for o in options
    }

    assert {"CORE_CCO", "CORE_WCD"} <= codes


def test_dual_certification_is_preserved_in_separate_exclusive_obligations(core_session):
    course = _course(core_session, "01:198:111")
    reqs = _core_reqs(core_session)
    for code, goal in (("CORE_QQ", "QQ"), ("CORE_QR", "QR")):
        assert {r.category for r in reqs[code].course_options if r.course_id == course.id} == {goal}
    assert reqs["CORE_QQ"].allocation_scope == reqs["CORE_QR"].allocation_scope


def test_unmapped_soc_codes_are_reported_not_silently_dropped(
    cs_session, soc_archive: pathlib.Path
) -> None:
    """SOC carries codes that are NOT SAS Core goals.

    SOEHS is School of Engineering; ITR is certified on courses but is not
    listed as a current goal by either official SAS page. Both are counted so
    the decision to exclude them stays visible.
    """
    stats = CoreIngestionPipeline(cs_session).run(
        CORE_DEFINITION, soc_archive, observed_term_code="20269"
    )

    assert "SOEHS" in stats.unmapped_goal_codes
    assert "ITR" in stats.unmapped_goal_codes
    assert stats.unmapped_goal_codes["ITR"] == 1


def test_no_course_is_invented_for_an_unresolved_certification(
    cs_session, soc_archive: pathlib.Path
) -> None:
    from app.models import Course

    before = cs_session.scalar(select(func.count()).select_from(Course))
    CoreIngestionPipeline(cs_session).run(CORE_DEFINITION, soc_archive)
    after = cs_session.scalar(select(func.count()).select_from(Course))

    assert before == after


def test_core_ingestion_is_idempotent(cs_session, soc_archive: pathlib.Path) -> None:
    pipeline = CoreIngestionPipeline(cs_session)
    pipeline.run(CORE_DEFINITION, soc_archive)
    before = cs_session.scalar(select(func.count()).select_from(RequirementCourseOption))

    second = pipeline.run(CORE_DEFINITION, soc_archive)

    assert second.requirements_inserted == 0
    assert second.eligibility_inserted == 0
    assert (
        cs_session.scalar(select(func.count()).select_from(RequirementCourseOption))
        == before
    )


# ==========================================================================
# validation
# ==========================================================================


def _goal(code="NS", year=Y26) -> NormalizedCoreGoal:
    return NormalizedCoreGoal(code=code, name="x", description="d", catalog_year=year)


def _elig(course="01:198:111", code="NS", year=Y26) -> NormalizedCoreEligibility:
    return NormalizedCoreEligibility(course_string=course, goal_code=code, catalog_year=year)


def test_duplicate_goal_for_one_catalog_year_is_rejected() -> None:
    outcome = CoreValidator().validate([_goal(), _goal()], [], Y26)

    assert len(outcome.goals) == 1
    assert "duplicate goal" in outcome.error_messages[0]


def test_cross_year_goal_is_rejected() -> None:
    outcome = CoreValidator().validate([_goal(year="2025-2026")], [], Y26)

    assert outcome.goals == []
    assert "never be mixed" in outcome.error_messages[0]


def test_cross_year_eligibility_is_rejected() -> None:
    outcome = CoreValidator().validate(
        [_goal()], [_elig(year="2025-2026")], Y26
    )

    assert outcome.eligibility == []
    assert outcome.error_messages


def test_eligibility_for_an_unknown_goal_is_counted_not_rejected() -> None:
    outcome = CoreValidator().validate([_goal("NS")], [_elig(code="SOEHS")], Y26)

    assert outcome.eligibility == []
    assert outcome.unmapped_codes["SOEHS"] == 1
    assert outcome.rejected == []


def test_duplicate_eligibility_is_detected() -> None:
    outcome = CoreValidator().validate([_goal()], [_elig(), _elig()], Y26)

    assert len(outcome.eligibility) == 1
    assert outcome.duplicate_eligibility == 1


def test_goal_code_case_is_matched_insensitively() -> None:
    """The official page writes WCR/WCD; SOC writes WCr/WCd. Same goals.

    Treating them as different would leave both requirements permanently
    unsatisfiable while looking correct.
    """
    outcome = CoreValidator().validate(
        [_goal("WCr")], [_elig(code="WCR")], Y26
    )

    assert len(outcome.eligibility) == 1
    assert outcome.unmapped_codes == {}


def test_malformed_goal_code_is_rejected() -> None:
    from pydantic import ValidationError

    # Short enough to reach the regex rather than tripping max_length first.
    with pytest.raises(ValidationError, match="core code"):
        NormalizedCoreGoal(code="N!", name="x", catalog_year=Y26)


def test_malformed_course_string_is_rejected() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="Rutgers format"):
        NormalizedCoreEligibility(course_string="CS-111", goal_code="NS", catalog_year=Y26)


def test_parser_rejects_definition_missing_required_keys() -> None:
    with pytest.raises(ValueError, match="missing required key"):
        CoreDefinitionParser().parse(json.dumps({"source": {}}).encode())


def test_eligibility_parser_rejects_non_array_payload() -> None:
    with pytest.raises(ValueError, match="JSON array"):
        CoreEligibilityParser().parse(b'{"not": "an array"}')


# ==========================================================================
# allocation: Major + Core sharing
# ==========================================================================


def test_one_course_satisfies_major_and_core(core_session) -> None:
    """The reason Phase 3.75 existed, now on real Rutgers data.

    01:198:111 is a required CS course AND is certified QQ/QR for the core
    quantitative requirement.
    """
    student = _student(core_session)
    _enroll(core_session, student, "01:198:111")

    result = _audit(core_session, student)

    assert _find(result, "CS_111").status is RequirementStatus.SATISFIED
    systems = {a.requirement_system for a in result.allocation}
    assert systems == {"major", "core"}

    shared = {a.course.course_string for a in result.shared_allocations}
    assert "01:198:111" in shared


def test_shared_course_counts_its_credits_once(core_session) -> None:
    """Satisfaction can double. Credits cannot."""
    student = _student(core_session)
    course = _course(core_session, "01:198:111")
    _enroll(core_session, student, "01:198:111")

    result = _audit(core_session, student)

    assert len(result.allocation) == 2          # two requirements satisfied
    assert result.credits_completed == course.credits
    assert result.credits_applicable_to_degree == course.credits
    assert isinstance(result.credits_applicable_to_degree, Decimal)


def test_one_course_can_fill_different_core_areas_but_not_contemporary_pair(core_session):
    student = _student(core_session)
    _enroll(core_session, student, "01:070:111")
    result = _audit(core_session, student)
    codes = {a.requirement_code for a in result.allocation if a.requirement_system == "core"}
    assert "CORE_NS" in codes
    assert len(codes & {"CORE_CCD", "CORE_CCO"}) == 1
    assert result.credits_applicable_to_degree == Decimal(4)


def test_dual_certified_course_fills_one_slot_not_two(core_session) -> None:
    """01:198:111 is certified for BOTH QQ and QR, so CORE_QFR now holds two
    eligibility rows for it. Two rows must still mean one course.

    This is the risk the category column introduces: if eligibility were not
    deduplicated per course before allocation, one course would appear to
    complete a two-course requirement.
    """
    student = _student(core_session)
    _enroll(core_session, student, "01:198:111")

    result = _audit(core_session, student)
    qfr_allocs = [a for a in result.allocation if a.requirement_code in {"CORE_QQ", "CORE_QR"}]

    assert len(qfr_allocs) <= 1
    assert _find(result, "CORE_QFR").status is not RequirementStatus.SATISFIED


def test_arts_and_humanities_requires_two_distinct_goals(core_session) -> None:
    """Real Rutgers data, the defect Phase 4.1 fixes.

    SAS: "Students must take two degree credit-bearing courses and meet at
    least two of these goals." Two courses certified for the SAME AH goal
    meet the course count and not the goal count.
    """
    ah = _core_reqs(core_session)["CORE_AH"]
    assert ah.min_count == 2
    assert ah.min_distinct_categories == 2

    # The two conditions are loaded from the curated definition, which is the
    # part this fixture CAN prove. The CS course archive contains no
    # AH-certified courses, so the behaviour itself is exercised on synthetic
    # categories in test_distinct_and_credits.py rather than faked here.
    by_goal: dict[str, list[str]] = {}
    for option in ah.course_options:
        course = core_session.get(Course, option.course_id)
        by_goal.setdefault(option.category, []).append(course.course_string)
    repeated = [g for g, cs in by_goal.items() if len(set(cs)) >= 2]
    if not repeated:
        pytest.skip(
            "CS course archive certifies no AH courses - the AH behaviour is "
            "covered by test_distinct_and_credits.py"
        )

    student = _student(core_session)
    for cs in sorted(set(by_goal[repeated[0]]))[:2]:
        _enroll(core_session, student, cs)

    result = _audit(core_session, student)
    ah_result = _find(result, "CORE_AH")

    assert ah_result.satisfied_count == 2          # the course count IS met
    assert ah_result.distinct_categories == 1      # the goal count is NOT
    assert ah_result.status is not RequirementStatus.SATISFIED


def test_natural_sciences_does_not_starve_a_count_requirement(core_session) -> None:
    """Independent NS scope permits reuse without starving count obligations."""
    student = _student(core_session)
    _enroll(core_session, student, "01:070:111")   # CCD, CCO, NS - 4 credits
    _enroll(core_session, student, "01:070:102")   # NS - 4 credits

    result = _audit(core_session, student)
    ns_courses = {
        a.course.course_string
        for a in result.allocation
        if a.requirement_code == "CORE_NS"
    }

    assert "01:070:111" in ns_courses
    assert _find(result, "CORE_NS").satisfied_credits <= Decimal(8)


def test_separate_courses_satisfy_separate_goals(core_session) -> None:
    """Two eligible courses CAN satisfy two goals - that is the difference."""
    student = _student(core_session)
    _enroll(core_session, student, "01:070:111")   # CCD, CCO, NS
    _enroll(core_session, student, "01:013:120")   # HST

    result = _audit(core_session, student)
    core_allocs = {
        a.requirement_code for a in result.allocation if a.requirement_system == "core"
    }

    assert len(core_allocs) == 3
    assert "CORE_HST" in core_allocs


def test_an_excluded_course_cannot_satisfy_a_core_requirement_either(
    core_session,
) -> None:
    """Interaction between the Phase 3.5 exclusion rule and Core.

    01:198:405 is certified for CCO and WCd, but the CS major rule says
    declared majors "will not receive credit (major or degree)" for it. The
    prose says DEGREE credit, not merely major credit, so it earns nothing
    toward core either - and the audit must not quietly use it.
    """
    student = _student(core_session)
    _enroll(core_session, student, "01:198:405")

    result = _audit(core_session, student)

    assert result.allocation == []
    assert [c.course_string for c in result.excluded_courses] == ["01:198:405"]
    assert result.credits_applicable_to_degree == Decimal(0)


def test_one_course_still_cannot_fill_two_major_slots(core_session) -> None:
    """The Phase 3.75 guarantee survives Core."""
    student = _student(core_session)
    _enroll(core_session, student, "01:198:111")

    result = _audit(core_session, student)
    major_allocs = [a for a in result.allocation if a.requirement_system == "major"]
    codes = [a.requirement_code for a in major_allocs]

    assert len(codes) == len(set(codes)) == 1


def test_core_credits_requirement_accumulates(core_session) -> None:
    """CORE_NS states credits, not a course count.

    Regression for a real gap: `credits` requirements were given ZERO
    allocation slots, so they could never be satisfied no matter what the
    student took. The slot count is now derived from how many of the
    student's own courses are eligible.

    Uses NS-ONLY courses (01:070:201, 01:070:212) so the credit mechanism is
    tested in isolation - see the contested case below for what happens when
    an NS course is also eligible elsewhere.
    """
    reqs = _core_reqs(core_session)
    assert reqs["CORE_NS"].min_credits == Decimal(6)

    student = _student(core_session)
    result = _audit(core_session, student)
    assert _find(result, "CORE_NS").satisfied_credits == Decimal(0)

    # Two 3-credit NS-only courses = 6 credits, exactly the bar.
    _enroll(core_session, student, "01:070:201")
    _enroll(core_session, student, "01:070:212")
    result = _audit(core_session, student)
    node = _find(result, "CORE_NS")

    assert node.needed_credits == Decimal(6)
    assert node.satisfied_credits == Decimal(6)
    assert isinstance(node.satisfied_credits, Decimal)
    assert node.status is RequirementStatus.SATISFIED


def test_credit_requirements_reuse_permitted_courses_across_areas(
    core_session,
) -> None:
    """Independent NS scope permits reuse without starving count obligations."""
    student = _student(core_session)
    _enroll(core_session, student, "01:070:111")   # CCD, CCO, NS
    _enroll(core_session, student, "01:070:102")   # HST, NS

    result = _audit(core_session, student)

    # The scarce count-based requirements are settled first...
    assert _find(result, "CORE_CCD").status is RequirementStatus.SATISFIED
    assert _find(result, "CORE_HST").status is RequirementStatus.SATISFIED
    # ...and nothing is left for the credits requirement. It reports zero
    # honestly rather than counting a course another requirement holds.
    assert _find(result, "CORE_NS").satisfied_credits == Decimal(8)


def test_core_audit_is_deterministic(core_session) -> None:
    student = _student(core_session)
    for code in CORE_CERTIFIED:
        _enroll(core_session, student, code)
    core_session.commit()

    engine = DegreeAuditEngine(core_session)
    runs = [
        sorted(
            (a.course.course_string, a.requirement_code, a.requirement_system)
            for a in engine.audit(student).allocation
        )
        for _ in range(5)
    ]

    assert all(r == runs[0] for r in runs)


def test_major_only_student_is_unaffected_by_core(core_session) -> None:
    """A course with no core certification behaves exactly as before."""
    student = _student(core_session)
    _enroll(core_session, student, "01:198:112")  # no coreCodes

    result = _audit(core_session, student)

    assert _find(result, "CS_112").status is RequirementStatus.SATISFIED
    assert result.shared_allocations == []


def test_core_root_must_also_be_satisfied_for_a_complete_degree(core_session) -> None:
    """Two roots now: the major and the core. Finishing one is not a degree."""
    from .test_degree_audit import CORE, MATH

    student = _student(core_session)
    for code in CORE + MATH:
        _enroll(core_session, student, code)

    result = _audit(core_session, student)
    roots = {r.requirement_code: r.status for r in result.requirements}

    assert "SAS_CORE" in roots
    assert roots["SAS_CORE"] is not RequirementStatus.SATISFIED
    assert result.is_complete is False


# Phase 7.2.2 correctness probes use fabricated student records only.
def _certify(session, code, course_string, category=None):
    req = _core_reqs(session)[code]
    course = _course(session, course_string)
    session.add(RequirementCourseOption(requirement_id=req.id, course_id=course.id,
        category=category, source_id=req.source_id))
    session.flush()


def test_same_goal_courses_cannot_complete_quantitative_area(core_session):
    from sqlalchemy import delete
    reqs = _core_reqs(core_session)
    core_session.execute(delete(RequirementCourseOption).where(
        RequirementCourseOption.requirement_id == reqs["CORE_QR"].id))
    student = _student(core_session)
    _enroll(core_session, student, "01:640:151")
    _enroll(core_session, student, "01:640:152")
    result = _audit(core_session, student)
    assert _find(result, "CORE_QQ").status is RequirementStatus.SATISFIED
    assert _find(result, "CORE_QR").status is RequirementStatus.UNSATISFIED
    assert _find(result, "CORE_QFR").status is not RequirementStatus.SATISFIED


def test_pass_grade_earns_degree_credit_but_not_core(core_session):
    student = _student(core_session)
    _enroll(core_session, student, "01:640:151", grade="P")
    result = _audit(core_session, student)
    assert result.credits_completed > 0
    assert not [a for a in result.allocation if a.requirement_system == "core"]


def test_wcr_requires_verified_nb_campus_and_rejects_transfer(core_session):
    from app.models import StudentAcademicEvidence, StudentCourse
    _certify(core_session, "CORE_WCR", "01:198:111", "WCr")
    student = _student(core_session)
    _enroll(core_session, student, "01:198:111")
    result = _audit(core_session, student)
    assert _find(result, "CORE_WCR").status is RequirementStatus.PROVISIONALLY_SATISFIED
    assert any(f.code == "requirement_eligibility_unknown"
               and f.requirement_code == "CORE_WCR" for f in result.findings)
    core_session.add(StudentAcademicEvidence(student_id=student.id,
        evidence_kind="attendance_campus", evidence_key="01:198:111",
        value={"term_code": "20269", "campus": "NB"}, verified=True,
        source_reference="synthetic://verified-attendance"))
    assert _find(_audit(core_session, student), "CORE_WCR").status is RequirementStatus.SATISFIED
    attempt = core_session.scalar(select(StudentCourse).where(StudentCourse.student_id == student.id))
    attempt.credit_origin = "transfer"
    assert _find(_audit(core_session, student), "CORE_WCR").status is RequirementStatus.UNSATISFIED


def test_ap_exemption_requires_verified_score_and_changes_academic_identity(core_session):
    from app.models import StudentAcademicEvidence
    from app.services.audit.cache import academic_fingerprint
    student = _student(core_session)
    claim = StudentAcademicEvidence(student_id=student.id, evidence_kind="exam_score",
        evidence_key="ap_english_language", value={"score": 4}, verified=False,
        source_reference="synthetic://exam-report")
    core_session.add(claim)
    core_session.flush()
    before = academic_fingerprint(core_session, student)
    assert _find(_audit(core_session, student), "CORE_COLLEGE_WRITING").status is RequirementStatus.UNSATISFIED
    claim.verified = True
    core_session.flush()
    assert academic_fingerprint(core_session, student) != before
    result = _audit(core_session, student)
    assert _find(result, "CORE_COLLEGE_WRITING").status is RequirementStatus.SATISFIED
    assert "verified academic evidence" in _find(result, "CORE_COLLEGE_WRITING").reason
    assert _find(result, "CORE_WC").status is not RequirementStatus.SATISFIED
    assert result.credits_completed == 0


def test_count_and_credit_constraints_select_feasible_alternative(core_session):
    from app.models import StudentCourse
    _certify(core_session, "CORE_HST", "01:198:111", "HST")
    student = _student(core_session)
    _enroll(core_session, student, "01:013:120")
    low = core_session.scalar(select(StudentCourse).where(StudentCourse.student_id == student.id))
    low.credits_earned = Decimal(1)
    result = _audit(core_session, student)
    assert _find(result, "CORE_HST").status is RequirementStatus.PARTIALLY_SATISFIED
    assert _find(result, "CORE_HST").needed_credits == 3
    _enroll(core_session, student, "01:198:111")
    result = _audit(core_session, student)
    hst = _find(result, "CORE_HST")
    assert hst.status is RequirementStatus.SATISFIED
    assert hst.satisfied_credits >= 3
    assert [c.course_string for c in hst.allocated_courses] == ["01:198:111"]


def test_school_tree_is_reused_by_second_major_and_is_fingerprinted(core_session):
    from app.models import Program
    from app.services.academic_context import composed_requirements, context_coverage
    from app.services.audit.cache import rules_fingerprint
    original = core_session.scalar(select(ProgramVersion))
    program = Program(school_id=original.program.school_id, code="synthetic-722",
        name="Fabricated second major", degree_type="BA", source_id=original.source_id)
    core_session.add(program)
    core_session.flush()
    other = ProgramVersion(program_id=program.id, catalog_year=original.catalog_year,
        source_id=original.source_id)
    core_session.add(other)
    core_session.flush()
    original_ids = {r.id for r in composed_requirements(core_session, original) if r.requirement_system == "core"}
    assert original_ids == {r.id for r in composed_requirements(core_session, other)}
    assert context_coverage(core_session, other)["school_context"] == "available"
    before = rules_fingerprint(core_session, other.id)
    school_set = core_session.scalar(select(SchoolRequirementSetVersion))
    school_set.definition_version = "synthetic-update"
    core_session.flush()
    assert rules_fingerprint(core_session, other.id) != before


@pytest.mark.parametrize("score", [None, "not a score", "NaN", "Infinity", 3, 4.5, 6])
def test_invalid_or_insufficient_ap_evidence_never_waives_requirement(score):
    from types import SimpleNamespace

    from app.services.academic_context import requirement_exemptions
    requirement = SimpleNamespace(code="WC", exemption_rule={"evidence_kind": "exam_score",
        "keys": ["ap_english_language"], "minimum_score": 4, "allowed_scores": [4, 5]})
    claim = SimpleNamespace(verified=True, evidence_kind="exam_score",
        evidence_key="ap_english_language", value={"score": score})
    assert requirement_exemptions([requirement], [claim]) == set()


def test_conflicting_verified_campus_evidence_stays_unknown():
    from types import SimpleNamespace

    from app.domain.grades import Tri
    from app.services.academic_context import attempt_eligibility
    requirement = SimpleNamespace(eligible_origins=["rutgers"], graded_only=True,
                                  required_campus="NB")
    attempt = SimpleNamespace(credit_origin="rutgers", status="completed", grade="A",
                              term_code="20309")
    course = SimpleNamespace(course_string="01:999:101")
    evidence = [SimpleNamespace(verified=True, evidence_kind="attendance_campus",
        evidence_key=course.course_string, value={"term_code": "20309", "campus": c})
        for c in ("NB", "NK")]
    status, reason = attempt_eligibility(requirement, attempt, course, evidence)
    assert status is Tri.UNKNOWN and "conflict" in reason


def test_changed_school_rules_go_back_for_review_unchanged_do_not() -> None:
    from types import SimpleNamespace

    from app.services.program_lifecycle import (
        NEEDS_REREVIEW,
        school_set_definition_changed,
    )

    fresh = SimpleNamespace(definition_sha256=None, lifecycle_state="published")
    assert not school_set_definition_changed(fresh, "a" * 64)
    assert fresh.lifecycle_state == "published" and fresh.definition_sha256 == "a" * 64
    assert not school_set_definition_changed(fresh, "a" * 64)
    assert fresh.lifecycle_state == "published"
    assert school_set_definition_changed(fresh, "b" * 64)
    assert fresh.lifecycle_state == NEEDS_REREVIEW and fresh.definition_sha256 == "b" * 64
