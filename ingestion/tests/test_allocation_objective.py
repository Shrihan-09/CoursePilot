"""Allocation objective counterexamples (Phase 4.2 investigation).

## What this file is

An INVESTIGATION artifact, not a feature. Every test here pins what the
allocator does TODAY. Where today's answer is worse than an available
alternative, the test says so in its assertions and names the better
allocation, so that a later phase changing the objective has an exact
inventory of what should change.

Nothing in this file asserts that current behaviour is correct. Several
assertions deliberately pin behaviour the investigation flags as wrong.

## The question

The allocator maximizes FILLED SLOTS. Requirement satisfaction is a
THRESHOLD over filled slots: a `choose_n` node needing 2 reports SATISFIED at
2 and contributes nothing at 1. Maximizing a sum is not the same as
maximizing how many thresholds are crossed, so the two objectives can differ.

## Synthetic data

Requirement DEFINITIONS are SYNTHETIC and are not Rutgers requirements. The
COURSES are real, so credits are real.
"""

from __future__ import annotations

import json
from decimal import Decimal

from app.domain.audit import RequirementStatus
from app.models import ProgramVersion, Student
from coursepilot_ingestion.loaders.requirements import RequirementLoader
from sqlalchemy import select

from tests.test_degree_audit import _audit, _enroll, _find

_SOURCE = {
    "url": "synthetic://coursepilot/test/phase-4.2",
    "catalog_year": "2032-2033",
    "retrieved_at": "2026-09-20",
    "kind": "manual_curation",
    "curation_status": "synthetic",
}


def _root(system: str = "major") -> dict:
    return {
        "code": "ROOT",
        "name": "Synthetic Degree",
        "requirement_type": "all_of",
        "sort_order": 0,
        "parent": None,
        "requirement_system": system,
        "source_prose": "SYNTHETIC",
    }


def _load(session, requirements: list[dict], policy: str = "exclusive") -> ProgramVersion:
    definition = {
        "source": _SOURCE,
        "school": {"code": "SAS", "name": "School of Arts and Sciences", "campus_code": "NB"},
        "program": {"code": "997", "name": "Synthetic Program 4.2", "degree_type": "BA"},
        "program_version": {
            "catalog_year": "2032-2033",
            "total_credits_min": 12,
            "sharing_policy": policy,
            "source_prose": "SYNTHETIC TEST DATA - not a Rutgers requirement.",
        },
        "requirements": requirements,
    }
    RequirementLoader(session).load(definition, json.dumps(definition).encode())
    session.commit()
    return session.scalar(
        select(ProgramVersion).where(ProgramVersion.catalog_year == "2032-2033")
    )


def _student_on(session, version: ProgramVersion, ref: str) -> Student:
    student = Student(
        external_ref=ref,
        catalog_year=version.catalog_year,
        program_version_id=version.id,
    )
    session.add(student)
    session.flush()
    return student


def _run(session, requirements, courses, *, policy="exclusive", ref="obj"):
    version = _load(session, requirements, policy)
    student = _student_on(session, version, ref)
    for course_string in courses:
        _enroll(session, student, course_string)
    return _audit(session, student)


def _satisfied(result, codes: list[str]) -> set[str]:
    """Which of these requirement codes actually reached SATISFIED."""
    return {
        code
        for code in codes
        if _find(result, code).status
        in (RequirementStatus.SATISFIED, RequirementStatus.PROVISIONALLY_SATISFIED)
    }


def _filled_slots(result, codes: list[str]) -> int:
    return len([a for a in result.allocation if a.requirement_code in codes])


# ==========================================================================
# Case A - one course, two possible slots, only ever one requirement
# ==========================================================================


