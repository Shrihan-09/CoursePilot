"""Course-taking eligibility semantics (Phase 6.4) - pure, no database.

Every condition/co-requisite text below is VERBATIM from the archived SOC
(courseNotes / sectionNotes, 2025-09 .. 2027-01). Expectations follow
app.domain.conditions / corequisites / prerequisites; where Rutgers is
silent the answer is UNKNOWN.
"""

from __future__ import annotations

import pytest

from app.domain.attempts import Attempt
from app.domain.conditions import course_codes, interpret
from app.domain.corequisites import parse_note
from app.domain.prerequisites import (
    AllOf,
    AnyOf,
    AttemptState,
    ConcurrentReq,
    CourseReq,
    GradeCondition,
    PrereqStatus,
    Unsupported,
    _combine_all,
    _combine_any,
    evaluate,
    from_json,
    to_json,
)

S, U, K = PrereqStatus.SATISFIED, PrereqStatus.UNSATISFIED, PrereqStatus.UNKNOWN
KNOWN = {("198", "111"), ("198", "112"), ("640", "135"), ("640", "151"), ("220", "102"),
         ("220", "103"), ("940", "102"), ("940", "121"), ("940", "203"), ("119", "116"),
         ("146", "356"), ("640", "111"), ("640", "115"), ("700", "210"), ("700", "381"),
         ("700", "382"), ("700", "384")}
UNIT = {"700": "07"}


def resolve(subject, number):
    return f"{UNIT.get(subject, '01')}:{subject}:{number}" if (subject, number) in KNOWN else None


def took(key, grade="B", term="20259", status="completed", origin="rutgers"):
    return Attempt(key, term, status, grade, origin)


# ==========================================================================
# three-valued logic (Phase 6.2 semantics must survive)
# ==========================================================================


@pytest.mark.parametrize(("a", "b", "and_", "or_"), [
    (S, K, K, S), (U, K, U, K), (S, U, U, S), (K, K, K, K), (S, S, S, S), (U, U, U, U)])
def test_kleene_truth_tables(a, b, and_, or_) -> None:
    assert _combine_all([a, b]) is and_ and _combine_all([b, a]) is and_
    assert _combine_any([a, b]) is or_ and _combine_any([b, a]) is or_


# ==========================================================================
# minimum-grade conditions: reading
# ==========================================================================


def test_cs_note_applies_to_all_prerequisites() -> None:
    out = interpret(["A grade below a 'C' in a prerequisite course will not satisfy prereq"],
                    {"01:198:111"}, resolve)
    assert out["minimum_grade"]["grade"] == "C" and out["minimum_grade"]["scope"] == "all"
    assert out["uninterpreted"] == []


def test_chemistry_note_with_unrelated_prose() -> None:
    note = ("Student needs C or better in all prerequisites. STUDENTS WILL RECEIVE INFORMATION "
            "REGARDING THE ONLINE RECITATIONS VIA EMAIL, SAKAI AND OR THE GENERAL CHEMISTRY "
            "WEBSITE (http://generalchemistry.rutgers.edu/)")
    out = interpret([note], {"01:640:135"}, resolve)
    assert out["minimum_grade"]["scope"] == "all" and out["uninterpreted"] == []


def test_economics_note_names_its_courses_including_shorthand() -> None:
    note = ("220:320 REPLACES 220:203. CREDIT NOT GIVEN FOR BOTH 220:320 AND 220:203. "
            "Prerequisites are grades of C or higher in Intro to Micro 220:102, Intro to Macro "
            "220:103, and Calculus I 640:135 or 151")
    g = interpret([note], {"01:220:102", "01:640:135"}, resolve)["minimum_grade"]
    assert g["scope"] == "named"
    assert g["courses"] == ["01:220:102", "01:220:103", "01:640:135", "01:640:151"]


def test_names_without_codes_leave_the_scope_unspecified() -> None:
    note = "Econometrics Prerequisites are minimum grades of C for 102, 103, Calculus I and Stats"
    assert interpret([note], set(), resolve)["minimum_grade"]["scope"] == "unspecified"


def test_conflicting_grades_are_not_interpreted() -> None:
    out = interpret(["PREREQ 01:198:111 WITH A C OR BETTER. PREREQ 01:198:112 WITH A B OR BETTER"],
                    {"01:198:111", "01:198:112"}, resolve)
    assert out["minimum_grade"] is None and "minimum_grade" in out["uninterpreted"]


def test_advisory_grade_prose_is_not_a_rule() -> None:
    out = interpret(["Students who are having difficulty earning grades of C or better are strongly "
                     "encouraged to see a prerequisite adviser."], set(), resolve)
    assert out["minimum_grade"] is None and out["uninterpreted"]


def test_placement_alternative_requires_an_exact_restatement() -> None:
    same = interpret(["FOR ALL SECTIONS: PREREQ - 940:102 OR 121 OR PLACEMENT TEST"],
                     {"01:940:102", "01:940:121"}, resolve)
    assert same["alternatives"][0]["kinds"] == ["placement"] and same["uninterpreted"] == []
    differs = interpret(["FOR ALL SECTIONS: PREREQ - 940:102 OR PLACEMENT TEST"],
                        {"01:940:102", "01:940:121"}, resolve)
    assert differs["alternatives"] == [] and differs["uninterpreted"]


