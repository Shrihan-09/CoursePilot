"""A student's DROP is respected: the course leaves, nothing replaces it.

World: the Prompt 11 plan-options world (test_plan_options) - real public SOC
records, the curated CS B.S. with SAS Core and its FABRICATED student. In
Spring 2027 01:810:135 is a planner-picked filler for Core Arts and
Humanities: removing it is the bug's shape (a course whose requirement the
planner used to backfill with another course in the same term).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from app.domain.plan_edits import PlanChange, PlanEdits
from app.domain.planning import FillerPreference, Pace, WorkloadLevel
from app.domain.planning_profile import PlanningProfileInput
from app.services.planning.edits import apply_preview, preview_change, profile_constraints, undo
from app.services.planning.profile import confirm_profile
from app.services.planning.saved import current_plan, save_plan

from .test_plan_options import FILLER, SPRING27, _ctx, _row, _where, planned, today  # noqa: F401
from .test_workload_levels import _student, world  # noqa: F401
from .test_workload_levels import HISTORY


def _terms(result) -> dict[str, set[str]]:
    return {t.term_code: {pc.course for pc in t.courses} for t in result.terms}


def _drop(session, student, course=FILLER, term=SPRING27):
    preview = preview_change(session, student, PlanChange(kind="remove", course=course,
                                                          term_code=term))
    assert preview.valid, preview.errors
    return preview


def test_a_drop_leaves_every_other_course_and_adds_nothing(planned):
    session, student = planned
    before = current_plan(session, student)
    spring = next(t for t in before.result.terms if t.term_code == SPRING27)
    dropped = next(pc for pc in spring.courses if pc.course == FILLER)
    preview = _drop(session, student)
    d = preview.diff
    sp = next(t for t in d.terms if t.term_code == SPRING27)
    # The dropped term: exactly one course out, nothing in.
    assert sp.removed == [FILLER] and sp.added == []
    assert sp.credits_after == sp.credits_before - dropped.credits
    # Every term: no course added, none moved (no backfill anywhere).
    for t in d.terms:
        assert t.added == [], (t.label, t.added)
        if t.term_code != SPRING27:
            assert t.removed == [], (t.label, t.removed)
    assert d.forced_moves == []
    # Elective credits stay where they were.
    after = {t.term_code: t.credits_after for t in d.terms}
    for t in d.terms:
        if t.term_code != SPRING27:
            assert after[t.term_code] == t.credits_before
    # The requirement it filled is reported unmet, in plain words.
    filled = {r.requirement_code for r in dropped.requirements}
    assert filled & {u.code for u in d.unmet}, (filled, d.unmet)
    assert d.summary.startswith(f"Spring 2027: {sp.credits_before.normalize():f} → "
                                f"{sp.credits_after.normalize():f} credits.")
    assert "now unmet" in d.summary and d.summary.endswith("Graduation unchanged.")
    assert d.dropped_course == FILLER and d.dropped_term == SPRING27
    # A preview is never the plan.
    assert current_plan(session, student).revision == before.revision


def test_apply_avoids_the_course_everywhere_holds_the_term_and_undo_restores(planned):
    session, student = planned
    before = current_plan(session, student)
    kept = _terms(before.result)[SPRING27] - {FILLER}
    preview = _drop(session, student)
    session.commit()
    saved = apply_preview(session, student, preview.preview_id)
    session.commit()
    assert _terms(saved.result)[SPRING27] == kept
    assert FILLER not in _where(saved)
    edits = PlanEdits.model_validate(_row(session, student).edits)
    avoid = [a for a in edits.avoid if a.course == FILLER]
    assert len(avoid) == 1 and avoid[0].term_code is None          # every term
    # A rebuild keeps the drop: the course never returns, the term is not refilled.
    rebuilt = save_plan(session, student, start_term=SPRING27,
                        constraints=profile_constraints(session, student))
    session.commit()
    assert FILLER not in _where(rebuilt)
    assert _terms(rebuilt.result)[SPRING27] <= kept
    # Undo (twice: the rebuild, then the drop) brings it back, avoid and all.
    undo(session, student)
    restored = undo(session, student)
    session.commit()
    assert FILLER in _terms(restored.result)[SPRING27]
    assert not [a for a in PlanEdits.model_validate(_row(session, student).edits).avoid
                if a.course == FILLER]


def test_below_the_minimum_warns_and_keeps_the_term_at_its_new_total(world, today):  # noqa: F811
    session = world
    student = _student(session, HISTORY)
    confirm_profile(session, student, PlanningProfileInput(
        workload_level=WorkloadLevel.SEMI_RIGOROUS, filler_preference=FillerPreference.FLEXIBLE,
        max_courses_per_term=6, pace=Pace.BALANCED, credit_min=16, credit_max=18),
        dt.datetime.now(dt.UTC))
    session.commit()
    save_plan(session, student, start_term=SPRING27,
              constraints=profile_constraints(session, student))
    session.commit()
    plan = current_plan(session, student).result
    spring = next(t for t in plan.terms if t.term_code == SPRING27)
    pick = next(pc for pc in spring.courses if pc.course == FILLER)
    total = spring.credits + spring.free_elective_credits
    assert total - pick.credits < 16, "fixture: the drop must cross the 16-credit minimum"
    preview = _drop(session, student)
    sp = next(t for t in preview.diff.terms if t.term_code == SPRING27)
    left = (total - pick.credits).normalize()
    # The bug's shape: under the minimum is exactly where the planner used to
    # refill the term. A pure drop: nothing added anywhere, nothing moved, and
    # no other term's credits change (the lost credits are not made up).
    assert sp.added == [] and sp.removed == [FILLER]
    assert sp.credits_after == total - pick.credits
    assert preview.diff.forced_moves == []
    for t in preview.diff.terms:
        assert t.added == [], (t.label, t.added)
        if t.term_code != SPRING27:
            assert (t.removed, t.credits_after) == ([], t.credits_before), t.label
    assert sp.below_minimum and sp.credit_floor == Decimal(16)
    assert f"{left:f} credits is below your 16-credit minimum for Spring 2027." in sp.warnings
    session.commit()
    saved = apply_preview(session, student, preview.preview_id)
    session.commit()
    after = next(t for t in saved.result.terms if t.term_code == SPRING27)
    assert after.credits + after.free_elective_credits == total - pick.credits
    cap = next(c for c in PlanEdits.model_validate(_row(session, student).edits).term_caps
               if c.term_code == SPRING27)
    assert cap.credit_max == int(left) and cap.credit_min == int(left)   # "Keep it at N"


def test_workload_over_budget_is_a_clear_warning(planned):
    session, student = planned
    d = _drop(session, student).diff
    sp = next(t for t in d.terms if t.term_code == SPRING27)
    over = sp.workload_after is not None and sp.budget is not None and sp.workload_after > sp.budget
    assert any(w.startswith("Workload ") and "over your" in w for w in sp.warnings) == over


def test_a_replacement_is_added_only_when_the_student_chooses_one(planned):
    from app.services.planning.options import course_options

    session, student = planned
    before = current_plan(session, student)
    drop = _drop(session, student)
    assert not [t for t in drop.diff.terms if t.added]            # the drop suggests nothing
    opts = course_options(_ctx(session, student), FILLER, SPRING27)
    replacement = next(o.course for g in opts.groups for o in g.options)
    swap = preview_change(session, student, PlanChange(
        kind="swap", course=FILLER, term_code=SPRING27, replacement=replacement))
    assert swap.valid, swap.errors
    assert replacement not in _terms(current_plan(session, student).result)[SPRING27]  # not yet confirmed
    assert current_plan(session, student).revision == before.revision
    session.commit()
    saved = apply_preview(session, student, swap.preview_id)
    session.commit()
    assert replacement in _terms(saved.result)[SPRING27]


def test_an_assistant_drop_is_the_same_drop(planned):
    from app.services.assistant.db_tools import DbAssistantTools

    session, student = planned
    direct = _drop(session, student).diff
    session.commit()
    via = DbAssistantTools(session, student).preview_plan_change(
        PlanChange(kind="remove", course=FILLER, term_code=SPRING27))
    assert via["valid"], via["errors"]
    d = via["diff"]
    assert d["summary"] == direct.summary
    assert [(t["term_code"], t["added"], t["removed"]) for t in d["terms"]] == [
        (t.term_code, t.added, t.removed) for t in direct.terms]
    assert d["forced_moves"] == [] and d["dropped_course"] == FILLER


@pytest.mark.parametrize("kind", ["swap", "move", "add"])
def test_other_changes_still_replan_normally(planned, kind):
    """Strict holds belong to a drop only."""
    from app.services.planning.edits import build_edit

    session, student = planned
    row = _row(session, student)
    from app.services.planning.saved import _decode
    plan = _decode(row.result_blob)
    change = {"swap": PlanChange(kind="swap", course=FILLER, term_code=SPRING27,
                                 replacement="01:190:207"),
              "move": PlanChange(kind="move", course=FILLER, term_code=SPRING27,
                                 to_term="20279"),
              "add": PlanChange(kind="add", course="01:190:207", term_code=SPRING27)}[kind]
    edit = build_edit(plan, profile_constraints(session, student), PlanEdits(), change)
    assert edit.strict is False


def test_the_drop_preview_asks_the_planner_to_add_nothing(planned, monkeypatch):
    import app.services.planning.service as service

    session, student = planned
    plan = current_plan(session, student).result
    seen = {}
    real = service.generate_plan

    def spy(*args, **kwargs):
        seen.update(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(service, "generate_plan", spy)
    _drop(session, student)
    assert seen["strict_holds"] is True
    assert seen["held_electives"] == {t.term_code: t.free_elective_credits for t in plan.terms}
    assert FILLER not in seen["holds"] and set(seen["holds"]) == set(_where(current_plan(session, student))) - {FILLER}


def test_strict_planning_keeps_the_plan_minus_the_drop(planned):
    """The engine half of a drop, without the term's saved cap: every other
    course where it was, nothing added anywhere, elective credits unmoved.
    The same request WITHOUT strict holds refills - the bug this prevents."""
    from app.domain.planning import CourseAvoid
    from app.services.planning.service import generate_plan

    session, student = planned
    row = _row(session, student)
    before = current_plan(session, student).result
    holds = {c: t for c, t in _where(current_plan(session, student)).items() if c != FILLER}
    base = profile_constraints(session, student)
    constraints = base.model_copy(update={"avoid": [*base.avoid, CourseAvoid(course=FILLER)]})
    electives = {t.term_code: t.free_elective_credits for t in before.terms}
    strict = generate_plan(session, student, start_term=row.start_term, constraints=constraints,
                           holds=holds, strict_holds=True, held_electives=electives)
    placed = {pc.course: t.term_code for t in strict.terms for pc in t.courses}
    assert placed == holds
    assert {t.term_code: t.free_elective_credits for t in strict.terms if t.free_elective_credits} \
        == {k: v for k, v in electives.items() if v}
    loose = generate_plan(session, student, start_term=row.start_term, constraints=constraints,
                          holds=holds)
    loose_placed = {pc.course: t.term_code for t in loose.terms for pc in t.courses}
    loose_electives = {t.term_code: t.free_elective_credits for t in loose.terms
                       if t.free_elective_credits}
    assert loose_placed != holds or loose_electives != {k: v for k, v in electives.items() if v}, \
        "fixture: without strict holds the planner must refill something"
