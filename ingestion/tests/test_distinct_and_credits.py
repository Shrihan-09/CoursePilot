"""Distinct categories and credit allocation (Phase 4.1).

Two defects found when SAS Core met real data, both fixed generically.

## Issue A - distinct categories

Rutgers SAS, Arts and Humanities:

    "Students must take two degree credit-bearing courses and meet at least
     two of these goals."

That is a CONJUNCTION of two independent conditions - a course count AND a
goal count - and the old model could express only the first. AHo/AHp/AHq/AHr
were collapsed onto one requirement node, so two AHp courses passed.

The fix is not an AH branch. `Requirement.min_distinct_categories` is a
column, and `RequirementCourseOption.category` records WHY a course is
eligible. Any source that certifies courses under sub-categories can now say
so.

## Issue B - credits are not slots

A `credits` requirement was competing in the same bipartite matching as count
requirements, which measures the wrong thing. A 6-credit requirement with five
eligible 3-credit courses claimed all five and starved a count requirement of
its only option. Credit requirements now leave the matching entirely and are
settled afterwards, highest-credit-first, stopping at the minimum.

## Synthetic data

The requirement DEFINITIONS below are SYNTHETIC - they are shaped like SAS
Core because that is the shape the rules have, but they are not Rutgers
requirements and must not be read as such. The COURSES are real, so credits
are real.
"""

from __future__ import annotations

import json
from decimal import Decimal

from app.domain.audit import RequirementStatus
from app.models import ProgramVersion, Requirement, RequirementCourseOption, Student
from app.services.audit.allocation import allocate_credits, max_distinct_categories
from coursepilot_ingestion.loaders.requirements import RequirementLoader
from sqlalchemy import select

from tests.test_degree_audit import _audit, _course, _enroll, _find

_SOURCE = {
    "url": "synthetic://coursepilot/test/phase-4.1",
    "catalog_year": "2031-2032",
    "retrieved_at": "2026-09-20",
    "kind": "manual_curation",
    "curation_status": "synthetic",
}

# Real courses, chosen for their real credit values.
#   01:013:120  3      01:070:201  3      01:070:212  3
#   01:198:405  3      01:640:250  3      01:198:111  4
#   01:070:102  4      01:070:111  4      01:198:104  1


def _definition(requirements: list[dict]) -> dict:
    return {
        "source": _SOURCE,
        "school": {"code": "SAS", "name": "School of Arts and Sciences", "campus_code": "NB"},
        "program": {"code": "998", "name": "Synthetic Program 4.1", "degree_type": "BA"},
        "program_version": {
            "catalog_year": "2031-2032",
            "total_credits_min": 12,
            "sharing_policy": "exclusive",
            "source_prose": "SYNTHETIC TEST DATA - not a Rutgers requirement.",
        },
        "requirements": requirements,
    }


def _load(session, requirements: list[dict]) -> ProgramVersion:
    definition = _definition(requirements)
    RequirementLoader(session).load(definition, json.dumps(definition).encode())
    session.commit()
    return session.scalar(
        select(ProgramVersion).where(ProgramVersion.catalog_year == "2031-2032")
    )


def _student_on(session, version: ProgramVersion) -> Student:
    student = Student(
        external_ref="phase41-test",
        catalog_year=version.catalog_year,
        program_version_id=version.id,
    )
    session.add(student)
    session.flush()
    return student


# ==========================================================================
# Issue A - "at least two distinct goals"
# ==========================================================================