def test_case_a_one_course_two_eligible_slots_one_satisfied(cs_session) -> None:
    """The baseline, and the reason eligibility is not satisfaction.

    01:198:111 is eligible for P and Q. Both objectives agree: one course
    fills one slot and satisfies exactly one requirement. There is nothing to
    optimize - the only question is WHICH, and that is a tie-break.

    possible allocations   A->P  |  A->Q
    filled slots           1     |  1
    requirements satisfied 1     |  1
    credits                4     |  4
    preferred by           neither objective - they tie
    """
    reqs = [
        _root(),
        {"code": "P", "name": "P", "requirement_type": "course", "sort_order": 0,
         "parent": "ROOT", "requirement_system": "major", "courses": ["01:198:111"]},
        {"code": "Q", "name": "Q", "requirement_type": "course", "sort_order": 1,
         "parent": "ROOT", "requirement_system": "major", "courses": ["01:198:111"]},
    ]
    result = _run(cs_session, reqs, ["01:198:111"], ref="case-a")

    assert _filled_slots(result, ["P", "Q"]) == 1
    assert len(_satisfied(result, ["P", "Q"])) == 1
    # Credits are counted once regardless of which requirement won.
    assert result.credits_applicable_to_degree == Decimal(4)


# ==========================================================================
# Case B - two courses, two requirements, augmenting path required
# ==========================================================================


def test_case_b_matching_beats_greedy_and_both_objectives_agree(cs_session) -> None:
    """Why a matching was chosen over a greedy scan in the first place.

    01:198:111 is eligible for P and Q; 01:198:112 only for Q. Scanning in
    sorted order puts 111 in P... which is fine here, but the reverse order
    would strand 112. The matching finds the assignment that fills both.

    possible allocations   111->P, 112->Q  |  111->Q, 112 stranded
    filled slots           2                |  1
    requirements satisfied 2                |  1
    preferred by           BOTH objectives
    """
    reqs = [
        _root(),
        {"code": "P", "name": "P", "requirement_type": "course", "sort_order": 0,
         "parent": "ROOT", "requirement_system": "major", "courses": ["01:198:111"]},
        {"code": "Q", "name": "Q", "requirement_type": "choose_n", "min_count": 1,
         "sort_order": 1, "parent": "ROOT", "requirement_system": "major",
         "courses": ["01:198:111", "01:198:112"]},
    ]
    result = _run(cs_session, reqs, ["01:198:111", "01:198:112"], ref="case-b")

    assert _filled_slots(result, ["P", "Q"]) == 2
    assert _satisfied(result, ["P", "Q"]) == {"P", "Q"}


# ==========================================================================
# Case C - nested choose_n: equal slots, DIFFERENT requirements satisfied
# ==========================================================================


def test_case_c_equal_slots_different_satisfaction(cs_session) -> None:
    """The first true divergence.

    G is all_of[R1 (choose 2), R2 (choose 1), R3 (choose 1)].
      01:198:111 -> eligible R1, R2
      01:198:112 -> eligible R1, R3

    Two allocations both fill TWO slots:

    possible allocations   111->R1, 112->R1        |  111->R2, 112->R3
    filled slots           2                       |  2
    requirements satisfied 1  (R1 only)            |  2  (R2 and R3)
    group G                partially satisfied     |  partially satisfied
    preferred by           max-slots is INDIFFERENT|  max-satisfied strictly

    Maximum-cardinality matching cannot tell these apart. What saves the
    current implementation here is the most-constrained-first tie-break: R2
    and R3 have one eligible course each, R1 has two, so the scarce ones are
    served first and the better allocation is reached by heuristic.

    That is worth stating precisely: the current code gets the right answer
    here for a reason that is NOT its stated objective.
    """
    reqs = [
        _root(),
        {"code": "G", "name": "G", "requirement_type": "all_of", "sort_order": 0,
         "parent": "ROOT", "requirement_system": "major"},
        {"code": "R1", "name": "R1", "requirement_type": "choose_n", "min_count": 2,
         "sort_order": 0, "parent": "G", "requirement_system": "major",
         "courses": ["01:198:111", "01:198:112"]},
        {"code": "R2", "name": "R2", "requirement_type": "choose_n", "min_count": 1,
         "sort_order": 1, "parent": "G", "requirement_system": "major",
         "courses": ["01:198:111"]},
        {"code": "R3", "name": "R3", "requirement_type": "choose_n", "min_count": 1,
         "sort_order": 2, "parent": "G", "requirement_system": "major",
         "courses": ["01:198:112"]},
    ]
    result = _run(cs_session, reqs, ["01:198:111", "01:198:112"], ref="case-c")

    assert _filled_slots(result, ["R1", "R2", "R3"]) == 2
    # Current behaviour reaches the max-satisfied answer via the tie-break.
    assert _satisfied(result, ["R1", "R2", "R3"]) == {"R2", "R3"}


