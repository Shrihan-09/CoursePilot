"""Phase 7.4 Degree Navigator status rules, freshness, mapping tables and failure injection.

No database: the overlay works on Degree Engine results and DN rows as data;
the mapping tests read the committed tables; the injection tests damage the
SYNTHETIC audit layout and parse it.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.domain.audit import DegreeAuditResult, RequirementResult, RequirementStatus
from app.domain.degree_audit import parse_audit
from app.services.degree_audit.mapping import check_track, load_mappings
from app.services.degree_audit.overlay import (
    DISAGREE_LABEL,
    DnOverlay,
    NodeStatus,
    apply_overlay,
    freshness,
    planning_status,
)
from app.services.degree_audit.pdf_worker import extract_audit_layout

from .support.synthetic_dn import audit_layout, render_pdf, sas_core_model

ROOT = pathlib.Path(__file__).resolve().parents[2]
PROGRAMS = ROOT / "data" / "programs" / "rutgers" / "nb-undergrad"


def _row(status, courses):
    return SimpleNamespace(dn_status=status, extracted_courses=courses)


# --------------------------------------------------------------------------
# decision 2: "In Progress or Planned" is split deterministically
# --------------------------------------------------------------------------


def test_in_progress_when_every_course_is_in_the_confirmed_current_schedule():
    row = _row("in_progress_or_planned",
               [{"course": "01:999:102", "term_code": "20269", "kind": "current"},
                {"course": "01:999:101", "term_code": "20261", "kind": "course"}])
    status, rule = planning_status(row, {("20269", "01:999:102")})
    assert status == "in_progress" and "confirmed current schedule" in rule


def test_planned_when_a_current_course_is_not_in_the_confirmed_schedule():
    row = _row("in_progress_or_planned",
               [{"course": "01:999:102", "term_code": "20269", "kind": "current"}])
    status, rule = planning_status(row, set())
    assert status == "planned" and "01:999:102 is not in your confirmed current schedule" in rule


def test_planned_when_a_course_behind_it_is_only_planned():
    row = _row("in_progress_or_planned",
               [{"course": "01:999:102", "term_code": "20269", "kind": "current"},
                {"course": "01:999:103", "term_code": None, "icon": "planned", "kind": None}])
    status, rule = planning_status(row, {("20269", "01:999:102")})
    assert status == "planned" and "only planned" in rule


def test_fulfilled_and_not_fulfilled_map_directly():
    assert planning_status(_row("satisfied", []), set())[0] == "satisfied"
    assert planning_status(_row("not_satisfied", []), set())[0] == "not_satisfied"
    assert planning_status(_row("unknown", []), set())[0] == "unknown"


# --------------------------------------------------------------------------
# the overlay lifts mapped nodes and re-derives their groups; rules untouched
# --------------------------------------------------------------------------


def _node(code, status, rtype="course", children=()):
    return RequirementResult(requirement_code=code, requirement_name=code, requirement_type=rtype,
                             status=status, reason="CoursePilot.", children=list(children))


def _result(*roots):
    return DegreeAuditResult(program_name="P", program_code="P", degree_type="BA",
                             catalog_year="2026-2027", status="incomplete",
                             requirements=list(roots))


def _status(code, planning, reconciliation="dn_satisfied_cp_not"):
    return NodeStatus(code, "k", planning, "satisfied", "Degree Navigator shows it fulfilled.",
                      "i", datetime(2026, 10, 20, tzinfo=UTC), reconciliation, "reviewed", code)


def test_a_dn_satisfied_leaf_lifts_its_all_of_parent():
    tree = _node("ROOT", RequirementStatus.PARTIALLY_SATISFIED, "all_of", [
        _node("A", RequirementStatus.SATISFIED), _node("B", RequirementStatus.UNSATISFIED)])
    out = apply_overlay(_result(tree), DnOverlay({"B": _status("B", "satisfied")}))
    root = out.requirements[0]
    assert root.children[1].status is RequirementStatus.SATISFIED
    assert DISAGREE_LABEL in root.children[1].reason
    assert root.status is RequirementStatus.SATISFIED


def test_in_progress_lifts_only_to_provisional_and_never_lowers():
    tree = _node("ROOT", RequirementStatus.PARTIALLY_SATISFIED, "all_of", [
        _node("A", RequirementStatus.SATISFIED), _node("B", RequirementStatus.UNSATISFIED)])
    out = apply_overlay(_result(tree), DnOverlay({
        "A": _status("A", "not_satisfied", "cp_satisfied_dn_not"),
        "B": _status("B", "in_progress")}))
    a, b = out.requirements[0].children
    assert a.status is RequirementStatus.SATISFIED            # DN "not" never lowers
    assert DISAGREE_LABEL in a.reason                         # ...but is stated
    assert b.status is RequirementStatus.PROVISIONALLY_SATISFIED
    assert out.requirements[0].status is RequirementStatus.PROVISIONALLY_SATISFIED


# --------------------------------------------------------------------------
# decision 6: freshness
# --------------------------------------------------------------------------


def _imp(as_of, source="print_header"):
    return SimpleNamespace(as_of=as_of, as_of_source=source)


def test_an_audit_older_than_the_limit_is_stale():
    now = datetime(2026, 12, 1, tzinfo=UTC)
    f = freshness(_imp(now - timedelta(days=45)), now)
    assert f.stale and "older_than_limit" in f.reasons and f.age_days == 45


def test_an_audit_printed_before_final_grades_is_stale():
    # Spring 2026 final grades: grading period ends 2026-05-15 (Registrar calendar).
    now = datetime(2026, 5, 20, tzinfo=UTC)
    f = freshness(_imp(datetime(2026, 5, 10, tzinfo=UTC)), now)
    assert f.stale and f.reasons == ["predates_final_grades"]
    assert "20261" in f.message


def test_a_recent_audit_after_grades_is_fresh():
    now = datetime(2026, 10, 24, tzinfo=UTC)
    f = freshness(_imp(datetime(2026, 10, 22, tzinfo=UTC)), now)
    assert not f.stale and "printed Oct 22, 2026" in f.message


# --------------------------------------------------------------------------
# the committed mapping tables (decisions 1 and 7)
# --------------------------------------------------------------------------


def _core_codes():
    core = json.loads((PROGRAMS / "2026-2027" / "sas-core.json").read_text(encoding="utf-8"))
    nodes = {r["code"]: r for r in core["requirements"]}
    return nodes, {g["code"] for g in core["goals"]}


def test_sas_core_mapping_points_only_at_real_nodes_and_codes():
    [core] = [m for m in load_mappings("2026-2027", PROGRAMS) if m.program_key == "sas-core"]
    nodes, goals = _core_codes()
    assert core.dn_subject == "The SAS Core Curriculum (NB)"
    assert len(core.requirements) == 6
    for entry in [*core.requirements.values(), *core.goals.values()]:
        assert entry.requirement_code is None or entry.requirement_code in nodes
    assert set(core.goals) <= goals
    # 14 goal codes appear in a real SAS Core DN audit (not 13); every one is listed.
    assert len(core.goals) == 14
    for code, entry in core.goals.items():
        if entry.requirement_code is not None:
            assert nodes[entry.requirement_code].get("goal") == code      # one-to-one
    assert {c for c, e in core.goals.items() if not e.usable} == {"AHo", "AHp", "AHq", "AHr", "NS"}


def test_cs_mappings_exist_but_map_nothing_until_a_real_cs_audit_is_reviewed():
    tables = {m.program_key: m for m in load_mappings("2026-2027", PROGRAMS)}
    for key in ("sas-198-ba", "sas-198-bs"):
        assert tables[key].requirements == {} and tables[key].dn_subject is None


def test_track_check_blocks_a_mismatch_and_never_guesses_a_subject():
    tables = load_mappings("2026-2027", PROGRAMS)
    core = check_track(tables, "The SAS Core Curriculum (NB)", "Summer 2019", "sas-198-ba",
                       "Computer Science", {"sas-core"})
    assert (core.result, core.blocks) == ("matches_school_core", False)
    assert core.mapping is not None
    other = check_track(tables, "The SAS Core Curriculum (NB)", "Summer 2019", "sas-198-ba",
                        "Computer Science", set())
    assert (other.result, other.blocks) == ("mismatch", True)
    near = check_track(tables, "The SAS Core Curriculum", "Summer 2019", "sas-198-ba",
                       "Computer Science", {"sas-core"})
    assert near.result == "no_mapping"                         # exact subject only
    version = check_track(tables, "The SAS Core Curriculum (NB)", "Fall 2030", "sas-198-ba",
                          "Computer Science", {"sas-core"})
    assert version.result == "matches_school_core" and version.mapping is None


# --------------------------------------------------------------------------
# scripts/inject_dn_failures.py: every injected failure becomes NEEDS REVIEW
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def inject():
    spec = importlib.util.spec_from_file_location(
        "inject_dn_failures", ROOT / "scripts" / "inject_dn_failures.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _parse(pages):
    out = extract_audit_layout(render_pdf(pages), 40, 400_000)
    return parse_audit(out[3]).blocks[0]


def test_a_flipped_satisfied_marker_is_flagged(inject):
    block = _parse(inject.flipped_marker(audit_layout(sas_core_model()), number=2))
    r2 = block.requirements[1]
    assert r2.icon == "fulfilled"                    # what the damaged print shows...
    assert "STATUS_TEXT_MISMATCH" in r2.problems     # ...contradicts its Needs line
    assert all("COMPLETED_COUNT_MISMATCH" in r.problems for r in block.requirements)


def test_a_dropped_allocation_is_flagged(inject):
    block = _parse(inject.dropped_allocation(audit_layout(sas_core_model()), number=1))
    assert block.requirements[0].problems == ["ALLOCATION_COUNT_MISMATCH"]


def test_a_shifted_column_is_flagged(inject):
    block = _parse(inject.shifted_column(audit_layout(sas_core_model()), number=2))
    r2 = block.requirements[1]
    assert {"ALLOCATION_COUNT_MISMATCH", "COURSE_ROWS_NEED_REVIEW"} <= set(r2.problems)
    kinds = {p for c in r2.courses for p in c.problems}
    assert kinds == {"COURSE_LINE_UNREADABLE", "READING_ORDER_UNRELIABLE"}


def test_the_undamaged_audit_flags_nothing():
    block = _parse(audit_layout(sas_core_model()))
    assert [r.problems for r in block.requirements] == [[]] * 6 and block.warnings == []