# SYN_AH is shaped like CORE_AH: two courses AND two distinct goals.
# 01:640:250 is deliberately certified for BOTH Xp and Xq - the case that
# decides whether the implementation matches or merely unions.
_AH = [
    {
        "code": "SYN_ROOT",
        "name": "Synthetic Degree",
        "requirement_type": "all_of",
        "sort_order": 0,
        "parent": None,
        "requirement_system": "core",
        "source_prose": "SYNTHETIC",
    },
    {
        "code": "SYN_AH",
        "name": "Synthetic Arts and Humanities",
        "requirement_type": "choose_n",
        "min_count": 2,
        "min_distinct_categories": 2,
        "sort_order": 0,
        "parent": "SYN_ROOT",
        "requirement_system": "core",
        "course_categories": {
            "Xo": ["01:013:120"],
            "Xp": ["01:070:201", "01:070:212", "01:640:250"],
            "Xq": ["01:198:405", "01:640:250"],
        },
        "source_prose": (
            "SYNTHETIC, modelled on: 'Students must take two degree "
            "credit-bearing courses and meet at least two of these goals.'"
        ),
    },
]


def test_eligibility_stores_one_row_per_certifying_category(cs_session) -> None:
    """The source information the old model destroyed.

    A course certified for two goals produces TWO eligibility rows, because
    'which goal certifies this' is part of the fact, not decoration.
    """
    _load(cs_session, _AH)
    req = cs_session.scalar(select(Requirement).where(Requirement.code == "SYN_AH"))
    course = _course(cs_session, "01:640:250")

    rows = cs_session.scalars(
        select(RequirementCourseOption).where(
            RequirementCourseOption.requirement_id == req.id,
            RequirementCourseOption.course_id == course.id,
        )
    ).all()

    assert {r.category for r in rows} == {"Xp", "Xq"}
    assert req.min_distinct_categories == 2


# --- Case A: two courses, ONE goal -> the bug Phase 4.1 exists to fix ------


def test_case_a_two_courses_one_goal_is_not_satisfied(cs_session) -> None:
    """Before Phase 4.1 this passed. It is the whole defect."""
    version = _load(cs_session, _AH)
    student = _student_on(cs_session, version)
    _enroll(cs_session, student, "01:070:201")  # Xp
    _enroll(cs_session, student, "01:070:212")  # Xp

    result = _audit(cs_session, student)
    ah = _find(result, "SYN_AH")

    assert ah.status is not RequirementStatus.SATISFIED
    assert ah.distinct_categories == 1
    assert ah.needed_distinct_categories == 2
    assert "goal" in ah.reason or "categor" in ah.reason


# --- Case B: two courses, two goals ---------------------------------------


def test_case_b_two_courses_two_goals_is_satisfied(cs_session) -> None:
    version = _load(cs_session, _AH)
    student = _student_on(cs_session, version)
    _enroll(cs_session, student, "01:070:201")  # Xp
    _enroll(cs_session, student, "01:013:120")  # Xo

    result = _audit(cs_session, student)
    ah = _find(result, "SYN_AH")

    assert ah.status is RequirementStatus.SATISFIED
    assert ah.distinct_categories == 2


# --- Case C: one course, one goal -----------------------------------------


def test_case_c_one_course_fails_the_count(cs_session) -> None:
    version = _load(cs_session, _AH)
    student = _student_on(cs_session, version)
    _enroll(cs_session, student, "01:070:201")  # Xp

    result = _audit(cs_session, student)
    ah = _find(result, "SYN_AH")

    assert ah.status is not RequirementStatus.SATISFIED
    assert ah.satisfied_count == 1


# --- Case D: ONE course certified for TWO goals ---------------------------


def test_case_d_one_dual_certified_course_is_one_goal(cs_session) -> None:
    """The case the source wording settles.

    01:640:250 is certified for Xp AND Xq. A union of categories would answer
    'two goals met' and - if the count were also mis-read - satisfy a
    two-course requirement with one course. The Rutgers sentence is a
    conjunction: two COURSES and two GOALS. One course occupies one goal slot.
    """
    version = _load(cs_session, _AH)
    student = _student_on(cs_session, version)
    _enroll(cs_session, student, "01:640:250")  # Xp and Xq

    result = _audit(cs_session, student)
    ah = _find(result, "SYN_AH")

    assert ah.status is not RequirementStatus.SATISFIED
    assert ah.satisfied_count == 1
    assert ah.distinct_categories == 1


