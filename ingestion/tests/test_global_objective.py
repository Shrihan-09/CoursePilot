"""Global allocation objective: adversarial cases and oracle (Phase 4.3).

INVESTIGATION artifact. Production behaviour is unchanged by this phase; these
tests pin what the current allocator does and measure it against the
mathematically optimal allocation computed by `app.services.audit.oracle`.

Where the current allocator is provably suboptimal, the test says so and names
the better allocation. Where the optimum is a TIE, the test says that too -
several of the cases the brief expected to be resolved by a global objective
turn out to be ties, which is a finding rather than a disappointment.

## Synthetic data

Requirement DEFINITIONS are SYNTHETIC and are not Rutgers requirements. The
COURSES are real.
"""

from __future__ import annotations

import json

import pytest
from app.domain.audit import RequirementStatus
from app.models import ProgramVersion, Student
from app.services.audit.oracle import (
    OracleAllocation,
    OracleProblem,
    OracleRequirement,
    OracleTooLarge,
    connected_components,
    enumerate_optima,
    lexicographic_objective,
    max_satisfied_objective,
    max_slots_objective,
    solve,
)
from coursepilot_ingestion.loaders.requirements import RequirementLoader
from sqlalchemy import select

from tests.test_degree_audit import _audit, _enroll, _find

# Real courses.
C1 = "01:013:120"
C2 = "01:070:102"
C3 = "01:070:201"
C4 = "01:070:212"

_SOURCE = {
    "url": "synthetic://coursepilot/test/phase-4.3",
    "catalog_year": "2034-2035",
    "retrieved_at": "2026-09-20",
    "kind": "manual_curation",
    "curation_status": "synthetic",
}


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _req(
    code: str,
    *,
    system: str = "core",
    count: int = 1,
    categories: dict[str, list[str]] | None = None,
    courses: list[str] | None = None,
    distinct: int = 0,
    order: int = 0,
) -> dict:
    """A leaf requirement definition for the real loader."""
    out: dict = {
        "code": code,
        "name": code,
        "requirement_type": "choose_n",
        "min_count": count,
        "sort_order": order,
        "parent": None,
        "requirement_system": system,
        "source_prose": "SYNTHETIC",
    }
    if distinct:
        out["min_distinct_categories"] = distinct
    if categories:
        out["course_categories"] = categories
    if courses:
        out["courses"] = courses
    return out


def _oracle_req(definition: dict, share: bool) -> OracleRequirement:
    """The SAME requirement, expressed for the oracle.

    Built from the definition rather than from the database, so the oracle
    never inherits an interpretation from the code it is checking.
    """
    categories: dict[str, frozenset[str]] = {}
    for course in definition.get("courses", []) or []:
        categories[course] = frozenset()
    for category, courses in (definition.get("course_categories") or {}).items():
        for course in courses:
            categories[course] = categories.get(course, frozenset()) | {category}
    return OracleRequirement(
        code=definition["code"],
        system=definition["requirement_system"] if share else "*",
        needed_count=definition.get("min_count", 0),
        needed_categories=definition.get("min_distinct_categories", 0) or 0,
        categories=categories,
        sort_key=(definition.get("sort_order", 0), definition["code"]),
    )


def _problem(definitions: list[dict], held: list[str], *, share: bool = False):
    return OracleProblem(
        requirements=tuple(_oracle_req(d, share) for d in definitions),
        course_keys=tuple(sorted(held)),
        share_across_systems=share,
    )


def _load(session, requirements: list[dict], policy: str = "exclusive") -> ProgramVersion:
    definition = {
        "source": _SOURCE,
        "school": {"code": "SAS", "name": "School of Arts and Sciences", "campus_code": "NB"},
        "program": {"code": "995", "name": "Synthetic Program 4.3", "degree_type": "BA"},
        "program_version": {
            "catalog_year": "2034-2035",
            "total_credits_min": 12,
            "sharing_policy": policy,
            "source_prose": "SYNTHETIC TEST DATA - not a Rutgers requirement.",
        },
        "requirements": requirements,
    }
    RequirementLoader(session).load(definition, json.dumps(definition).encode())
    session.commit()
    return session.scalar(
        select(ProgramVersion).where(ProgramVersion.catalog_year == "2034-2035")
    )


