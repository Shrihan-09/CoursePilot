"""Prerequisite IR, parser, canonical form and three-valued evaluation (Phase 6.2).

Pure - no database. Parser inputs are REAL Rutgers SOC strings (from the
archived payloads), exactly as published, markup included.
"""

from __future__ import annotations

import json
import pathlib
import random

import pytest

from app.domain.prerequisites import (
    AllOf,
    AnyOf,
    AtLeast,
    AttemptState,
    Classification,
    CourseReq,
    PrereqStatus,
    Unsupported,
    canonicalize,
    condition_kinds,
    course_keys,
    evaluate,
    from_json,
    parse,
    to_json,
    to_text,
)

A, B, C, D = (CourseReq(k) for k in ("01:198:111", "01:640:151", "01:640:152", "14:332:221"))
P, IP, NP = AttemptState.PASSED, AttemptState.IN_PROGRESS, AttemptState.NOT_PASSED
SAT, UNSAT, UNK = PrereqStatus.SATISFIED, PrereqStatus.UNSATISFIED, PrereqStatus.UNKNOWN
ARCHIVES = pathlib.Path(__file__).resolve().parents[2] / "data" / "raw"


# ==========================================================================
# parser - real Rutgers strings
# ==========================================================================


def test_a_single_course() -> None:
    r = parse("(01:013:156 HEBREW REVIEW&CONTIN )")
    assert r.classification is Classification.PARSED
    assert r.expression == CourseReq("01:013:156")


def test_em_wrapped_or() -> None:
    r = parse("(01:013:140 ELEMENTARY ARABIC I )<em> OR </em>(01:074:140 ELEMENTARY ARABIC I )")
    assert r.expression == AnyOf((CourseReq("01:013:140"), CourseReq("01:074:140")))


def test_lower_case_and_inside_a_group() -> None:
    r = parse("((01:119:116 GENERAL BIOLOGY II  and 01:119:117 BIOLOGICAL RESEARCH LABORATORY )"
              " or (01:119:102 GENERAL BIOLOGY ))")
    # Canonical order sorts children by canonical text, so a group "(..."
    # precedes a bare course; compare against the canonical form.
    assert r.expression == canonicalize(AnyOf((
        CourseReq("01:119:102"),
        AllOf((CourseReq("01:119:116"), CourseReq("01:119:117"))),
    )))


def test_nested_and_of_ors() -> None:
    r = parse("((01:160:160 GEN CHEM FOR ENGRS  or 01:160:162 GENERAL CHEMISTRY  or "
              "01:160:164 HONORS GENERAL CHEM ) and (01:160:171 INTRODUCTION TO EXPERIMENTATION ))")
    assert to_text(r.expression) == (
        "((01:160:160 or 01:160:162 or 01:160:164) and 01:160:171)")


def test_titles_containing_and_or_are_not_operators() -> None:
    """Upper-case AND in a title is title text: operators are lower case or <em>."""
    r = parse("(01:640:135 CALCULUS I FOR THE LIFE AND SOCIAL SCIENCES )")
    assert r.expression == CourseReq("01:640:135")


def test_titles_containing_parentheses() -> None:
    assert parse("(01:377:160 INTRODUCTION TO PHYSICAL THERAPY (PT) )").expression == \
        CourseReq("01:377:160")
    r = parse("(19:913:598 CLINICAL SOCIAL WORK (CSW) SPECIALIZATION )<em> OR </em>"
              "(19:913:599 MANAGEMENT & POLICY (MAP) SPECIALIZATION )")
    assert r.expression == AnyOf((CourseReq("19:913:598"), CourseReq("19:913:599")))


def test_whitespace_and_operator_case_do_not_change_the_tree() -> None:
    tight = parse("(01:013:140 X)<em>OR</em>(01:074:140 Y)")
    loose = parse("  (  01:013:140  X   )  <em>  or  </em>  ( 01:074:140 Y )  ")
    assert tight.expression == loose.expression
    assert tight.canonical_text == loose.canonical_text