@pytest.mark.parametrize(("text", "alternatives", "uninterpreted"), [
    # registration logistics after a restatement: dropped (whitelisted)
    ("PRE-REQUISITE: 01:185:201 IF CLOSED CONTACT INSTRUCTOR FOR AN SPN", [], []),
    # "OR EQUIVALENT" is an alternative, never ignored
    ("PREREQ: 01:565:202 OR EQUIVALENT", ["equivalent"], []),
    # a trailing CONDITION is never dropped (the over-parse this guards against)
    ("PREREQ 01:185:201 AND APPROVAL BY RUCCS EXECUTIVE COMMITTEE", [], ["other"]),
    ("PRE-REQUISITE: 01:185:201 IF CLOSED CONTACT A MAJORS ADVISOR", [], ["program_restriction"]),
])
def test_restatement_tails(text, alternatives, uninterpreted) -> None:
    known = {("185", "201"): "01:185:201", ("565", "202"): "01:565:202"}
    out = interpret([text], {"01:185:201"} if "185" in text else {"01:565:202"},
                    lambda s, n: known.get((s, n)))
    assert [k for a in out["alternatives"] for k in a["kinds"]] == alternatives
    assert out["uninterpreted"] == uninterpreted


def test_unknown_conditions_stay_uninterpreted() -> None:
    out = interpret(["PREREQ: AIR FORCE ROTC CADET"], set(), resolve)
    assert out["uninterpreted"] == ["other"]
    out = interpret(["THIS COURSE WILL BE RESTRICTED TO DECLARED MAJORS ... PREREQ - 940:203"],
                    {"01:940:203"}, resolve)
    assert "program_restriction" in out["uninterpreted"]


def test_shorthand_never_guesses_a_unit() -> None:
    codes, complete = course_codes("PREREQ: 999:101 OR 198:111", resolve)
    assert codes == ["01:198:111"] and complete is False


# ==========================================================================
# minimum-grade conditions: evaluation
# ==========================================================================

EXPR = AnyOf((CourseReq("01:220:102"), CourseReq("11:373:121")))


def _eval(history, condition, expr=EXPR):
    return evaluate(expr, history, grade_condition=condition)


def test_strict_grade_condition() -> None:
    cond = GradeCondition("C", "all")
    assert _eval({"01:220:102": [took("01:220:102", "B")]}, cond).status is S
    assert _eval({"01:220:102": [took("01:220:102", "C")]}, cond).status is S        # boundary
    ev = _eval({"01:220:102": [took("01:220:102", "D")]}, cond)
    assert ev.status is U
    expected = {"course": "01:220:102", "required_grade": "C", "earned_grade": "D",
                "result": "unsatisfied"}
    assert expected.items() <= ev.grade_checks[0].items()
    assert _eval({"01:220:102": [took("01:220:102", None, status="in_progress")]},
                 cond).status is K
    assert _eval({"01:220:102": [took("01:220:102", None, origin="transfer")]}, cond).status is K


def test_named_scope_is_strict_for_named_and_ambiguous_for_others() -> None:
    cond = GradeCondition("C", "named", frozenset({"01:220:102"}))
    assert _eval({"11:373:121": [took("11:373:121", "B")]}, cond).status is S   # meets C anyway
    ev = _eval({"11:373:121": [took("11:373:121", "D")]}, cond)
    assert ev.status is K and "grade_scope_ambiguous:11:373:121" in ev.unknown_reasons
    assert _eval({"01:220:102": [took("01:220:102", "D")]}, cond).status is U


def test_unspecified_scope_never_fails_a_passing_grade() -> None:
    cond = GradeCondition("C", "unspecified")
    assert _eval({"01:220:102": [took("01:220:102", "D")]}, cond).status is K
    assert _eval({"01:220:102": [took("01:220:102", "F")]}, cond).status is U


def test_grade_conditions_compose_with_nested_logic() -> None:
    expr = AllOf((CourseReq("01:198:111"), AnyOf((CourseReq("01:640:151"), Unsupported("x", "x")))))
    cond = GradeCondition("C", "all")
    history = {"01:198:111": [took("01:198:111", "A")], "01:640:151": [took("01:640:151", "B")]}
    assert evaluate(expr, history, grade_condition=cond).status is S
    history["01:198:111"] = [took("01:198:111", "D")]
    assert evaluate(expr, history, grade_condition=cond).status is U    # UNSAT AND ... = UNSAT
    history["01:198:111"] = [took("01:198:111", "A")]
    history["01:640:151"] = [took("01:640:151", "D")]
    assert evaluate(expr, history, grade_condition=cond).status is K    # SAT AND (UNSAT OR UNK)