def test_case_c2_slots_spent_on_an_unsatisfiable_requirement(cs_session) -> None:
    """The divergence the tie-break does NOT save, pinned as current behaviour.

    R_BIG needs 2 courses and only ONE course is eligible for it, so it can
    never be satisfied by any allocation. R_ONE needs 1 course and the same
    single course can satisfy it.

    possible allocations   111->R_BIG              |  111->R_ONE
    filled slots           1                       |  1
    requirements satisfied 0                       |  1
    preferred by           max-slots is INDIFFERENT|  max-satisfied strictly

    Most-constrained-first does not help: both requirements have exactly one
    eligible course, so the tie falls to sort order. A slot is filled in a
    requirement that cannot be completed, and a requirement that could have
    been completed reports unsatisfied.

    THIS ASSERTION PINS BEHAVIOUR THE INVESTIGATION CONSIDERS WRONG.
    """
    reqs = [
        _root(),
        {"code": "R_BIG", "name": "Needs two, only one eligible",
         "requirement_type": "choose_n", "min_count": 2, "sort_order": 0,
         "parent": "ROOT", "requirement_system": "major", "courses": ["01:198:111"]},
        {"code": "R_ONE", "name": "Needs one", "requirement_type": "choose_n",
         "min_count": 1, "sort_order": 1, "parent": "ROOT",
         "requirement_system": "major", "courses": ["01:198:111"]},
    ]
    result = _run(cs_session, reqs, ["01:198:111"], ref="case-c2")

    assert _filled_slots(result, ["R_BIG", "R_ONE"]) == 1

    # MEASURED: the course goes to R_BIG, which can never be completed.
    assert [a.requirement_code for a in result.allocation] == ["R_BIG"]
    assert _satisfied(result, ["R_BIG", "R_ONE"]) == set()
    assert _find(result, "R_BIG").satisfied_count == 1
    assert _find(result, "R_BIG").needed_count == 2
    assert _find(result, "R_ONE").status is RequirementStatus.UNSATISFIED

    # One slot filled, ZERO requirements satisfied, where one was achievable.
    # The student is told they have finished nothing, and a different reading
    # of the same transcript would have finished R_ONE.


# ==========================================================================
# Case D - distinct categories make a filled slot worthless
# ==========================================================================


def test_case_d_category_aware_allocation_fixes_the_blind_matching(cs_session) -> None:
    """FIXED IN PHASE 4.2. Kept as the historical record of the defect.

    R_AH needs 2 courses AND 2 distinct categories.
      01:013:120 -> Xp        01:070:102 -> Xp        01:070:201 -> Xo

    possible allocations   120 + 102 (both Xp)      |  120 + 201 (Xp, Xo)
    filled slots           2                        |  2
    distinct categories    1                        |  2
    requirements satisfied 0                        |  1
    preferred by           max-slots is INDIFFERENT |  max-satisfied strictly

    The ordinary matching fills slots without knowing that categories exist,
    so it chose 120 + 102 - both Xp - and reported the requirement
    unsatisfied. Phase 4.1 made the requirement able to DETECT that; Phase
    4.2 makes the allocator avoid causing it.
    """
    reqs = [
        _root("core"),
        {"code": "R_AH", "name": "Synthetic AH", "requirement_type": "choose_n",
         "min_count": 2, "min_distinct_categories": 2, "sort_order": 0,
         "parent": "ROOT", "requirement_system": "core",
         "course_categories": {
             "Xp": ["01:013:120", "01:070:102"],
             "Xo": ["01:070:201"],
         }},
    ]
    result = _run(
        cs_session, reqs, ["01:013:120", "01:070:102", "01:070:201"], ref="case-d"
    )
    ah = _find(result, "R_AH")

    assert ah.satisfied_count == 2          # two slots filled

    # Phase 4.2: the category-aware pass picks the Xp + Xo pair.
    chosen = sorted(a.course.course_string for a in result.allocation)
    assert chosen == ["01:013:120", "01:070:201"]
    assert ah.distinct_categories == 2
    assert ah.status is RequirementStatus.SATISFIED


