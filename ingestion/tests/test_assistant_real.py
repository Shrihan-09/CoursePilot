"""Prompt 11 Part B on REAL public SOC data: the assistant's database tools.

World: Part A's (test_plan_options) - real Spring 2026 / Fall 2026 / Spring
2027 SOC records, the curated CS B.S. with SAS Core, and its FABRICATED
student. The fake provider drives the turns; what is tested is that
`DbAssistantTools` reshapes the real services correctly and that the guided
swap works end to end on the real planner: valid cards only, "Choose this"
applies through Part A's preview/apply with the student's choice recorded,
undo restores, and nothing is applied without the tap.
"""

from __future__ import annotations

import asyncio

from app.domain.plan_edits import CourseState
from app.services.assistant import actions
from app.services.assistant.agent import Assistant
from app.services.assistant.db_tools import DbAssistantTools
from app.services.assistant.fake import FakeAssistantProvider
from app.services.planning.edits import undo
from app.services.planning.saved import current_plan

from .test_plan_options import FILLER, SPRING27, _ctx, planned, today  # noqa: F401
from .test_workload_levels import world  # noqa: F401


def _assistant(session, student):
    tools = DbAssistantTools(session, student)
    return tools, Assistant(tools, default=FakeAssistantProvider("fake-assistant"),
                            escalation=FakeAssistantProvider("fake-assistant_escalation"))


def test_the_read_tools_reshape_the_real_services(planned) -> None:  # noqa: F811
    session, student = planned
    tools = DbAssistantTools(session, student)
    plan = tools.get_plan()
    spring = next(t for t in plan["terms"] if t["term_code"] == SPRING27)
    lines = {c["course"]: c for c in spring["courses"]}
    assert FILLER in lines and lines["01:640:152"]["state"] == "locked"
    assert {c["offering"] for c in lines.values()} <= {"confirmed", "historically offered"}
    assert plan["graduation"] and plan["revision"] == 1
    why = tools.explain_course_placement("01:640:152", SPRING27)
    assert why["reasons"] and why["term_code"] == SPRING27
    progress = tools.get_progress(include_gpa=False)
    assert progress["source_label"] == "estimated" and "gpa" not in progress
    assert progress["requirements"] and all(r["status"] for r in progress["requirements"])
    assert {"name", "support"} <= set(tools.get_profile_summary()["program"])
    found = tools.search_courses(text="", term_code=SPRING27, requirements=None,
                                 topic="sociology", filters=None, exclude=[], limit=5)
    assert all(h["term_code"] == SPRING27 for h in found["results"])


def test_the_guided_swap_runs_end_to_end_on_the_real_planner(planned) -> None:  # noqa: F811
    session, student = planned
    tools, assistant = _assistant(session, student)
    before = tools.plan_revision()
    turn = asyncio.run(assistant.turn(f"I don't want {FILLER}. Same requirement, any format, "
                                "nothing else."))
    cards = turn.reply.cards
    assert turn.reply.questions == [] and cards, turn.reply.text
    assert tools.plan_revision() == before                   # showing options writes nothing
    ctx = _ctx(session, student)
    for c in cards:
        assert c.term_code == SPRING27 and c.offering in ("confirmed", "historically offered")
        verdict = ctx.check(SPRING27, {c.course: ctx.fills(c.course)}, replacing=FILLER)
        assert verdict[c.course].ok                          # the planner's own gates
    pick = cards[0]
    done = actions.execute(tools, pick.action)
    if done.proposals:                                       # a big side effect: one more tap
        assert done.proposals[0].reason == "second_tap" and tools.plan_revision() == before
        done = actions.execute(tools, done.proposals[0].action)
    session.commit()
    assert done.undo_available and tools.plan_revision() == before + 1
    saved = current_plan(session, student)
    state = next(s for s in saved.course_states if s.course == pick.course)
    assert state.state is CourseState.USER_SELECTED
    undo(session, student)
    session.commit()
    restored = current_plan(session, student)
    assert any(pc.course == FILLER for t in restored.result.terms for pc in t.courses)


def test_a_change_request_is_only_a_proposal_until_confirmed(planned) -> None:  # noqa: F811
    session, student = planned
    tools, assistant = _assistant(session, student)
    turn = asyncio.run(assistant.turn("Make spring lighter"))
    assert tools.plan_revision() == 1 and not turn.reply.undo_available
    for p in turn.reply.proposals:
        assert p.change["kind"] == "lighten_term" and p.preview_id