def test_any_n_of_the_following() -> None:
    r = parse("Any Two Course from the following: (01:615:305 SYNTAX )    "
              "(01:615:315 PHONOLOGY )    (01:615:325 SEMANTICS )    ")
    assert r.classification is Classification.PARSED
    assert isinstance(r.expression, AtLeast) and r.expression.n == 2
    assert course_keys(r.expression) == ["01:615:305", "01:615:315", "01:615:325"]


def test_minimum_course_level_is_unsupported_but_keeps_its_reference() -> None:
    r = parse("Any Course EQUAL or GREATER Than: (01:640:112 PRECALCULUS PART II )")
    assert r.classification is Classification.UNSUPPORTED_MINIMUM_COURSE_LEVEL
    assert isinstance(r.expression, Unsupported)
    assert r.references == ("01:640:112",)


def test_mixed_and_or_without_grouping_is_not_guessed() -> None:
    r = parse("(01:198:111 X  or 01:640:151 Y  and 01:640:152 Z )")
    assert r.classification is Classification.UNSUPPORTED_AMBIGUOUS_PRECEDENCE
    assert isinstance(r.expression, Unsupported)


@pytest.mark.parametrize("raw", [
    "(01:198:111 X",                       # unbalanced
    "(01:198:111 X ) <em> OR </em>",       # dangling operator
    "(01:198:111 X ) (01:640:151 Y )",     # adjacent terms, no operator
    "(01:198:111 X <b>bold</b> )",         # unexpected markup
])
def test_malformed_input_is_classified_not_repaired(raw) -> None:
    assert parse(raw).classification is Classification.MALFORMED


def test_prose_without_courses_is_unknown() -> None:
    r = parse("TWO Course Within the Subject Area:")
    assert r.classification is Classification.UNKNOWN and r.expression is None


def test_no_text_is_no_prerequisite() -> None:
    assert parse(None) is None and parse("   ") is None


# ==========================================================================
# canonical form
# ==========================================================================


def test_canonical_form_ignores_order_duplicates_and_nesting() -> None:
    assert canonicalize(AnyOf((B, A, B))) == canonicalize(AnyOf((A, B)))
    assert canonicalize(AllOf((A, AllOf((B, C))))) == AllOf((A, B, C))
    assert canonicalize(AnyOf((A,))) == A


def test_canonical_text_round_trips_through_the_parser() -> None:
    for expr in (A, AllOf((A, B)), AnyOf((A, D)), AllOf((A, AnyOf((B, C)))),
                 AnyOf((AllOf((A, B)), AllOf((C, D)))), AtLeast(2, (A, B, C))):
        canon = canonicalize(expr)
        assert parse(to_text(canon)).expression == canon


def test_json_round_trip() -> None:
    expr = canonicalize(AllOf((A, AnyOf((B, Unsupported("x", "t", ("01:640:112",)))))))
    assert from_json(json.loads(json.dumps(to_json(expr)))) == expr


def test_every_archived_string_round_trips() -> None:
    """Property over real data: parse -> canonical text -> parse is stable."""
    files = sorted(ARCHIVES.glob("soc_courses_*.json"))
    if not files:
        pytest.skip("SOC archives not present")
    strings = {c["preReqNotes"] for f in files for c in json.loads(f.read_bytes())
               if (c.get("preReqNotes") or "").strip()}
    parsed = [parse(s) for s in strings]
    ok = [r for r in parsed if r.classification.is_parsed]
    assert len(ok) >= 0.95 * len(strings)
    for r in ok:
        assert parse(r.canonical_text).expression == r.expression


def test_shuffled_operands_canonicalize_identically() -> None:
    rng = random.Random(6_2)
    kids = [A, B, C, D, AllOf((A, B))]
    for _ in range(50):
        rng.shuffle(kids)
        assert to_text(canonicalize(AnyOf(tuple(kids)))) == to_text(canonicalize(AnyOf((A, B, C, D, AllOf((A, B))))))


# ==========================================================================
# three-valued evaluation
# ==========================================================================


def _status(expr, **history):
    return evaluate(expr, {k: v for k, v in history.items()}).status


def test_course_leaf() -> None:
    assert evaluate(A, {A.course_key: P}).status is SAT
    assert evaluate(A, {}).status is UNSAT
    assert evaluate(A, {A.course_key: IP}).status is UNK


