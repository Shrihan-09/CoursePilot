"""User drops are exact edits, never requests to refill a term. Fabricated student."""
import datetime as dt

import pytest

from app.domain.plan_edits import PlanChange, PlanEdits
from app.domain.planning import WorkloadLevel, PlanningConstraints
from app.domain.planning_profile import PlanningProfileInput
from app.services.planning.edits import apply_preview, preview_change, profile_constraints, undo
from app.services.planning.profile import confirm_profile
from app.services.planning.saved import current_plan, save_plan
from .test_plan_options import FILLER, SPRING27, _ctx, planned, today, world


@pytest.mark.parametrize('kind', ['remove', 'set_term'])
def test_drop_leaves_exact_roster_below_minimum_and_survives_rebuild(planned, kind):
    session, student = planned
    ctx = _ctx(session, student)
    # Preserve the source plan shape but give this test an explicit 16-credit floor.
    profile = PlanningProfileInput(workload_level=WorkloadLevel.SEMI_RIGOROUS,
                                   credit_min=16, credit_max=18, max_courses_per_term=6)
    confirm_profile(session, student, profile, dt.datetime.now(dt.UTC))
    session.commit()
    before = save_plan(session, student, start_term=SPRING27, constraints=profile_constraints(session, student))
    session.commit()
    t = next(t for t in before.result.terms if t.term_code == SPRING27)
    pick = next(c for c in t.courses if c.role == 'filler')
    keep = {c.course for c in t.courses} - {pick.course}
    change = PlanChange(kind=kind, course=pick.course if kind == 'remove' else None,
                        term_code=SPRING27, courses=sorted(keep), expected_revision=before.revision)
    preview = preview_change(session, student, change)
    assert preview.valid, preview.errors
    diff = next(t for t in preview.diff.terms if t.term_code == SPRING27)
    assert diff.added == []
    assert diff.removed == [pick.course]
    assert diff.warnings and '16-credit minimum' in diff.warnings[0]
    if kind == 'remove':
        assert diff.credits_after == diff.credits_before - pick.credits
    assert diff.requirement_effects  # rescheduled later or visibly unfilled
    assert current_plan(session, student).revision == before.revision
    saved = apply_preview(session, student, preview.preview_id)
    session.commit()
    after = next(t for t in saved.result.terms if t.term_code == SPRING27)
    assert {c.course for c in after.courses} == keep
    assert any('leave this space open' in w for w in after.warnings)
    assert any(a.course == pick.course and a.term_code == SPRING27 for a in PlanEdits.model_validate(saved.edits).avoid)
    override = next(o for o in PlanningConstraints.model_validate(saved.constraints).term_overrides if o.term_code == SPRING27)
    assert override.allow_below_minimum and set(override.manual_courses) == keep
    rebuilt = save_plan(session, student, start_term=SPRING27, constraints=profile_constraints(session, student))
    session.commit()
    rebuilt_term = next(t for t in rebuilt.result.terms if t.term_code == SPRING27)
    assert {c.course for c in rebuilt_term.courses} == keep
    assert rebuilt_term.warnings


def test_explicit_swap_still_adds_the_chosen_replacement_and_undo_restores(planned):
    session, student = planned
    ctx = _ctx(session, student)
    from app.services.planning.options import course_options
    opts = course_options(ctx, FILLER, SPRING27)
    replacement = next(o.course for g in opts.groups for o in g.options)
    preview = preview_change(session, student, PlanChange(kind='swap', course=FILLER,
                             replacement=replacement, term_code=SPRING27))
    assert preview.valid, preview.errors
    saved = apply_preview(session, student, preview.preview_id)
    assert replacement in {c.course for t in saved.result.terms if t.term_code == SPRING27 for c in t.courses}
    restored = undo(session, student)
    assert FILLER in {c.course for t in restored.result.terms if t.term_code == SPRING27 for c in t.courses}


def test_assistant_drop_does_not_search_until_suggest_replacement_is_tapped(planned):
    import asyncio
    from app.services.assistant.agent import Assistant
    from app.services.assistant.db_tools import DbAssistantTools
    from app.services.assistant.fake import FakeAssistantProvider
    from app.services.assistant import actions
    session, student = planned
    tools = DbAssistantTools(session, student)
    assistant = Assistant(tools, default=FakeAssistantProvider())
    turn = asyncio.run(assistant.turn(f'Remove {FILLER} from Spring 2027'))
    assert turn.reply.proposals, turn.reply.text
    assert not any(c['name'] == 'search_courses' for c in turn.tool_calls)
    done = actions.execute(tools, turn.reply.proposals[0].action)
    revision = tools.plan_revision()
    assert any(c.label == 'Suggest a replacement' for c in done.suggestions)
    assert done.cards == []
    suggestions = asyncio.run(assistant.turn('', answers={'suggest_replacement': SPRING27}))
    assert any(c['name'] == 'search_courses' for c in suggestions.tool_calls)
    assert tools.plan_revision() == revision


def test_emptying_a_future_term_keeps_the_warning_visible(planned):
    session, student = planned
    before = current_plan(session, student)
    term = next(t for t in reversed(before.result.terms) if t.courses and
                all(c.role != 'anchor' and not c.retake for c in t.courses))
    preview = preview_change(session, student, PlanChange(kind='set_term', term_code=term.term_code,
                             courses=[], expected_revision=before.revision))
    assert preview.valid, preview.errors
    saved = apply_preview(session, student, preview.preview_id)
    emptied = next(t for t in saved.result.terms if t.term_code == term.term_code)
    assert emptied.courses == [] and emptied.free_elective_credits == 0
    assert emptied.warnings and emptied.user_edited


def test_preserved_elective_slots_never_push_explicit_choices_above_the_cap(planned):
    from decimal import Decimal
    from app.services.planning.edits import plan_constraints
    from app.services.planning.service import generate_plan
    session, student = planned
    term = _ctx(session, student).term(SPRING27)
    edits = PlanEdits(term_rosters={SPRING27: [c.course for c in term.courses]},
                      term_free_electives={SPRING27: Decimal(6)})
    constraints = plan_constraints(profile_constraints(session, student), edits)
    result = generate_plan(session, student, start_term=SPRING27, constraints=constraints)
    after = next(t for t in result.terms if t.term_code == SPRING27)
    assert after.credits + after.free_elective_credits <= after.workload.credit_max