# --- Case E: the matching case a greedy scan gets wrong -------------------


def test_case_e_dual_certified_course_yields_to_the_constrained_one(
    cs_session,
) -> None:
    """01:640:250 (Xp, Xq) + 01:070:201 (Xp only).

    Scanned greedily in sorted order, 01:640:250 takes Xp and 01:070:201 then
    has nowhere to go - one distinct goal, requirement unsatisfied. The right
    answer is two: 01:640:250 moves to Xq. That is an augmenting path, which
    is why this reuses the matching rather than a loop.
    """
    version = _load(cs_session, _AH)
    student = _student_on(cs_session, version)
    _enroll(cs_session, student, "01:640:250")  # Xp, Xq
    _enroll(cs_session, student, "01:070:201")  # Xp

    result = _audit(cs_session, student)
    ah = _find(result, "SYN_AH")

    assert ah.distinct_categories == 2
    assert ah.status is RequirementStatus.SATISFIED


# --- the helper, directly -------------------------------------------------


def test_max_distinct_categories_unit_cases() -> None:
    both_same = {"A": frozenset({"Xp"}), "B": frozenset({"Xp"})}
    assert max_distinct_categories(both_same)[0] == 1

    different = {"A": frozenset({"Xp"}), "B": frozenset({"Xo"})}
    assert max_distinct_categories(different)[0] == 2

    one_dual = {"A": frozenset({"Xp", "Xq"})}
    assert max_distinct_categories(one_dual)[0] == 1

    augmenting = {"A": frozenset({"Xp", "Xq"}), "B": frozenset({"Xp"})}
    count, assignment = max_distinct_categories(augmenting)
    assert count == 2
    assert assignment == {"A": "Xq", "B": "Xp"}


def test_max_distinct_categories_is_deterministic() -> None:
    courses = {
        "C": frozenset({"Xp", "Xq"}),
        "A": frozenset({"Xp"}),
        "B": frozenset({"Xq", "Xo"}),
    }
    first = max_distinct_categories(courses)
    for _ in range(5):
        assert max_distinct_categories(dict(reversed(list(courses.items())))) == first


def test_requirements_without_categories_are_unaffected(cs_session) -> None:
    """A requirement whose source certifies no sub-categories keeps working.

    Every major requirement is in this shape, so this is the guard against
    the generic mechanism leaking into requirements that never asked for it.
    """
    plain = [
        dict(_AH[0]),
        {
            "code": "SYN_PLAIN",
            "name": "Any two courses",
            "requirement_type": "choose_n",
            "min_count": 2,
            "sort_order": 0,
            "parent": "SYN_ROOT",
            "requirement_system": "core",
            "courses": ["01:070:201", "01:070:212"],
            "source_prose": "SYNTHETIC",
        },
    ]
    version = _load(cs_session, plain)
    student = _student_on(cs_session, version)
    _enroll(cs_session, student, "01:070:201")
    _enroll(cs_session, student, "01:070:212")

    result = _audit(cs_session, student)
    plain_result = _find(result, "SYN_PLAIN")

    assert plain_result.status is RequirementStatus.SATISFIED
    assert plain_result.needed_distinct_categories is None
    assert plain_result.distinct_categories == 0


# ==========================================================================
# Issue B - credits are not slots
# ==========================================================================

