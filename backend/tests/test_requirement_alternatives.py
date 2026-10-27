"""Generic one-of commitments, independent of any major or course subject."""
from decimal import Decimal
from types import SimpleNamespace

from app.domain.audit import RequirementResult, RequirementStatus
from app.domain.conditions import interpret
from app.domain.prerequisites import PrereqStatus
from app.services.course_eligibility import _corequisite
from app.services.planning.engine import PlanningEngine, active_leaves


def node(code, kind="course", children=(), **kw):
    return RequirementResult(requirement_code=code, requirement_name=code,
        requirement_type=kind, status=RequirementStatus.UNSATISFIED,
        needed_count=1, reason="test", children=list(children), **kw)


def selector(costs, started=(), *, share=False):
    engine = PlanningEngine.__new__(PlanningEngine)
    engine.alternative_choices = {}
    engine.constraints = SimpleNamespace(alternative_selections={})
    engine._choice_metadata = {}
    engine._branch_benefit = {}
    engine._record_progress = {c: Decimal(1) for c in started}
    engine.requirements = {c: SimpleNamespace(sort_order=i) for i, c in enumerate(costs)}
    engine.pools = {}
    engine._usable = lambda codes: len(codes) if share else 1
    engine._alternative_terms = lambda child, *args: costs[child.requirement_code]
    root = node("CHOICE", "any_of", [node(c) for c in costs])
    audit = SimpleNamespace(requirements=[root])
    return engine, audit


def choose(engine, audit):
    engine._choose_alternatives(audit, None, None, [], {})
    return [n.requirement_code for n in engine._active(audit.requirements)]


def test_zero_progress_opens_only_one_alternative():
    root = node("CHOICE", "any_of", [node("A"), node("B")])
    assert len(active_leaves([root])) == 1
    engine, audit = selector({"A": 4, "B": 2})
    assert choose(engine, audit) == ["B"]


def test_record_progress_precedes_completion_time_and_is_retained():
    engine, audit = selector({"A": 1, "B": 4}, started=["B"])
    assert choose(engine, audit) == ["B"]
    engine._record_progress = {}
    assert choose(engine, audit) == ["B"]


def test_explicit_student_choice_overrides_provisional_recommendation():
    engine, audit = selector({"A": 1, "B": 4})
    engine.constraints.alternative_selections = {"CHOICE": ["B"]}
    assert choose(engine, audit) == ["B"]
    metadata = engine._choice_metadata["CHOICE"]
    assert metadata["selected"] == ["B"]
    assert metadata["status"] == "student_selected"
    # The planner's own recommendation is still shown, with its reason.
    assert [c["code"] for c in metadata["recommended"]] == ["A"]
    assert "soonest" in metadata["recommendation_reason"]


def test_impossible_commitment_falls_back_in_catalog_order():
    costs = {"Z": 2, "B": 2, "A": 2}
    engine, audit = selector(costs, started=["Z"])
    assert choose(engine, audit) == ["Z"]
    costs["Z"] = None
    assert choose(engine, audit) == ["B"]
    assert engine._choice_metadata["CHOICE"]["fallback_from"] == ["Z"]


def test_two_of_preserves_completed_child_and_opens_only_one_more():
    engine, audit = selector({"A": 0, "B": 2, "C": 3})
    root = audit.requirements[0]
    root.needed_count = 2
    root.children[0].status = RequirementStatus.SATISFIED
    root.children[0].satisfied_count = 1
    assert choose(engine, audit) == ["B"]
    assert engine.alternative_choices["CHOICE"] == ["A", "B"]


def test_sharing_benefit_only_breaks_equal_time_ties_when_allowed():
    engine, audit = selector({"A": 2, "B": 2}, share=True)
    engine.pools = {"A": {"x"}, "B": {"y"}, "OTHER": {"y"}}
    engine._branch_benefit = {"A": Decimal(0), "B": Decimal(1)}
    assert choose(engine, audit) == ["B"]
    engine, audit = selector({"A": 2, "B": 2})
    engine.pools = {"A": {"x"}, "B": {"y"}, "OTHER": {"y"}}
    assert choose(engine, audit) == ["A"]


def test_plain_prerequisite_note_is_enforced_as_an_additional_or_list():
    parsed = interpret(["Prerequisite 01:160:159 or 01:160:161 or 01:160:163"],
                       {"01:160:159", "01:160:161", "01:160:163", "01:160:165"},
                       lambda subject, number: None)
    assert parsed["prerequisite_lists"] == [["01:160:159", "01:160:161", "01:160:163"]]
    assert parsed["uninterpreted"] == []
    from app.domain.attempts import Attempt
    from app.services.prerequisites import _check
    row = SimpleNamespace(raw_text="OR-list", condition_note="plain prerequisite list",
        section_condition_note=None, expression={"any_of": [{"course": k} for k in
            ("01:160:159", "01:160:161", "01:160:163", "01:160:165")]},
        interpreted_conditions=parsed, classification="parsed", canonical_text="OR-list")
    for key, expected in (("01:160:161", PrereqStatus.SATISFIED),
                          ("01:160:165", PrereqStatus.UNSATISFIED)):
        history = {key: [Attempt(key, "20269", "completed", "B")]}
        assert _check("01:160:171", "20271", True, [row], history).status is expected