def _run(session, requirements, held, *, policy="exclusive", ref="glob"):
    version = _load(session, requirements, policy)
    student = Student(
        external_ref=ref,
        catalog_year=version.catalog_year,
        program_version_id=version.id,
    )
    session.add(student)
    session.flush()
    for course_string in held:
        _enroll(session, student, course_string)
    return _audit(session, student)


def _engine_satisfied(result, codes: list[str]) -> set[str]:
    return {
        code
        for code in codes
        if _find(result, code).status
        in (RequirementStatus.SATISFIED, RequirementStatus.PROVISIONALLY_SATISFIED)
    }


def _compare(session, definitions, held, *, policy="exclusive", ref="cmp"):
    """Run the real engine and the oracle on the same instance."""
    codes = [d["code"] for d in definitions]
    result = _run(session, definitions, held, policy=policy, ref=ref)
    engine_satisfied = _engine_satisfied(result, codes)
    engine_slots = len([a for a in result.allocation if a.requirement_code in codes])

    problem = _problem(
        definitions, held, share=policy == "share_across_systems"
    )
    best = solve(problem)
    return {
        "engine_satisfied": engine_satisfied,
        "engine_slots": engine_slots,
        "oracle_satisfied": set(best.satisfied(problem)),
        "oracle_slots": best.filled_slots,
        "oracle": best,
        "problem": problem,
        "result": result,
    }


# ==========================================================================
# Oracle correctness - the oracle itself must be trustworthy first
# ==========================================================================


def test_oracle_returns_only_valid_allocations() -> None:
    """Validity is re-checked from scratch, not assumed from construction."""
    problem = _problem(
        [
            _req("R1", count=2, courses=[C1, C2]),
            _req("R2", count=1, courses=[C1], order=1),
        ],
        [C1, C2],
    )
    best = solve(problem)
    assert best.is_valid(problem)


def test_oracle_respects_single_use_within_a_system() -> None:
    """One course cannot serve two requirements in one system."""
    problem = _problem(
        [
            _req("R1", count=1, courses=[C1]),
            _req("R2", count=1, courses=[C1], order=1),
        ],
        [C1],
    )
    best = solve(problem)
    assert best.filled_slots == 1
    assert len(best.satisfied(problem)) == 1


def test_oracle_respects_capacity() -> None:
    problem = _problem([_req("R1", count=1, courses=[C1, C2])], [C1, C2])
    best = solve(problem)
    assert best.filled_slots == 1


def test_oracle_never_reuses_a_category() -> None:
    """A course certified for two categories occupies ONE of them."""
    problem = _problem(
        [_req("AH", count=2, distinct=2, categories={"Xp": [C1], "Xq": [C1]})],
        [C1],
    )
    best = solve(problem)
    assert best.filled_slots == 1
    assert len(set(best.categories_for("AH"))) == 1
    assert best.satisfied(problem) == frozenset()


def test_oracle_refuses_rather_than_guessing_when_too_large() -> None:
    """An oracle that silently gave up would agree with anything."""
    many = [f"course-{i}" for i in range(40)]
    definitions = [
        _req(f"R{i}", count=2, courses=many, order=i) for i in range(6)
    ]
    problem = _problem(definitions, many)
    with pytest.raises(OracleTooLarge):
        solve(problem, max_states=10_000)


def test_oracle_agrees_with_the_real_evaluator(cs_session) -> None:
    """The oracle's satisfaction rule must match the production evaluator.

    Otherwise every comparison in this file measures a disagreement about
    what 'satisfied' means rather than about allocation quality.
    """
    definitions = [
        _req("AH", count=2, distinct=2, categories={"Xp": [C1, C2], "Xo": [C3]}),
        _req("OTHER", count=1, courses=[C4], order=1),
    ]
    outcome = _compare(cs_session, definitions, [C1, C2, C3, C4], ref="agree")

    assert outcome["engine_satisfied"] == outcome["oracle_satisfied"]
    assert outcome["engine_slots"] == outcome["oracle_slots"]


# ==========================================================================
# Case 1 - completion vs partial progress (symmetric)
# ==========================================================================