# SYN_NS states a credit minimum and no course count - the real shape of
# CORE_NS. SYN_HST is count-based and its ONLY option (01:070:111) is also
# eligible for SYN_NS. That overlap is the starvation case.
_CREDITS = [
    dict(_AH[0]),
    {
        "code": "SYN_NS",
        "name": "Synthetic Natural Sciences",
        "requirement_type": "credits",
        "min_credits": 6,
        "sort_order": 0,
        "parent": "SYN_ROOT",
        "requirement_system": "core",
        "courses": [
            "01:070:111",  # 4
            "01:070:102",  # 4
            "01:070:201",  # 3
            "01:070:212",  # 3
            "01:013:120",  # 3
        ],
        "source_prose": "SYNTHETIC, modelled on a 6-credit area with no course count.",
    },
    {
        "code": "SYN_HST",
        "name": "Synthetic Historical Analysis",
        "requirement_type": "choose_n",
        "min_count": 1,
        "sort_order": 1,
        "parent": "SYN_ROOT",
        "requirement_system": "core",
        # Exactly one option, and it is also SYN_NS-eligible.
        "courses": ["01:070:111"],
        "source_prose": "SYNTHETIC",
    },
]


def test_credits_requirement_gets_no_matching_slots(cs_session) -> None:
    """The mechanism, stated as a test.

    A credit requirement contributes zero slots to the bipartite matching.
    Anything else measures a credit minimum in courses.
    """
    from app.services.audit.engine import DegreeAuditEngine

    version = _load(cs_session, _CREDITS)
    req = cs_session.scalar(select(Requirement).where(Requirement.code == "SYN_NS"))
    engine = DegreeAuditEngine(cs_session)

    assert engine._slots_needed(req, {}) == 0
    assert version is not None


# --- Case F: claims only what it needs ------------------------------------


def test_case_f_credit_requirement_stops_at_the_minimum(cs_session) -> None:
    """Three 3-credit courses, 6 credits needed: two are claimed, not three."""
    version = _load(cs_session, _CREDITS)
    student = _student_on(cs_session, version)
    for cs in ("01:070:201", "01:070:212", "01:013:120"):
        _enroll(cs_session, student, cs)

    result = _audit(cs_session, student)
    ns = _find(result, "SYN_NS")

    assert ns.status is RequirementStatus.SATISFIED
    claimed = [a for a in result.allocation if a.requirement_code == "SYN_NS"]
    assert len(claimed) == 2
    assert ns.satisfied_credits == Decimal(6)


# --- Case G: the starvation regression ------------------------------------


def test_case_g_credit_requirement_does_not_starve_a_count_requirement(
    cs_session,
) -> None:
    """The defect measured in Phase 4, pinned here.

    01:070:111 is the ONLY course that can satisfy SYN_HST, and it is also
    SYN_NS-eligible. While credits competed in the matching, SYN_NS could
    claim it and leave SYN_HST unsatisfiable. Settling credits after the
    matching makes both satisfiable at once - and both must be satisfied.
    """
    version = _load(cs_session, _CREDITS)
    student = _student_on(cs_session, version)
    _enroll(cs_session, student, "01:070:111")  # 4, the contested course
    _enroll(cs_session, student, "01:070:201")  # 3
    _enroll(cs_session, student, "01:070:212")  # 3

    result = _audit(cs_session, student)

    assert _find(result, "SYN_HST").status is RequirementStatus.SATISFIED
    assert _find(result, "SYN_NS").status is RequirementStatus.SATISFIED

    # And the contested course went to the requirement that had no alternative.
    hst = [a for a in result.allocation if a.requirement_code == "SYN_HST"]
    assert [a.course.course_string for a in hst] == ["01:070:111"]
    ns_courses = {
        a.course.course_string for a in result.allocation if a.requirement_code == "SYN_NS"
    }
    assert "01:070:111" not in ns_courses


def test_case_g_no_course_is_claimed_twice_in_one_system(cs_session) -> None:
    """Sharing is across systems, never within one. The credit pass must obey
    the same invariant the matching does."""
    version = _load(cs_session, _CREDITS)
    student = _student_on(cs_session, version)
    for cs in ("01:070:111", "01:070:201", "01:070:212"):
        _enroll(cs_session, student, cs)

    result = _audit(cs_session, student)

    core = [a for a in result.allocation if a.requirement_system == "core"]
    keys = [a.course.course_string for a in core]
    assert len(keys) == len(set(keys))