# ==========================================================================
# Case E - credit requirement vs count requirement
# ==========================================================================


def test_case_e_credit_and_count_do_not_compete(cs_session) -> None:
    """Phase 4.1 already resolved this one; pinned so it stays resolved.

    R_CNT needs one course and 01:070:111 is its only option. R_CRD needs 6
    credits and 01:070:111 would also serve it.

    possible allocations   111->R_CRD (+201,212)    |  111->R_CNT, 201+212->R_CRD
    filled slots           counted differently      |  -
    requirements satisfied 1                        |  2
    credits toward R_CRD   10                       |  6
    preferred by           -                        |  BOTH objectives

    Credit requirements are settled after the matching precisely so the
    second allocation is the one produced.
    """
    reqs = [
        _root("core"),
        {"code": "R_CNT", "name": "One course", "requirement_type": "choose_n",
         "min_count": 1, "sort_order": 0, "parent": "ROOT",
         "requirement_system": "core", "courses": ["01:070:111"]},
        {"code": "R_CRD", "name": "Six credits", "requirement_type": "credits",
         "min_credits": 6, "sort_order": 1, "parent": "ROOT",
         "requirement_system": "core",
         "courses": ["01:070:111", "01:070:201", "01:070:212"]},
    ]
    result = _run(
        cs_session, reqs, ["01:070:111", "01:070:201", "01:070:212"], ref="case-e"
    )

    assert _satisfied(result, ["R_CNT", "R_CRD"]) == {"R_CNT", "R_CRD"}
    credit_courses = {
        a.course.course_string for a in result.allocation if a.requirement_code == "R_CRD"
    }
    assert "01:070:111" not in credit_courses


# ==========================================================================
# Case F - Major + Core sharing
# ==========================================================================


def test_case_f_sharing_removes_the_competition_entirely(cs_session) -> None:
    """Sharing changes the SHAPE of the problem, not the objective.

    With SHARE_ACROSS_SYSTEMS the matching runs once per system, so a course
    eligible for a major requirement and a core requirement fills one slot in
    each. There is no allocation to choose between - both are satisfied.

    possible allocations   one matching per system
    filled slots           2 (one per system)
    requirements satisfied 2
    credits                4, counted ONCE
    preferred by           both objectives agree; no conflict exists

    The objective question only arises WITHIN a system.
    """
    reqs = [
        _root(),
        {"code": "M", "name": "Major req", "requirement_type": "course",
         "sort_order": 0, "parent": "ROOT", "requirement_system": "major",
         "courses": ["01:198:111"]},
        {"code": "C", "name": "Core req", "requirement_type": "choose_n",
         "min_count": 1, "sort_order": 0, "parent": None,
         "requirement_system": "core", "courses": ["01:198:111"],
         "source_prose": "SYNTHETIC"},
    ]
    result = _run(
        cs_session, reqs, ["01:198:111"], policy="share_across_systems", ref="case-f"
    )

    assert _satisfied(result, ["M", "C"]) == {"M", "C"}
    assert result.credits_applicable_to_degree == Decimal(4)