def test_case_1_symmetric_completion_is_a_tie(cs_session) -> None:
    """A satisfied + B partial, versus A partial + B satisfied.

    Both allocations satisfy exactly ONE requirement and fill the same slots.
    Every objective examined is INDIFFERENT, so the choice is a deterministic
    tie-break - not an optimization question at all.
    """
    definitions = [
        _req("A", count=2, courses=[C1, C2]),
        _req("B", count=2, courses=[C1, C2], order=1),
    ]
    problem = _problem(definitions, [C1, C2])
    score, optima = enumerate_optima(problem)

    assert score == (1, 2)
    satisfied_sets = {tuple(sorted(a.satisfied(problem))) for a in optima}
    assert satisfied_sets == {("A",), ("B",)}, satisfied_sets

    # The engine picks one of them, deterministically.
    outcome = _compare(cs_session, definitions, [C1, C2], ref="case-1")
    assert len(outcome["engine_satisfied"]) == 1
    assert outcome["engine_satisfied"] <= {"A", "B"}


# ==========================================================================
# Case 2 - one completion vs two partials
# ==========================================================================


def test_case_2_one_completion_beats_two_partials(cs_session) -> None:
    """THE product question, made concrete.

    Allocation X: A satisfied (2 courses), B empty        -> 1 satisfied
    Allocation Y: A 1 of 2, B 1 of 2                      -> 0 satisfied

    Both fill two slots. max-slots is indifferent; maximize-satisfied
    strictly prefers X, and so does the Rutgers statement that the audit
    should leave "the maximum number of requirements complete".

    A student mid-degree might prefer Y, because it shows movement on two
    fronts. That preference has no support in any Rutgers source, so it is
    recorded as an open product decision rather than implemented.
    """
    definitions = [
        _req("A", count=2, courses=[C1, C2]),
        _req("B", count=2, courses=[C1, C2], order=1),
    ]
    problem = _problem(definitions, [C1, C2])

    lex = solve(problem, lexicographic_objective)
    slots_only = solve(problem, max_slots_objective)

    assert len(lex.satisfied(problem)) == 1
    assert lex.filled_slots == 2
    # max-slots can reach the same slot count with ZERO requirements done.
    assert slots_only.filled_slots == 2

    spread = OracleAllocation(
        tuple(sorted([(C1, "A", ""), (C2, "B", "")]))
    )
    assert spread.is_valid(problem)
    assert spread.filled_slots == 2
    assert len(spread.satisfied(problem)) == 0
    assert lexicographic_objective(lex, problem) > lexicographic_objective(
        spread, problem
    )

    # The current engine already reaches a completion here.
    outcome = _compare(cs_session, definitions, [C1, C2], ref="case-2")
    assert len(outcome["engine_satisfied"]) == 1


# ==========================================================================
# Case 3 - dead-end requirement (the Case C2 defect)
# ==========================================================================


def test_case_3_dead_end_requirement_now_matches_the_oracle(cs_session) -> None:
    """R_BIG needs 2 courses and only 1 is eligible: it can NEVER finish.

    This was the one adversarial case where the engine was provably
    suboptimal, and it was the whole argument for a global objective. Phase
    4.5 adopted one, so the engine now reaches the oracle's answer.
    """
    definitions = [
        _req("R_BIG", count=2, courses=[C1]),
        _req("R_ONE", count=1, courses=[C1], order=1),
    ]
    outcome = _compare(cs_session, definitions, [C1], ref="case-3")

    # Oracle: one requirement can be completed.
    assert outcome["oracle_satisfied"] == {"R_ONE"}
    assert outcome["oracle_slots"] == 1

    # Engine now agrees with the oracle.
    assert outcome["engine_slots"] == 1
    assert outcome["engine_satisfied"] == {"R_ONE"}
    assert outcome["engine_satisfied"] == outcome["oracle_satisfied"]


# ==========================================================================
# Case 4 - Case H, and why a global objective does NOT resolve it
# ==========================================================================