# --- Case H: overshooting a MINIMUM is fine -------------------------------


def test_case_h_overshooting_the_minimum_is_allowed(cs_session) -> None:
    """Rutgers states a MINIMUM. 4 + 3 = 7 satisfies 6; nothing is truncated
    and no course is split."""
    version = _load(cs_session, _CREDITS)
    student = _student_on(cs_session, version)
    _enroll(cs_session, student, "01:070:102")  # 4
    _enroll(cs_session, student, "01:070:201")  # 3

    result = _audit(cs_session, student)
    ns = _find(result, "SYN_NS")

    assert ns.status is RequirementStatus.SATISFIED
    assert ns.satisfied_credits == Decimal(7)


# --- Case I: short of the minimum -----------------------------------------


def test_case_i_insufficient_credits_are_reported_honestly(cs_session) -> None:
    version = _load(cs_session, _CREDITS)
    student = _student_on(cs_session, version)
    _enroll(cs_session, student, "01:070:201")  # 3 of 6

    result = _audit(cs_session, student)
    ns = _find(result, "SYN_NS")

    assert ns.status is not RequirementStatus.SATISFIED
    assert ns.satisfied_credits == Decimal(3)
    assert ns.needed_credits == Decimal(6)
    assert "3" in ns.reason and "6" in ns.reason


# --- Case J: determinism --------------------------------------------------


def test_case_j_credit_allocation_is_deterministic(cs_session) -> None:
    version = _load(cs_session, _CREDITS)
    student = _student_on(cs_session, version)
    for cs in ("01:070:102", "01:070:201", "01:070:212", "01:013:120"):
        _enroll(cs_session, student, cs)

    runs = []
    for _ in range(3):
        result = _audit(cs_session, student)
        runs.append(
            sorted(
                a.course.course_string
                for a in result.allocation
                if a.requirement_code == "SYN_NS"
            )
        )
    assert runs[0] == runs[1] == runs[2]
    # Highest credits first (01:070:102, 4cr), then the lowest-numbered of the
    # tied 3-credit courses. The tie-break is the COURSE STRING, not the
    # surrogate id, so this expectation survives a re-ingest.
    assert runs[0] == ["01:013:120", "01:070:102"]


def test_allocate_credits_unit_ordering() -> None:
    chosen = allocate_credits(
        "SYN_NS",
        Decimal(6),
        [
            ("key-B", Decimal(3), "01:000:200"),
            ("key-A", Decimal(4), "01:000:300"),
            ("key-C", Decimal(3), "01:000:100"),
        ],
    )
    # 4 credits first, then the tie broken by course string - NOT by the
    # opaque key, which would have chosen key-B.
    assert chosen == ["key-A", "key-C"]


def test_allocate_credits_takes_nothing_when_nothing_is_needed() -> None:
    assert allocate_credits("SYN_NS", Decimal(0), [("A", Decimal(3), "01:000:100")]) == []


def test_allocate_credits_takes_everything_when_short() -> None:
    """Short of the minimum, the requirement still shows what it holds -
    it does not silently claim zero."""
    chosen = allocate_credits("SYN_NS", Decimal(6), [("A", Decimal(3), "01:000:100")])
    assert chosen == ["A"]


def test_allocate_credits_handles_missing_credits_without_inventing_any() -> None:
    """A course whose credits are unknown contributes nothing.

    It is never assumed to be 3, so it sorts last and is not claimed while
    courses with known credits can meet the minimum. Inventing a credit value
    would be the one thing worse than leaving the course unclaimed.
    """
    chosen = allocate_credits(
        "SYN_NS",
        Decimal(6),
        [
            ("A", None, "01:000:100"),
            ("B", Decimal(3), "01:000:200"),
            ("C", Decimal(3), "01:000:300"),
        ],
    )
    assert chosen == ["B", "C"]