def test_retakes_for_prerequisites() -> None:
    cond = GradeCondition("C", "all")
    assert _eval({"01:220:102": [took("01:220:102", "F"), took("01:220:102", "B", "20261")]},
                 cond).status is S                                     # fail -> pass
    assert _eval({"01:220:102": [took("01:220:102", "C"), took("01:220:102", "D", "20261")]},
                 cond).status is S                                     # qualifying -> later low
    assert _eval({"01:220:102": [took("01:220:102", "D"),
                                 took("01:220:102", None, "20269", "in_progress")]},
                 cond).status is K                                     # low -> in progress


def test_legacy_histories_without_grades_cannot_meet_a_grade_condition() -> None:
    cond = GradeCondition("C", "all")
    ev = _eval({"01:220:102": AttemptState.PASSED}, cond)
    assert ev.status is K and "no_grade_information:01:220:102" in ev.unknown_reasons
    assert _eval({"01:220:102": AttemptState.PASSED}, None).status is S       # Phase 6.2 unchanged


def test_uninterpreted_conditions_still_cap() -> None:
    ev = evaluate(CourseReq("01:220:102"), {"01:220:102": [took("01:220:102", "A")]},
                  unmodeled_conditions=("permission",))
    assert ev.status is K and ev.uncapped_status is S


# ==========================================================================
# co-requisites
# ==========================================================================


def _coreq(text):
    parses = parse_note(text, resolve)
    assert len(parses) == 1
    return parses[0]


def test_coreq_forms() -> None:
    assert _coreq("PRE OR COREQ: 01:146:356").kind == "pre_or_co"
    assert _coreq("PRE/CO-REQ: 01:146:356").kind == "pre_or_co"
    russian = _coreq("FOR ALL SECTIONS: PREREQ OR COREQ - 940:203 OR PERM. OF DEPT. "
                     "NOT OPEN TO NATIVE SPEAKERS OR OTHERS ALREADY CONVERSANT IN LANGUAGE")
    assert russian.classification == "parsed"
    assert russian.canonical_text == ("(CONCURRENT[01:940:203: before or same term] or "
                                      "UNSUPPORTED[permission])")
    co = _coreq("COREQ: 640:111 OR 115; CHE SCHEDULE GO TO SCHEDULING .RUTGERS.EDU")
    assert co.kind == "co" and co.courses == ("01:640:111", "01:640:115")
    only = _coreq("THIS COURSE MUST BE TAKEN CONCURRENTLY WITH ONE OF THE FOLLOWING COURSES: "
                  "07:700:210, 381, 382, OR 384")
    assert only.kind == "concurrent_only" and len(only.courses) == 4


@pytest.mark.parametrize("text", [
    "COREQ: 01:640:112 OR HIGHER",
    "PRE OR CO-REQUISITE: COURSE FROM 198, 615, 730, 830 OR INSTRUCTOR PERMISSION.",
    "ADDITIONAL PRE- OR CO-REQ: 01:37 7:370",
    "COREQUISITES 01:640:151 OR HIGHER AND 01:750:275.",
])
def test_unclear_coreqs_are_unsupported_not_guessed(text) -> None:
    assert all(p.classification == "unsupported" and p.expression is None
               for p in parse_note(text, resolve))


def test_advice_is_not_a_rule() -> None:
    assert parse_note("RECOMMENDED AS A COREQUISITE FOR INTERNSHIP.", resolve) == []
    assert parse_note("MAY TAKE 565:103 CONCURRENTLY", resolve) == []


def _concurrent(prior, history, proposed=frozenset(), term="20269"):
    return evaluate(ConcurrentReq("01:146:356", prior), history, term_code=term,
                    proposed=frozenset(proposed)).status


def test_corequisite_evaluation() -> None:
    earlier = {"01:146:356": [took("01:146:356", "B", "20261")]}
    assert _concurrent(True, {}, {"01:146:356"}) is S            # same proposed term
    assert _concurrent(True, {}) is U                            # missing
    assert _concurrent(True, earlier) is S                       # completed before
    assert _concurrent(None, earlier) is K                       # "COREQ": earlier unspecified
    assert _concurrent(False, earlier) is U                      # "must be taken concurrently"
    enrolled = {"01:146:356": [took("01:146:356", None, "20269", "in_progress")]}
    assert _concurrent(False, enrolled) is S                     # registered in the same term
    pending = {"01:146:356": [took("01:146:356", None, "20261", "in_progress")]}
    assert _concurrent(True, pending) is K                       # earlier, outcome unknown


def test_corequisite_term_scoping() -> None:
    later = {"01:146:356": [took("01:146:356", "A", "20271")]}
    assert _concurrent(True, later, term="20269") is U   # a LATER completion is not "before"


def test_corequisite_with_unknown_alternative() -> None:
    expr = AnyOf((ConcurrentReq("01:940:203", True), Unsupported("permission", "perm")))
    assert evaluate(expr, {}, term_code="20269").status is K          # perm could apply
    assert evaluate(expr, {}, term_code="20269",
                    proposed=frozenset({"01:940:203"})).status is S


def test_concurrent_leaf_round_trips_through_json() -> None:
    expr = AnyOf((ConcurrentReq("01:146:356", True), ConcurrentReq("01:146:357", None)))
    assert from_json(to_json(expr)) == expr