def test_unsupported_corequisite_is_unknown_even_with_proposed_partners():
    row = SimpleNamespace(raw_text="COREQ: 01:640:112 OR EQUIVALENT", expression=None,
                          source_field="courseNotes", classification="unsupported",
                          canonical_text=None, parse_detail="equivalence unspecified")
    result = _corequisite([row], {}, "20271", frozenset({"01:640:112"}), set())
    assert result.status is PrereqStatus.UNKNOWN
    from app.domain.planning import EligibilityState
    assert PlanningEngine._state(result, result) is EligibilityState.NEEDS_CONFIRMATION


def test_generic_selector_has_no_major_specific_branching():
    import inspect
    source = inspect.getsource(PlanningEngine._choose_alternatives)
    source += inspect.getsource(PlanningEngine._alternative_terms)
    assert not any(token in source for token in ("sas-198", "CS_BS", "01:160", "01:750"))


def test_api_contract_accepts_explicit_choices_and_exposes_typed_alternatives():
    from app.api.v1.routes.plans import PlanRequest
    from app.domain.planning import PlanResult
    request = PlanRequest.model_validate({"start_term": "20271", "constraints": {
        "alternative_selections": {"SCIENCE": ["CHEMISTRY"]}}})
    assert request.constraints.alternative_selections == {"SCIENCE": ["CHEMISTRY"]}
    schema = PlanResult.model_json_schema()["$defs"]
    assert "selected" in schema["AlternativeChoice"]["properties"]
    assert "alternatives" in schema["AlternativeChoice"]["properties"]


def test_nested_choices_do_not_reopen_an_unselected_parent():
    x, y, z = node("X"), node("Y"), node("Z")
    left = node("LEFT", "any_of", [x, y])
    right = node("RIGHT", "any_of", [z])
    root = node("OUTER", "any_of", [left, right])
    engine = PlanningEngine.__new__(PlanningEngine)
    engine.alternative_choices = {"OUTER": ["LEFT"], "LEFT": ["X"], "RIGHT": ["Z"]}
    parents = {"OUTER": None, "LEFT": "OUTER", "RIGHT": "OUTER",
               "X": "LEFT", "Y": "LEFT", "Z": "RIGHT"}
    engine.by_id_code = {c: c for c in parents}
    engine.requirements = {c: SimpleNamespace(code=c, parent_id=p) for c, p in parents.items()}
    engine.pools = {"X": {"x"}, "Y": {"y"}, "Z": {"z"}}
    assert engine._competing_courses([root]) == {"y", "z"}


def test_an_impossible_student_choice_is_held_as_needs_reselection_never_switched():
    engine, audit = selector({"A": 1, "B": None})          # B cannot be completed
    engine.constraints.alternative_selections = {"CHOICE": ["B"]}
    assert choose(engine, audit) == ["B"]                     # held, never A
    metadata = engine._choice_metadata["CHOICE"]
    assert metadata["status"] == "needs_reselection"
    assert metadata["unavailable"] == ["B"]
    assert [c["code"] for c in metadata["recommended"]] == ["A"]
    assert engine.alternative_choices["CHOICE"] == ["B"]


def test_no_feasible_alternative_and_no_choice_is_undecided_with_one_branch_open():
    engine, audit = selector({"A": None, "B": None})
    assert len(choose(engine, audit)) == 1                    # never both
    metadata = engine._choice_metadata["CHOICE"]
    assert metadata["status"] == "undecided" and metadata["recommended"] == []
    assert "No alternative can be completed" in metadata["recommendation_reason"]


def test_recommendation_reasons_name_the_deciding_criterion():
    engine, audit = selector({"A": 4, "B": 2}, started=["A"])
    choose(engine, audit)
    meta = engine._choice_metadata["CHOICE"]
    assert meta["status"] == "planner_recommended"
    assert "already started" in meta["recommendation_reason"]
    engine, audit = selector({"A": 2, "B": 2})
    choose(engine, audit)
    assert "catalog's order" in engine._choice_metadata["CHOICE"]["recommendation_reason"]


def test_needs_reselection_issue_says_the_choice_is_kept():
    from app.domain.planning import AlternativeChoice, Severity
    from app.services.planning.engine import _needs_reselection
    issue = _needs_reselection(AlternativeChoice(
        requirement_code="CHOICE", status="needs_reselection",
        chosen=[{"code": "B", "name": "Branch B"}], alternatives=[], selected=["B"],
        unavailable=["B"], recommended=[{"code": "A", "name": "Branch A"}]))
    assert issue.code == "ALTERNATIVE_NEEDS_RESELECTION"
    assert issue.severity is Severity.NEEDS_CONFIRMATION
    assert "Branch B" in issue.message and "does not switch" in issue.message


def test_an_unparseable_prerequisite_is_unknown_never_satisfied():
    """A published prerequisite CoursePilot cannot interpret is UNKNOWN, even
    when the student has every course it mentions (SYNTHETIC course codes)."""
    from app.domain.attempts import Attempt
    from app.services.prerequisites import _check
    row = SimpleNamespace(raw_text="01:999:101 OR PERMISSION OF THE DEPARTMENT",
                          condition_note=None, section_condition_note=None, expression=None,
                          interpreted_conditions=None, classification="unsupported",
                          canonical_text=None)
    history = {"01:999:101": [Attempt("01:999:101", "20269", "completed", "A")]}
    assert _check("01:999:201", "20271", True, [row], history).status is PrereqStatus.UNKNOWN