def test_case_4_case_h_is_a_tie_under_every_objective(cs_session) -> None:
    """The headline finding of Phase 4.3.

    01:013:311 is eligible for CCD (1 course) and AH (2 courses, 2 goals);
    01:013:203 is eligible for AH under two goals.

        A -> CCD, B -> AH     CCD satisfied, AH 1 of 2     1 satisfied
        A -> AH,  B -> AH     AH satisfied,  CCD unsat     1 satisfied

    Both fill two slots and complete exactly one requirement. The brief
    expected a global objective to prefer the second; it does not. Only a
    WEIGHTING that ranks AH above CCD could break this tie, and no Rutgers
    source ranks core goals against one another.

    So Case H stays as it is - not because the fix is hard, but because the
    mathematics says there is nothing to fix.
    """
    definitions = [
        _req("CCD", count=1, courses=[C1]),
        _req(
            "AH",
            count=2,
            distinct=2,
            categories={"Xp": [C1], "Xo": [C2], "Xq": [C2]},
            order=1,
        ),
    ]
    problem = _problem(definitions, [C1, C2])
    score, optima = enumerate_optima(problem)

    assert score == (1, 2)
    satisfied_sets = {tuple(sorted(a.satisfied(problem))) for a in optima}
    assert ("CCD",) in satisfied_sets
    assert ("AH",) in satisfied_sets

    outcome = _compare(cs_session, definitions, [C1, C2], ref="case-4")
    assert len(outcome["engine_satisfied"]) == len(outcome["oracle_satisfied"]) == 1


# ==========================================================================
# Case 5 - a global objective must not bypass category semantics
# ==========================================================================


def test_case_5_global_objective_cannot_fake_a_category(cs_session) -> None:
    """Two courses, both Xp, for a 2-course / 2-goal requirement.

    No allocation satisfies it, and the optimizer must not manufacture one by
    counting a course under two goals. Phase 4.2 semantics are a CONSTRAINT
    on the search, not a competing preference.
    """
    definitions = [
        _req("AH", count=2, distinct=2, categories={"Xp": [C1, C2]}),
    ]
    problem = _problem(definitions, [C1, C2])
    best = solve(problem)

    assert best.filled_slots == 2
    assert best.satisfied(problem) == frozenset()

    outcome = _compare(cs_session, definitions, [C1, C2], ref="case-5")
    assert outcome["engine_satisfied"] == set() == outcome["oracle_satisfied"]
    assert _find(outcome["result"], "AH").distinct_categories == 1


def test_case_5b_multi_category_course_still_counts_once(cs_session) -> None:
    """One course certified for both goals cannot satisfy a 2-goal
    requirement, under any objective."""
    definitions = [
        _req("AH", count=2, distinct=2, categories={"Xp": [C1], "Xq": [C1]}),
    ]
    problem = _problem(definitions, [C1])
    best = solve(problem)

    assert best.filled_slots == 1
    assert best.satisfied(problem) == frozenset()


# ==========================================================================
# Case 6 - multi-system sharing
# ==========================================================================


def test_case_6_sharing_removes_the_competition(cs_session) -> None:
    """Under SHARE_ACROSS_SYSTEMS a course serves one slot PER system, so
    there is no tradeoff to optimize - both requirements are satisfied."""
    definitions = [
        _req("MAJ", system="major", count=1, courses=[C1]),
        _req("CORE", system="core", count=1, courses=[C1], order=1),
    ]
    problem = _problem(definitions, [C1], share=True)
    best = solve(problem)

    assert set(best.satisfied(problem)) == {"MAJ", "CORE"}
    assert best.filled_slots == 2

    outcome = _compare(
        cs_session, definitions, [C1], policy="share_across_systems", ref="case-6"
    )
    assert outcome["engine_satisfied"] == {"MAJ", "CORE"}


def test_case_6b_exclusive_keeps_the_competition_and_it_ties(cs_session) -> None:
    definitions = [
        _req("MAJ", system="major", count=1, courses=[C1]),
        _req("CORE", system="core", count=1, courses=[C1], order=1),
    ]
    problem = _problem(definitions, [C1], share=False)
    score, optima = enumerate_optima(problem)

    assert score == (1, 1)
    assert len(optima) == 2          # either requirement, equally optimal


def test_case_6c_unknown_system_participates_normally(cs_session) -> None:
    """`requirement_system` is an open set; a 'minor' is just a third
    partition and the objective is unchanged."""
    definitions = [
        _req("MAJ", system="major", count=1, courses=[C1]),
        _req("MIN", system="minor", count=1, courses=[C1], order=1),
    ]
    problem = _problem(definitions, [C1], share=True)
    best = solve(problem)
    assert set(best.satisfied(problem)) == {"MAJ", "MIN"}


# ==========================================================================
# Case 7 - greedy is worse than optimal
# ==========================================================================


