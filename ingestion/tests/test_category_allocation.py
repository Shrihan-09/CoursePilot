"""Category-aware allocation, both strategies (Phase 4.2).

Two implementations of the same contract are tested against one another:

  * CategorySlotStrategy     - one vertex per available CATEGORY, matched
  * CategoryCoverageStrategy - enumerate subsets, score by coverage

They are independent: one solves a matching, the other searches and uses a
matching only to SCORE. Agreement between them is therefore evidence, not a
tautology.

Equivalence is defined semantically, per the brief: same filled slots, same
distinct-category coverage, same single-use rule. WHICH courses were chosen
may differ where several optima exist.

## Rutgers semantics used here

Case F asks whether ONE course certified for two categories can satisfy a
two-category requirement. It cannot, and that is verified rather than
inferred:

  * SAS, Arts and Humanities: "Students must take two degree credit-bearing
    courses and meet at least two of these goals."
  * SAS Core FAQ: a course on both the HST and SCL lists still leaves a
    student needing "at least one course that meets HST and one that meets
    SCL for a total of TWO courses (6 credits)."

One course, one category position.

## Synthetic data

Requirement DEFINITIONS are SYNTHETIC. The COURSES are real.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from app.domain.audit import RequirementStatus
from app.models import ProgramVersion, Student
from app.services.audit.categories import (
    ALL_STRATEGIES,
    CategoryAllocation,
    CategoryCandidate,
    CategoryCoverageStrategy,
    CategoryRequest,
    CategorySlotStrategy,
)
from coursepilot_ingestion.loaders.requirements import RequirementLoader
from sqlalchemy import select

from tests.test_degree_audit import _audit, _enroll, _find

# Real courses; the letters are the labels used in the brief's matrix.
A = "01:013:120"
B = "01:070:102"
C = "01:070:201"

_SOURCE = {
    "url": "synthetic://coursepilot/test/phase-4.2-categories",
    "catalog_year": "2033-2034",
    "retrieved_at": "2026-09-20",
    "kind": "manual_curation",
    "curation_status": "synthetic",
}


# ==========================================================================
# strategy-level tests: the abstraction, exercised directly
# ==========================================================================


def _cand(key: str, *categories: str) -> CategoryCandidate:
    return CategoryCandidate(
        course_key=key, sort_key=(key,), categories=frozenset(categories)
    )


def _request(candidates, need_count=2, need_categories=2) -> CategoryRequest:
    return CategoryRequest(
        requirement_code="SYN",
        needed_count=need_count,
        needed_categories=need_categories,
        candidates=tuple(candidates),
    )


def _both(request: CategoryRequest) -> dict[str, CategoryAllocation]:
    return {s.name: s.select(request) for s in ALL_STRATEGIES}


def _assert_single_use(allocation: CategoryAllocation) -> None:
    """THE invariant: one StudentCourse, at most one allocation, per requirement."""
    keys = [course for course, _ in allocation.assignments]
    assert len(keys) == len(set(keys)), f"course allocated twice: {allocation}"


@pytest.mark.parametrize("strategy", ALL_STRATEGIES, ids=lambda s: s.name)
def test_case_a_same_category_never_chooses_the_poor_pair(strategy) -> None:
    """A -> Xp, B -> Xp, C -> Xo; need 2 courses in 2 categories.

    A+B covers one category. Both strategies must reach A+C or B+C.
    """
    result = strategy.select(_request([_cand(A, "Xp"), _cand(B, "Xp"), _cand(C, "Xo")]))

    _assert_single_use(result)
    assert result.filled_slots == 2
    assert result.distinct_categories == 2
    assert set(result.course_keys) in ({A, C}, {B, C})
    assert C in result.course_keys


@pytest.mark.parametrize("strategy", ALL_STRATEGIES, ids=lambda s: s.name)
def test_case_b_already_distinct(strategy) -> None:
    """A -> Xp, B -> Xo. Nothing to fix; both must keep both."""
    result = strategy.select(_request([_cand(A, "Xp"), _cand(B, "Xo")]))

    _assert_single_use(result)
    assert set(result.course_keys) == {A, B}
    assert result.distinct_categories == 2


@pytest.mark.parametrize("strategy", ALL_STRATEGIES, ids=lambda s: s.name)
def test_case_c_more_choices_than_needed_is_deterministic(strategy) -> None:
    """A -> Xp, B -> Xo, C -> Xq; need 2. Exactly 2 courses, 2 categories,
    and the same 2 every time."""
    request = _request([_cand(A, "Xp"), _cand(B, "Xo"), _cand(C, "Xq")])
    runs = [strategy.select(request) for _ in range(5)]

    for result in runs:
        _assert_single_use(result)
        assert result.filled_slots == 2
        assert result.distinct_categories == 2
    assert all(r.assignments == runs[0].assignments for r in runs)


@pytest.mark.parametrize("strategy", ALL_STRATEGIES, ids=lambda s: s.name)
def test_case_d_impossible_is_reported_not_fabricated(strategy) -> None:
    """A -> Xp, B -> Xp only. Two categories are not achievable.

    The requirement must come back one-category short. No category is
    invented, and no course is used twice to manufacture one.
    """
    result = strategy.select(_request([_cand(A, "Xp"), _cand(B, "Xp")]))

    _assert_single_use(result)
    assert result.filled_slots == 2
    assert result.distinct_categories == 1


@pytest.mark.parametrize("strategy", ALL_STRATEGIES, ids=lambda s: s.name)
def test_case_e_multi_category_course_is_not_used_twice(strategy) -> None:
    """A -> Xp + Xq, B -> Xo; need 2 courses in 2 categories.

    A may take ONE of its categories. A+B reaches two categories legitimately
    because two different COURSES are involved.
    """
    result = strategy.select(_request([_cand(A, "Xp", "Xq"), _cand(B, "Xo")]))

    _assert_single_use(result)
    assert set(result.course_keys) == {A, B}
    assert result.distinct_categories == 2
    # A contributed exactly one category, and it is one A actually holds.
    a_categories = [cat for course, cat in result.assignments if course == A]
    assert len(a_categories) == 1
    assert a_categories[0] in {"Xp", "Xq"}


@pytest.mark.parametrize("strategy", ALL_STRATEGIES, ids=lambda s: s.name)
def test_case_f_one_multi_category_course_alone_covers_one_category(strategy) -> None:
    """A -> Xp + Xq, alone, for a two-category requirement.

    VERIFIED from Rutgers wording, not inferred - see the module docstring.
    A single course occupies a single category position, so this covers ONE
    category and fills ONE slot. It satisfies neither condition.
    """
    result = strategy.select(_request([_cand(A, "Xp", "Xq")]))

    _assert_single_use(result)
    assert result.filled_slots == 1
    assert result.distinct_categories == 1


@pytest.mark.parametrize("strategy", ALL_STRATEGIES, ids=lambda s: s.name)
def test_multi_category_course_yields_via_augmenting_path(strategy) -> None:
    """A -> Xp + Xq, B -> Xp. The case a greedy scan gets wrong.

    Scanned in order, A takes Xp and B is stranded: one category. The right
    answer moves A to Xq, which is an augmenting path.
    """
    result = strategy.select(_request([_cand(A, "Xp", "Xq"), _cand(B, "Xp")]))

    _assert_single_use(result)
    assert result.distinct_categories == 2
    assignments = dict(result.assignments)
    assert assignments[A] == "Xq"
    assert assignments[B] == "Xp"


@pytest.mark.parametrize("strategy", ALL_STRATEGIES, ids=lambda s: s.name)
def test_requirement_needing_more_courses_than_categories(strategy) -> None:
    """3 courses meeting at least 2 goals - a legitimate shape.

    Coverage is satisfied by two courses; the third is still allocated, and
    reporting it must not inflate the distinct count.
    """
    result = strategy.select(
        _request(
            [_cand(A, "Xp"), _cand(B, "Xp"), _cand(C, "Xo")],
            need_count=3,
            need_categories=2,
        )
    )

    _assert_single_use(result)
    assert result.filled_slots == 3
    assert result.distinct_categories == 2


@pytest.mark.parametrize("strategy", ALL_STRATEGIES, ids=lambda s: s.name)
def test_courses_without_categories_are_handled(strategy) -> None:
    """A course eligible for the requirement but certified under no category.

    It can still fill a course slot; it just contributes no coverage.
    """
    result = strategy.select(_request([_cand(A, "Xp"), _cand(B)]))

    _assert_single_use(result)
    assert result.filled_slots == 2
    assert result.distinct_categories == 1


# ==========================================================================
# Strategy comparison - same input, both strategies, semantic equivalence
# ==========================================================================

_COMPARISON_CASES = {
    "same_category": [_cand(A, "Xp"), _cand(B, "Xp"), _cand(C, "Xo")],
    "already_distinct": [_cand(A, "Xp"), _cand(B, "Xo")],
    "more_than_needed": [_cand(A, "Xp"), _cand(B, "Xo"), _cand(C, "Xq")],
    "impossible": [_cand(A, "Xp"), _cand(B, "Xp")],
    "multi_category": [_cand(A, "Xp", "Xq"), _cand(B, "Xo")],
    "multi_alone": [_cand(A, "Xp", "Xq")],
    "augmenting": [_cand(A, "Xp", "Xq"), _cand(B, "Xp")],
    "uncategorized": [_cand(A, "Xp"), _cand(B)],
    "empty": [],
    "three_way_overlap": [
        _cand(A, "Xp", "Xo"),
        _cand(B, "Xo", "Xq"),
        _cand(C, "Xp", "Xq"),
    ],
}


@pytest.mark.parametrize("case", sorted(_COMPARISON_CASES))
def test_strategies_are_semantically_equivalent(case) -> None:
    """The comparison the brief asks for, over every shape tested.

    Equivalence is (filled slots, distinct categories) plus the single-use
    rule - NOT identical course choices, because several optima can exist.
    """
    request = _request(_COMPARISON_CASES[case])
    results = _both(request)

    for allocation in results.values():
        _assert_single_use(allocation)

    signatures = {name: a.signature() for name, a in results.items()}
    assert len(set(signatures.values())) == 1, signatures


@pytest.mark.parametrize("case", sorted(_COMPARISON_CASES))
def test_strategies_agree_on_coverage_for_varied_shapes(case) -> None:
    """Same comparison across several (count, categories) shapes."""
    for need_count, need_categories in ((1, 1), (2, 2), (3, 2), (3, 3)):
        request = _request(
            _COMPARISON_CASES[case],
            need_count=need_count,
            need_categories=need_categories,
        )
        results = _both(request)
        signatures = {n: a.signature() for n, a in results.items()}
        assert len(set(signatures.values())) == 1, (case, need_count, signatures)


def test_coverage_strategy_refuses_rather_than_degrading() -> None:
    """Strategy B is exponential and says so.

    Beyond its bound it raises instead of quietly returning a worse answer -
    the same principle as INDETERMINATE: never present a guess as a result.
    """
    many = [_cand(f"c{i:03d}", f"X{i % 4}") for i in range(40)]
    request = _request(many, need_count=10, need_categories=3)

    with pytest.raises(ValueError, match="coverage search exceeded"):
        CategoryCoverageStrategy().select(request)

    # The polynomial strategy handles the same input without complaint.
    result = CategorySlotStrategy().select(request)
    _assert_single_use(result)
    assert result.filled_slots == 10
    # Four categories exist and the minimum is three; covering all four is
    # correct - "at least 3" is a minimum, not a target.
    assert result.distinct_categories >= 3


# ==========================================================================
# End-to-end: the same cases through the real audit engine
# ==========================================================================


def _load(session, requirements: list[dict], policy: str = "exclusive") -> ProgramVersion:
    definition = {
        "source": _SOURCE,
        "school": {"code": "SAS", "name": "School of Arts and Sciences", "campus_code": "NB"},
        "program": {"code": "996", "name": "Synthetic Program 4.2 cat", "degree_type": "BA"},
        "program_version": {
            "catalog_year": "2033-2034",
            "total_credits_min": 12,
            "sharing_policy": policy,
            "source_prose": "SYNTHETIC TEST DATA - not a Rutgers requirement.",
        },
        "requirements": requirements,
    }
    RequirementLoader(session).load(definition, json.dumps(definition).encode())
    session.commit()
    return session.scalar(
        select(ProgramVersion).where(ProgramVersion.catalog_year == "2033-2034")
    )


def _run(session, requirements, courses, *, policy="exclusive", ref="cat"):
    version = _load(session, requirements, policy)
    student = Student(
        external_ref=ref,
        catalog_year=version.catalog_year,
        program_version_id=version.id,
    )
    session.add(student)
    session.flush()
    for course_string in courses:
        _enroll(session, student, course_string)
    return _audit(session, student)


def _ah_requirement(categories: dict[str, list[str]], **extra) -> dict:
    base = {
        "code": "SYN_AH",
        "name": "Synthetic distinct-category requirement",
        "requirement_type": "choose_n",
        "min_count": 2,
        "min_distinct_categories": 2,
        "sort_order": 0,
        "parent": None,
        "requirement_system": "core",
        "course_categories": categories,
        "source_prose": "SYNTHETIC",
    }
    base.update(extra)
    return base


def test_case_g_real_case_d_through_the_engine(cs_session) -> None:
    """Case G from the brief, end to end.

    01:013:120 -> Xp, 01:070:102 -> Xp, 01:070:201 -> Xo.
    The engine must allocate an Xp + Xo pair.
    """
    reqs = [_ah_requirement({"Xp": [A, B], "Xo": [C]})]
    result = _run(cs_session, reqs, [A, B, C], ref="case-g")
    ah = _find(result, "SYN_AH")

    assert ah.distinct_categories == 2
    assert ah.status is RequirementStatus.SATISFIED
    chosen = sorted(a.course.course_string for a in result.allocation)
    assert chosen == [A, C]


def test_case_g_engine_reports_the_selected_edges(cs_session) -> None:
    """The reported category is the EDGE chosen, not the union of what the
    course could have counted as."""
    reqs = [_ah_requirement({"Xp": [A], "Xq": [A], "Xo": [C]})]
    result = _run(cs_session, reqs, [A, C], ref="case-g2")
    ah = _find(result, "SYN_AH")

    assert ah.distinct_categories == 2
    # A holds two categories but contributed one allocation.
    allocated = [a for a in result.allocation if a.requirement_code == "SYN_AH"]
    assert len(allocated) == 2
    assert len({a.course.course_string for a in allocated}) == 2


def test_case_i_major_and_core_each_use_the_course_once(cs_session) -> None:
    """Case I. Sharing ACROSS systems stays legal; twice INSIDE a
    requirement never becomes legal."""
    reqs = [
        {"code": "ROOT", "name": "Root", "requirement_type": "all_of", "sort_order": 0,
         "parent": None, "requirement_system": "major", "source_prose": "SYNTHETIC"},
        {"code": "MAJ", "name": "Major req", "requirement_type": "course",
         "sort_order": 0, "parent": "ROOT", "requirement_system": "major",
         "courses": [A]},
        _ah_requirement({"Xp": [A], "Xo": [C]}),
    ]
    result = _run(
        cs_session, reqs, [A, C], policy="share_across_systems", ref="case-i"
    )

    assert _find(result, "MAJ").status is RequirementStatus.SATISFIED
    ah = _find(result, "SYN_AH")
    assert ah.distinct_categories == 2
    assert ah.status is RequirementStatus.SATISFIED

    # A is used once in major and once in core - never twice inside either.
    per_requirement: dict[str, list[str]] = {}
    for allocation in result.allocation:
        per_requirement.setdefault(allocation.requirement_code, []).append(
            allocation.course.course_string
        )
    for code, courses in per_requirement.items():
        assert len(courses) == len(set(courses)), code


def test_case_k_engine_allocation_is_deterministic(cs_session) -> None:
    """Case K."""
    reqs = [_ah_requirement({"Xp": [A, B], "Xo": [C]})]
    version = _load(cs_session, reqs)
    student = Student(
        external_ref="case-k",
        catalog_year=version.catalog_year,
        program_version_id=version.id,
    )
    cs_session.add(student)
    cs_session.flush()
    for course_string in (A, B, C):
        _enroll(cs_session, student, course_string)

    runs = []
    for _ in range(3):
        result = _audit(cs_session, student)
        runs.append(
            sorted(
                (a.requirement_code, a.course.course_string) for a in result.allocation
            )
        )
    assert runs[0] == runs[1] == runs[2]


def test_case_l_result_does_not_depend_on_surrogate_ids(cs_session) -> None:
    """Case L - reproducibility across a re-ingest.

    The category matching sorts on `sort_key`, which is the course's natural
    identity. Feeding the same candidates under different opaque keys must
    not change which courses are chosen.
    """
    natural = [_cand(A, "Xp"), _cand(B, "Xp"), _cand(C, "Xo")]
    first = CategorySlotStrategy().select(_request(natural))

    # Same courses, different surrogate keys, deliberately in a different
    # order - as a re-ingest would produce.
    relabelled = [
        CategoryCandidate(
            course_key=f"uuid-{i}:20269", sort_key=c.sort_key, categories=c.categories
        )
        for i, c in enumerate(reversed(natural))
    ]
    second = CategorySlotStrategy().select(_request(relabelled))

    key_to_course = {f"uuid-{i}:20269": c.course_key for i, c in enumerate(reversed(natural))}
    assert sorted(key_to_course[k] for k in second.course_keys) == sorted(
        first.course_keys
    )
    assert first.signature() == second.signature()


def test_requirements_without_categories_take_the_ordinary_path(cs_session) -> None:
    """The guard on section 11 of the brief.

    A requirement that declares no distinct-category minimum must behave
    exactly as before - the category pass must not touch it.
    """
    reqs = [
        {"code": "PLAIN", "name": "Any two", "requirement_type": "choose_n",
         "min_count": 2, "sort_order": 0, "parent": None,
         "requirement_system": "core", "courses": [A, B, C],
         "source_prose": "SYNTHETIC"},
    ]
    result = _run(cs_session, reqs, [A, B, C], ref="plain")
    plain = _find(result, "PLAIN")

    assert plain.status is RequirementStatus.SATISFIED
    assert plain.needed_distinct_categories is None
    assert plain.distinct_categories == 0
    # First two in deterministic course order, exactly as before Phase 4.2.
    chosen = sorted(a.course.course_string for a in result.allocation)
    assert chosen == [A, B]


# ==========================================================================
# Both strategies through the REAL engine, on the same input
# ==========================================================================


def _audit_with(session, student, strategy):
    from app.services.audit.engine import DegreeAuditEngine

    session.commit()
    return DegreeAuditEngine(session, category_strategy=strategy).audit(student)


@pytest.mark.parametrize("case_courses", [[A, B, C], [A, B], [A, C], [A]])
def test_both_strategies_agree_through_the_engine(cs_session, case_courses) -> None:
    """Section 5 of the brief: run BOTH against the same real input.

    Compared on the semantics that matter - satisfied state, coverage, slots
    filled, and the single-use rule - not on which course was picked.
    """
    reqs = [_ah_requirement({"Xp": [A, B], "Xo": [C]})]
    version = _load(cs_session, reqs)
    student = Student(
        external_ref="cmp-" + "-".join(case_courses),
        catalog_year=version.catalog_year,
        program_version_id=version.id,
    )
    cs_session.add(student)
    cs_session.flush()
    for course_string in case_courses:
        _enroll(cs_session, student, course_string)

    outcomes = {}
    for strategy in ALL_STRATEGIES:
        result = _audit_with(cs_session, student, strategy)
        ah = _find(result, "SYN_AH")
        allocated = [
            a.course.course_string
            for a in result.allocation
            if a.requirement_code == "SYN_AH"
        ]
        assert len(allocated) == len(set(allocated)), strategy.name
        outcomes[strategy.name] = (
            ah.status,
            ah.satisfied_count,
            ah.distinct_categories,
            len(allocated),
        )

    assert len(set(outcomes.values())) == 1, outcomes


def test_credits_plus_categories_is_representable_but_not_evaluated(cs_session) -> None:
    """Section 12 of the brief, answered by inspection rather than invention.

    `min_distinct_categories` lives on Requirement and carries no CHECK tying
    it to a requirement_type, so a `credits` requirement CAN store one. What
    happens then, today:

      * `_slots_needed(CREDITS)` is 0 and `min_count` is None, so the
        category pass computes a zero-length selection and makes no change;
      * `_eval_credits` never looks at categories, so the constraint is
        silently not enforced.

    No Rutgers requirement found so far has this shape - SAS states Natural
    Sciences purely in credits - so no behaviour is invented here. The
    combination is pinned as UNMODELLED rather than left to be discovered.
    """
    reqs = [
        {"code": "SYN_CRD", "name": "Credits with categories",
         "requirement_type": "credits", "min_credits": 6,
         "min_distinct_categories": 2, "sort_order": 0, "parent": None,
         "requirement_system": "core",
         "course_categories": {"Xp": [A, B], "Xo": [C]},
         "source_prose": "SYNTHETIC - no known Rutgers requirement has this shape"},
    ]
    result = _run(cs_session, reqs, [A, B], ref="crd-cat")
    crd = _find(result, "SYN_CRD")

    # Credits are allocated by the credit pass, exactly as before.
    assert crd.satisfied_credits == Decimal(7)
    assert crd.status is RequirementStatus.SATISFIED
    # ...and the category minimum is NOT enforced for a credits requirement.
    assert crd.distinct_categories == 0