def test_and_or_basic() -> None:
    both = {A.course_key: P, B.course_key: P}
    one = {A.course_key: P}
    assert evaluate(AllOf((A, B)), both).status is SAT
    assert evaluate(AllOf((A, B)), one).status is UNSAT           # partially satisfied AND
    assert evaluate(AnyOf((A, B)), {B.course_key: P}).status is SAT
    assert evaluate(AnyOf((A, B)), {}).status is UNSAT


def test_kleene_combinations() -> None:
    unknown = Unsupported("placement", "t")
    passed_a = {A.course_key: P}
    assert evaluate(AnyOf((A, unknown)), passed_a).status is SAT    # SATISFIED OR UNKNOWN
    assert evaluate(AllOf((A, unknown)), {}).status is UNSAT        # UNSATISFIED AND UNKNOWN
    assert evaluate(AllOf((A, unknown)), passed_a).status is UNK    # SATISFIED AND UNKNOWN
    assert evaluate(AnyOf((A, unknown)), {}).status is UNK          # UNSATISFIED OR UNKNOWN


def test_nested_expression() -> None:
    expr = AllOf((A, AnyOf((B, C))))
    assert evaluate(expr, {A.course_key: P, C.course_key: P}).status is SAT
    assert evaluate(expr, {A.course_key: P}).status is UNSAT
    assert evaluate(expr, {A.course_key: P, B.course_key: IP}).status is UNK


def test_at_least() -> None:
    expr = AtLeast(2, (A, B, C))
    assert evaluate(expr, {A.course_key: P, B.course_key: P}).status is SAT
    assert evaluate(expr, {A.course_key: P}).status is UNSAT
    assert evaluate(expr, {A.course_key: P, B.course_key: IP}).status is UNK


def test_unsupported_is_never_satisfied() -> None:
    r = parse("Any Course EQUAL or GREATER Than: (01:640:112 PRECALCULUS PART II )")
    ev = evaluate(r.expression, {"01:640:112": P})
    assert ev.status is UNK
    assert "unsupported:minimum_course_level" in ev.unknown_reasons


def test_an_unmodeled_condition_caps_satisfied_at_unknown() -> None:
    """A published grade note is not interpreted - so a met expression is not proof."""
    ev = evaluate(A, {A.course_key: P}, unmodeled_conditions=("minimum_grade",))
    assert ev.status is UNK
    assert "unmodeled_condition:minimum_grade" in ev.unknown_reasons
    # ...but it can never rescue an unmet one.
    assert evaluate(A, {}, unmodeled_conditions=("minimum_grade",)).status is UNSAT


def test_evidence_lists_leaves() -> None:
    ev = evaluate(AllOf((A, AnyOf((B, C)), D)), {A.course_key: P, B.course_key: IP})
    assert ev.status is UNSAT
    assert ev.required_courses == ["01:198:111", "01:640:151", "01:640:152", "14:332:221"]
    assert ev.satisfied_courses == ["01:198:111"]
    assert ev.pending_courses == ["01:640:151"]
    assert set(ev.missing_courses) == {"01:640:152", "14:332:221"}


def test_a_referenced_course_never_loaded_is_simply_not_passed() -> None:
    """No Course row is needed to evaluate: the identity is the key."""
    assert evaluate(CourseReq("99:999:999"), {}).status is UNSAT


# ==========================================================================
# conditions published outside the expression
# ==========================================================================


@pytest.mark.parametrize("note, kinds", [
    ("Student needs C or better in all prerequisites.", ("minimum_grade",)),
    ("A grade below a 'C' in a prerequisite course will not satisfy prereq", ("minimum_grade",)),
    ("Prerequisites are grades of C or higher in Intro to Micro", ("minimum_grade",)),
    ("Writing prerequisite of (01:355:101 ...) or placement into 355:101", ("placement",)),
    ("FOR ALL SECTIONS: PREREQ OR COREQ - 940:203 OR PERM. OF DEPT.",
     ("permission", "corequisite")),
    ("Go to http://canvas.rutgers.edu", ()),
    (None, ()),
])
def test_condition_notes_are_detected_from_real_wording(note, kinds) -> None:
    assert condition_kinds(note) == kinds