def test_case_7_greedy_ordering_loses_to_the_optimum() -> None:
    """A graph where taking the obvious course first strands a requirement.

    R_SCARCE can only ever be filled by C1. R_POOL accepts C1 or C2. A greedy
    pass in course order puts C1 in R_POOL and strands R_SCARCE; the optimum
    completes both.

    The current allocator already avoids THIS failure - most-constrained-first
    plus augmenting paths - which is worth pinning, because it shows the
    existing heuristics are not the weak point. The dead-end case is.
    """
    definitions = [
        _req("R_POOL", count=1, courses=[C1, C2]),
        _req("R_SCARCE", count=1, courses=[C1], order=1),
    ]
    problem = _problem(definitions, [C1, C2])
    best = solve(problem)

    assert set(best.satisfied(problem)) == {"R_POOL", "R_SCARCE"}

    naive = OracleAllocation(((C1, "R_POOL", ""),))
    assert naive.is_valid(problem)
    assert len(naive.satisfied(problem)) == 1
    assert lexicographic_objective(best, problem) > lexicographic_objective(
        naive, problem
    )


def test_case_7b_engine_matches_the_optimum_here(cs_session) -> None:
    definitions = [
        _req("R_POOL", count=1, courses=[C1, C2]),
        _req("R_SCARCE", count=1, courses=[C1], order=1),
    ]
    outcome = _compare(cs_session, definitions, [C1, C2], ref="case-7b")
    assert outcome["engine_satisfied"] == outcome["oracle_satisfied"]
    assert outcome["engine_satisfied"] == {"R_POOL", "R_SCARCE"}


# ==========================================================================
# Decomposition
# ==========================================================================


def test_disconnected_components_are_split() -> None:
    """Two requirement groups sharing no course are independent instances."""
    problem = _problem(
        [
            _req("A1", count=1, courses=[C1]),
            _req("A2", count=1, courses=[C1], order=1),
            _req("B1", count=1, courses=[C3], order=2),
            _req("B2", count=1, courses=[C4], order=3),
        ],
        [C1, C3, C4],
    )
    parts = connected_components(problem)

    codes = sorted(tuple(sorted(r.code for r in p.requirements)) for p in parts)
    assert codes == [("A1", "A2"), ("B1",), ("B2",)]


def test_component_optima_compose_to_the_global_optimum() -> None:
    """Why decomposition is sound: the objective is a SUM over requirements,
    and sums decompose over independent parts."""
    definitions = [
        _req("A1", count=1, courses=[C1]),
        _req("A2", count=1, courses=[C1], order=1),
        _req("B1", count=1, courses=[C3], order=2),
        _req("B2", count=1, courses=[C4], order=3),
    ]
    problem = _problem(definitions, [C1, C3, C4])

    whole = solve(problem)
    per_part = sum(
        len(solve(part).satisfied(part)) for part in connected_components(problem)
    )
    assert len(whole.satisfied(problem)) == per_part == 3


# ==========================================================================
# Objective comparison on one instance
# ==========================================================================


def test_objectives_disagree_on_the_dead_end_case() -> None:
    """A, B and C side by side on the instance that separates them."""
    definitions = [
        _req("R_BIG", count=2, courses=[C1]),
        _req("R_ONE", count=1, courses=[C1], order=1),
    ]
    problem = _problem(definitions, [C1])

    by_slots = solve(problem, max_slots_objective)
    by_satisfied = solve(problem, max_satisfied_objective)
    by_lex = solve(problem, lexicographic_objective)

    # Every objective fills one slot - that is not what separates them.
    assert by_slots.filled_slots == by_satisfied.filled_slots == 1
    # But only the satisfaction-aware ones complete a requirement.
    assert len(by_satisfied.satisfied(problem)) == 1
    assert len(by_lex.satisfied(problem)) == 1
    assert by_lex.satisfied(problem) == {"R_ONE"}


def test_lexicographic_never_scores_below_max_slots_on_completions() -> None:
    """Sanity property across a family of generated instances."""
    for counts in ((1, 1), (2, 1), (2, 2), (3, 1)):
        definitions = [
            _req("P", count=counts[0], courses=[C1, C2]),
            _req("Q", count=counts[1], courses=[C1, C2, C3], order=1),
        ]
        problem = _problem(definitions, [C1, C2, C3])
        lex = solve(problem, lexicographic_objective)
        slots = solve(problem, max_slots_objective)
        assert len(lex.satisfied(problem)) >= len(slots.satisfied(problem))
        assert lex.filled_slots >= slots.filled_slots - 0
