"""Requirement sharing and double-counting (Phase 3.75).

## What changed and why

Before this phase the allocator maintained one invariant:

    one student course -> at most ONE requirement slot, anywhere

That is exactly what a single bipartite matching gives you, and it was correct
while the major was the only modeled system.

Rutgers SAS states that "a course used to meet core goals may also be used to
fulfill a major or minor requirement". So the invariant is too strict once
Core exists - but only in ONE direction. A course must still never fill two
slots inside the same system.

The new invariant:

    one student course -> at most one slot PER SYSTEM,
                          and only when policy permits sharing

## Synthetic data

The Core requirements below are **SYNTHETIC**. Core Curriculum has not been
ingested, and inventing Rutgers core goals would be exactly the kind of
unsupported claim this project refuses to make. They exist only to exercise
the sharing mechanism, and are labelled so nobody mistakes them for real
requirements. The COURSES are real.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from app.domain.audit import RequirementStatus
from app.models import ProgramVersion, Requirement, SharingPolicy, Student
from app.services.audit import DegreeAuditEngine
from app.services.audit.allocation import Candidate, Slot, allocate
from coursepilot_ingestion.loaders.requirements import RequirementLoader
from sqlalchemy import select

from tests.test_degree_audit import _audit, _course, _enroll, _find

# --------------------------------------------------------------------------
# SYNTHETIC program definitions
# --------------------------------------------------------------------------

_SOURCE = {
    "url": "synthetic://coursepilot/test/requirement-sharing",
    "catalog_year": "2030-2031",
    "retrieved_at": "2026-09-14",
    "kind": "manual_curation",
    "curation_status": "synthetic",
}


def _definition(sharing_policy: str, *, with_core: bool = True) -> dict:
    """A tiny two-system program. SYNTHETIC - not a Rutgers program.

    MAJOR_A and CORE_B are both satisfied by the same real course
    (01:198:111), which is the whole point: it is the shared case.
    """
    requirements = [
        {
            "code": "ROOT",
            "name": "Synthetic Degree",
            "requirement_type": "all_of",
            "sort_order": 0,
            "parent": None,
            "requirement_system": "major",
            "source_prose": "SYNTHETIC",
        },
        {
            "code": "MAJOR_A",
            "name": "Major Requirement A",
            "requirement_type": "course",
            "sort_order": 0,
            "parent": "ROOT",
            "requirement_system": "major",
            "courses": ["01:198:111"],
        },
        {
            "code": "MAJOR_B",
            "name": "Major Requirement B",
            "requirement_type": "course",
            "sort_order": 1,
            "parent": "ROOT",
            "requirement_system": "major",
            # Deliberately the SAME course as MAJOR_A: this is the invalid
            # same-system double-use case that must stay impossible.
            "courses": ["01:198:111"],
        },
    ]
    if with_core:
        requirements.append(
            {
                "code": "CORE_B",
                "name": "Core Goal B (synthetic)",
                "requirement_type": "course",
                "sort_order": 0,
                "parent": None,
                "requirement_system": "core",
                "courses": ["01:198:111"],
                "source_prose": "SYNTHETIC",
            }
        )
    return {
        "source": _SOURCE,
        "school": {"code": "SAS", "name": "School of Arts and Sciences", "campus_code": "NB"},
        "program": {"code": "999", "name": "Synthetic Program", "degree_type": "BA"},
        "program_version": {
            "catalog_year": "2030-2031",
            "total_credits_min": 12,
            "sharing_policy": sharing_policy,
            "source_prose": "SYNTHETIC TEST DATA - not a Rutgers requirement.",
        },
        "requirements": requirements,
    }


def _load(session, sharing_policy: str, **kwargs) -> ProgramVersion:
    definition = _definition(sharing_policy, **kwargs)
    RequirementLoader(session).load(definition, json.dumps(definition).encode())
    session.commit()
    return session.scalar(
        select(ProgramVersion).where(ProgramVersion.catalog_year == "2030-2031")
    )


def _student_on(session, version: ProgramVersion) -> Student:
    student = Student(
        external_ref="sharing-test",
        catalog_year=version.catalog_year,
        program_version_id=version.id,
    )
    session.add(student)
    session.flush()
    return student


# ==========================================================================
# Case A - Major + Core, one course, both satisfied, credits counted ONCE
# ==========================================================================


def test_case_a_course_satisfies_major_and_core(cs_session) -> None:
    version = _load(cs_session, SharingPolicy.SHARE_ACROSS_SYSTEMS.value)
    student = _student_on(cs_session, version)
    _enroll(cs_session, student, "01:198:111")  # 4 credits, real course

    result = _audit(cs_session, student)

    assert _find(result, "MAJOR_A").status is RequirementStatus.SATISFIED
    assert _find(result, "CORE_B").status is RequirementStatus.SATISFIED


def test_case_a_credits_are_counted_once_not_twice(cs_session) -> None:
    """The regression that matters most.

    One 4-credit course satisfying two requirements must contribute 4 credits
    to the degree, not 8. Requirement satisfaction and credit accounting are
    different quantities.
    """
    version = _load(cs_session, SharingPolicy.SHARE_ACROSS_SYSTEMS.value)
    student = _student_on(cs_session, version)
    course = _course(cs_session, "01:198:111")
    _enroll(cs_session, student, "01:198:111")

    result = _audit(cs_session, student)

    # Two requirements satisfied by one course...
    assert len(result.allocation) == 2
    # ...but the credits are counted once.
    assert result.credits_completed == course.credits
    assert result.credits_applicable_to_degree == course.credits


def test_case_a_allocation_explains_the_sharing(cs_session) -> None:
    """The audit must be able to say WHY a course counted twice."""
    version = _load(cs_session, SharingPolicy.SHARE_ACROSS_SYSTEMS.value)
    student = _student_on(cs_session, version)
    _enroll(cs_session, student, "01:198:111")

    result = _audit(cs_session, student)

    systems = {a.requirement_system for a in result.allocation}
    assert systems == {"major", "core"}

    shared = result.shared_allocations
    assert len(shared) == 2
    major_alloc = next(a for a in shared if a.requirement_system == "major")
    assert major_alloc.shared_with_systems == ["core"]
    assert "sharing policy permits" in major_alloc.reason
    assert result.sharing_policy == SharingPolicy.SHARE_ACROSS_SYSTEMS.value


# ==========================================================================
# Case B / C - single-system behaviour is unchanged
# ==========================================================================


def test_case_b_major_only_is_unchanged(cs_session) -> None:
    version = _load(cs_session, SharingPolicy.EXCLUSIVE.value, with_core=False)
    student = _student_on(cs_session, version)
    _enroll(cs_session, student, "01:198:111")

    result = _audit(cs_session, student)

    assert _find(result, "MAJOR_A").status is RequirementStatus.SATISFIED
    # Still cannot fill both major slots from one course.
    assert _find(result, "MAJOR_B").status is RequirementStatus.UNSATISFIED
    assert len(result.allocation) == 1


def test_case_c_core_only_behaves_normally(cs_session) -> None:
    version = _load(cs_session, SharingPolicy.SHARE_ACROSS_SYSTEMS.value)
    student = _student_on(cs_session, version)
    # A course eligible only for the core requirement would be ideal, but
    # 111 serves: with no other enrolment, core must still be satisfied.
    _enroll(cs_session, student, "01:198:111")

    result = _audit(cs_session, student)
    core = _find(result, "CORE_B")

    assert core.status is RequirementStatus.SATISFIED
    assert core.allocated_courses[0].course_string == "01:198:111"


# ==========================================================================
# Case D - invalid same-system double use stays impossible
# ==========================================================================


def test_case_d_one_course_cannot_fill_two_slots_in_the_same_system(
    cs_session,
) -> None:
    """Even under SHARE_ACROSS_SYSTEMS.

    Sharing is across systems only. MAJOR_A and MAJOR_B are both eligible for
    01:198:111 and both live in `major`, so exactly one may be satisfied.
    """
    version = _load(cs_session, SharingPolicy.SHARE_ACROSS_SYSTEMS.value)
    student = _student_on(cs_session, version)
    _enroll(cs_session, student, "01:198:111")

    result = _audit(cs_session, student)

    major_satisfied = [
        code
        for code in ("MAJOR_A", "MAJOR_B")
        if _find(result, code).status is RequirementStatus.SATISFIED
    ]
    assert len(major_satisfied) == 1, "a course filled two slots in one system"

    major_allocs = [a for a in result.allocation if a.requirement_system == "major"]
    assert len(major_allocs) == 1


def test_case_d_exclusive_policy_forbids_cross_system_sharing_too(cs_session) -> None:
    """Ambiguity fails safe: no stated policy means no sharing."""
    version = _load(cs_session, SharingPolicy.EXCLUSIVE.value)
    student = _student_on(cs_session, version)
    _enroll(cs_session, student, "01:198:111")

    result = _audit(cs_session, student)

    assert len(result.allocation) == 1
    assert result.shared_allocations == []
    satisfied = [
        code
        for code in ("MAJOR_A", "MAJOR_B", "CORE_B")
        if _find(result, code).status is RequirementStatus.SATISFIED
    ]
    assert len(satisfied) == 1


def test_default_policy_is_exclusive(cs_session) -> None:
    """A curated definition that says nothing about sharing must not get it."""
    definition = _definition(SharingPolicy.EXCLUSIVE.value)
    del definition["program_version"]["sharing_policy"]
    RequirementLoader(cs_session).load(definition, json.dumps(definition).encode())
    cs_session.commit()

    version = cs_session.scalar(
        select(ProgramVersion).where(ProgramVersion.catalog_year == "2030-2031")
    )
    assert version.sharing_policy == SharingPolicy.EXCLUSIVE.value


def test_requirements_default_to_the_major_system(cs_session) -> None:
    """Existing curated CS requirements carry no system; they must land in
    `major` so Phase 3 behaviour is unchanged."""
    for req in cs_session.scalars(
        select(Requirement).join(ProgramVersion).where(
            ProgramVersion.catalog_year == "2026-2027"
        )
    ).all():
        assert req.requirement_system == "major"


# ==========================================================================
# Case E - the 01:198:344 regression must survive
# ==========================================================================


def test_case_e_required_slot_still_beats_the_elective_pool(cs_session) -> None:
    """Phase 3's most-constrained-first fix, re-verified after sharing.

    01:198:344 is eligible for the required CS_344 node AND the 53-option
    elective pool. It must go to the required node.
    """
    from tests.test_degree_audit import _student

    student = _student(cs_session)
    _enroll(cs_session, student, "01:198:344")

    result = _audit(cs_session, student)

    assert _find(result, "CS_344").status is RequirementStatus.SATISFIED
    assert _find(result, "CS_ELECTIVES").satisfied_count == 0
    assert [
        a.requirement_code for a in result.allocation if a.course.course_string == "01:198:344"
    ] == ["CS_344"]


def test_case_e_elective_pool_still_fills_normally(cs_session) -> None:
    from tests.test_degree_audit import _student

    student = _student(cs_session)
    for code in ("01:198:314", "01:198:323", "01:198:334"):
        _enroll(cs_session, student, code)

    result = _audit(cs_session, student)
    assert _find(result, "CS_ELECTIVES").satisfied_count == 3


def test_case_e_no_course_allocated_twice_under_exclusive(cs_session) -> None:
    """The real CS program is EXCLUSIVE, so the old invariant still holds."""
    from tests.test_degree_audit import CORE, MATH, _student

    student = _student(cs_session)
    for code in CORE + MATH:
        _enroll(cs_session, student, code)

    result = _audit(cs_session, student)

    keys = [(a.course.course_string, a.term_code) for a in result.allocation]
    assert len(keys) == len(set(keys))
    assert result.shared_allocations == []


# ==========================================================================
# Case F - determinism
# ==========================================================================


def test_case_f_shared_allocation_is_deterministic(cs_session) -> None:
    version = _load(cs_session, SharingPolicy.SHARE_ACROSS_SYSTEMS.value)
    student = _student_on(cs_session, version)
    _enroll(cs_session, student, "01:198:111")
    cs_session.commit()

    engine = DegreeAuditEngine(cs_session)
    runs = [
        sorted(
            (a.course.course_string, a.requirement_code, a.requirement_system)
            for a in engine.audit(student).allocation
        )
        for _ in range(5)
    ]
    assert all(r == runs[0] for r in runs)


# ==========================================================================
# Unit tests of the allocator itself
# ==========================================================================


def _slot(code, system, options=1, order=0):
    return Slot(code, 0, sort_key=(order, code), option_count=options, system=system)


def test_exclusive_allocation_is_byte_for_byte_the_old_behaviour() -> None:
    """Sharing support must not change EXCLUSIVE results at all."""
    slots = [_slot("R1", "major", options=2), _slot("R2", "core", options=1, order=1)]
    candidates = [
        Candidate("A", ("A",), frozenset({"R1", "R2"})),
        Candidate("B", ("B",), frozenset({"R1"})),
    ]

    plan = allocate(slots, candidates, share_across_systems=False)

    assert len(plan.by_slot) == 2
    # Each course still occupies exactly one slot.
    assert all(len(plan.slots_for_course(c)) == 1 for c in ("A", "B"))
    assert plan.shared_course_keys == set()


def test_sharing_lets_one_course_fill_one_slot_per_system() -> None:
    slots = [_slot("MAJOR_A", "major"), _slot("CORE_B", "core")]
    candidates = [Candidate("X", ("X",), frozenset({"MAJOR_A", "CORE_B"}))]

    plan = allocate(slots, candidates, share_across_systems=True)

    assert len(plan.by_slot) == 2
    assert plan.slots_for_course("X") == [("CORE_B", 0), ("MAJOR_A", 0)]
    assert plan.systems_for_course("X") == ["core", "major"]
    assert plan.shared_course_keys == {"X"}


def test_sharing_never_fills_two_slots_in_one_system() -> None:
    slots = [_slot("MAJOR_A", "major"), _slot("MAJOR_B", "major", order=1)]
    candidates = [Candidate("X", ("X",), frozenset({"MAJOR_A", "MAJOR_B"}))]

    plan = allocate(slots, candidates, share_across_systems=True)

    assert len(plan.by_slot) == 1
    assert len(plan.slots_for_course("X")) == 1


def test_most_constrained_first_still_applies_within_a_system() -> None:
    """The Phase 3 fix operates per system, not globally."""
    slots = [
        _slot("REQUIRED", "major", options=1, order=5),
        _slot("POOL", "major", options=50, order=0),
    ]
    candidates = [Candidate("X", ("X",), frozenset({"REQUIRED", "POOL"}))]

    plan = allocate(slots, candidates, share_across_systems=True)

    assert plan.slots_for_course("X") == [("REQUIRED", 0)]


def test_sharing_is_order_independent() -> None:
    slots = [_slot("MAJOR_A", "major"), _slot("CORE_B", "core")]
    a = Candidate("A", ("A",), frozenset({"MAJOR_A", "CORE_B"}))
    b = Candidate("B", ("B",), frozenset({"MAJOR_A"}))

    first = allocate(slots, [a, b], share_across_systems=True)
    second = allocate(list(reversed(slots)), [b, a], share_across_systems=True)

    assert first.by_slot == second.by_slot


def test_unknown_system_is_simply_its_own_partition() -> None:
    """A system nobody has modeled yet must not crash the allocator - it just
    becomes another partition. This is why requirement_system is an open
    string rather than a constrained enum column."""
    slots = [_slot("A", "major"), _slot("B", "school_requirements")]
    candidates = [Candidate("X", ("X",), frozenset({"A", "B"}))]

    plan = allocate(slots, candidates, share_across_systems=True)

    assert plan.systems_for_course("X") == ["major", "school_requirements"]


# ==========================================================================
# Interaction with nested requirement types
# ==========================================================================


def test_sharing_does_not_disturb_nested_all_of(cs_session) -> None:
    """The real CS tree (all_of -> course leaves, choose_n with constraints)
    must evaluate identically under the new allocator."""
    from tests.test_degree_audit import MATH, _student

    student = _student(cs_session)
    for code in MATH:
        _enroll(cs_session, student, code)

    result = _audit(cs_session, student)
    math = _find(result, "CS_MATH")

    assert math.status is RequirementStatus.SATISFIED
    assert math.satisfied_count == 3


def test_sharing_does_not_disturb_choose_n_constraints(cs_session) -> None:
    from tests.test_degree_audit import _student

    student = _student(cs_session)
    for code in ("01:198:314", "01:198:323"):
        _enroll(cs_session, student, code)

    result = _audit(cs_session, student)
    electives = _find(result, "CS_ELECTIVES")

    assert electives.needed_count == 5
    assert electives.satisfied_count == 2
    assert electives.status is RequirementStatus.PARTIALLY_SATISFIED


def test_credit_totals_unaffected_by_sharing_in_exclusive_program(
    cs_session,
) -> None:
    from tests.test_degree_audit import _student

    student = _student(cs_session)
    _enroll(cs_session, student, "01:198:111")
    course = _course(cs_session, "01:198:111")

    result = _audit(cs_session, student)
    assert result.credits_applicable_to_degree == course.credits