def test_case_f2_exclusive_keeps_the_competition(cs_session) -> None:
    """The same definition under EXCLUSIVE. One slot, one requirement - and
    the objective question returns."""
    reqs = [
        _root(),
        {"code": "M", "name": "Major req", "requirement_type": "course",
         "sort_order": 0, "parent": "ROOT", "requirement_system": "major",
         "courses": ["01:198:111"]},
        {"code": "C", "name": "Core req", "requirement_type": "choose_n",
         "min_count": 1, "sort_order": 0, "parent": None,
         "requirement_system": "core", "courses": ["01:198:111"],
         "source_prose": "SYNTHETIC"},
    ]
    result = _run(cs_session, reqs, ["01:198:111"], policy="exclusive", ref="case-f2")

    assert len(_satisfied(result, ["M", "C"])) == 1
    assert _filled_slots(result, ["M", "C"]) == 1


# ==========================================================================
# Case G - an unknown requirement system
# ==========================================================================


def test_case_g_unknown_requirement_system_is_partitioned_not_rejected(
    cs_session,
) -> None:
    """`requirement_system` is an OPEN set, so a system nobody has modeled yet
    must behave predictably rather than crash or be silently dropped.

    Under SHARE_ACROSS_SYSTEMS a 'minor' system is simply a third partition
    and gets its own matching. Nothing about the objective changes; the
    question is asked once per system.
    """
    reqs = [
        _root(),
        {"code": "M", "name": "Major req", "requirement_type": "course",
         "sort_order": 0, "parent": "ROOT", "requirement_system": "major",
         "courses": ["01:198:111"]},
        {"code": "MIN", "name": "Minor req (unmodeled system)",
         "requirement_type": "choose_n", "min_count": 1, "sort_order": 0,
         "parent": None, "requirement_system": "minor",
         "courses": ["01:198:111"], "source_prose": "SYNTHETIC"},
    ]
    result = _run(
        cs_session, reqs, ["01:198:111"], policy="share_across_systems", ref="case-g"
    )

    assert _satisfied(result, ["M", "MIN"]) == {"M", "MIN"}
    systems = {a.requirement_system for a in result.allocation}
    assert systems == {"major", "minor"}
    # Still counted once, even across a system the engine has never seen.
    assert result.credits_applicable_to_degree == Decimal(4)


def test_case_g2_unknown_system_under_exclusive_shares_nothing(cs_session) -> None:
    """EXCLUSIVE collapses every system into one partition, including unknown
    ones - ambiguity fails safe rather than granting free credit."""
    reqs = [
        _root(),
        {"code": "M", "name": "Major req", "requirement_type": "course",
         "sort_order": 0, "parent": "ROOT", "requirement_system": "major",
         "courses": ["01:198:111"]},
        {"code": "MIN", "name": "Minor req (unmodeled system)",
         "requirement_type": "choose_n", "min_count": 1, "sort_order": 0,
         "parent": None, "requirement_system": "minor",
         "courses": ["01:198:111"], "source_prose": "SYNTHETIC"},
    ]
    result = _run(cs_session, reqs, ["01:198:111"], policy="exclusive", ref="case-g2")

    assert len(_satisfied(result, ["M", "MIN"])) == 1


# ==========================================================================
# determinism holds regardless of which objective is used
# ==========================================================================


def test_every_counterexample_is_deterministic(cs_session) -> None:
    """Whatever the allocator decides in the ambiguous cases, it must decide
    the same thing every time. Determinism is orthogonal to the objective."""
    reqs = [
        _root(),
        {"code": "R_BIG", "name": "Needs two", "requirement_type": "choose_n",
         "min_count": 2, "sort_order": 0, "parent": "ROOT",
         "requirement_system": "major", "courses": ["01:198:111"]},
        {"code": "R_ONE", "name": "Needs one", "requirement_type": "choose_n",
         "min_count": 1, "sort_order": 1, "parent": "ROOT",
         "requirement_system": "major", "courses": ["01:198:111"]},
    ]
    version = _load(cs_session, reqs)
    student = _student_on(cs_session, version, "det")
    _enroll(cs_session, student, "01:198:111")

    runs = []
    for _ in range(3):
        result = _audit(cs_session, student)
        runs.append(
            sorted((a.requirement_code, a.course.course_string) for a in result.allocation)
        )
    assert runs[0] == runs[1] == runs[2]
