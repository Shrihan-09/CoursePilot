"""Prompt 11 Part B: the assistant, without a database.

Everything runs on the eval's fabricated students (app/services/assistant/
eval/world.py) and the deterministic fake provider: no network, no key.
"""

from __future__ import annotations

import json
import re

import pytest

from app.domain.plan_edits import PlanChange
from app.llm.base import Role
from app.services.assistant import actions
from app.services.assistant.agent import AgentConfig, Assistant
from app.services.assistant.cards import shorten
from app.services.assistant.contracts import Facts, facts_from
from app.services.assistant.eval.runner import fake_factory, run_all, summarize
from app.services.assistant.eval.world import CATALOG, SyntheticTools
from app.services.assistant.fake import FakeAssistantProvider
from app.services.assistant.grounding import check_reply
from app.services.assistant.prompts import TOOL_RESULTS
from app.services.assistant.router import route
from app.services.assistant.tools import MODEL_TOOLS, run_tool

EASY = re.compile(r"\b(easy|easier|easiest|hard|harder|difficult)\b", re.I)


def _assistant(student="alex", script=None, **config):
    tools = SyntheticTools(student)
    default = FakeAssistantProvider("fake-assistant", script=script)
    escalation = FakeAssistantProvider("fake-assistant_escalation")
    return tools, default, Assistant(tools, default=default, escalation=escalation,
                                     config=AgentConfig(**config))


# --------------------------------------------------------------------------
# Router
# --------------------------------------------------------------------------


@pytest.mark.parametrize(("text", "intent", "changes"), [
    ("Why is CS 206 in Fall 2027?", "question", 0),
    ("What do I still need for Core?", "question", 0),
    ("What happens if I fail Calc II again?", "question", 0),
    ("Make spring lighter, I'm rushing.", "change_request", 1),
    ("Move physics to fall.", "change_request", 1),
    ("Make spring lighter and move physics to fall", "change_request", 2),
    ("Swap Art History for something online", "guided_swap", 1),
    ("I don't want to take Art History, but I don't know what's better.", "guided_swap", 1),
    ("I'd rather do a humanities course than history this spring.", "guided_swap", 1),
    ("No Fridays from now on", "preference_change", 1),
    ("Write my essay for Expos", "out_of_scope", 0),
    ("Register me in WebReg", "out_of_scope", 0),
    ("Here's my NetID, log in to WebReg for me", "out_of_scope", 0),
    ("What's the weather tomorrow?", "out_of_scope", 0),
    ("How do I add these index numbers in WebReg myself?", "change_request", 1),
])
def test_router_classification(text, intent, changes) -> None:
    r = route(text)
    assert r.intent == intent
    assert r.change_count == changes


def test_router_extracts_what_the_message_already_answers() -> None:
    k = route("Something about tech that still counts, online, no Fridays and no mornings").known
    assert (k.format, k.instead, k.avoid_days, k.earliest, k.topic) == (
        "online", "same", ["F"], "12:00", "tech")
    assert route("What's my GPA?").asks_gpa and not route("What do I need?").asks_gpa


# --------------------------------------------------------------------------
# Grounding
# --------------------------------------------------------------------------


def _facts(tools: SyntheticTools, *calls) -> Facts:
    f = Facts()
    for name, args in calls:
        out = run_tool(tools, name, args, gpa_allowed=False)
        assert out.ok, out.error
        f.merge(out.facts)
    return f


def test_an_ungrounded_claim_is_rejected() -> None:
    tools = SyntheticTools("alex")
    f = _facts(tools, ("get_plan", {}))
    ok = check_reply("01:198:206 Introduction to Discrete Structures II is in Fall 2027.", f,
                     user_text="", gpa_requested=False)
    assert ok == []
    for text, needle in [
        ("Take 01:777:101 Quantum Ethics instead.", "names courses no tool returned"),
        ("Try Advanced Underwater Ethics next spring.", "names something no tool returned"),
        ("01:198:206 is 6 credits.", "credits"),
        ("You could take it in Summer 2031.", "Summer 2031"),
        ("It also counts for WCd.", "requirement code"),
        ("01:640:152 Calculus II has a prerequisite you already met.", "prerequisite"),
        ("01:082:105 Art History: Ancient to Medieval is an easy one.", "easy or hard"),
        ("I've updated your plan.", "applied"),
        ("I registered you for 01:198:211.", "register"),
        ("You'll get an A in 01:198:211.", "grade"),
    ]:
        problems = check_reply(text, f, user_text="", gpa_requested=False)
        assert any(needle in p for p in problems), (text, problems)
    # Declining to rate is not rating.
    assert check_reply("I can't say whether a course is easy.", f, user_text="",
                       gpa_requested=False) == []


def test_labels_pass_through_unchanged() -> None:
    tools = SyntheticTools("alex")
    f = _facts(tools, ("get_plan", {}))
    # 01:198:206 in Fall 2027 is "historically offered", never "confirmed".
    problems = check_reply("01:198:206 is confirmed for Fall 2027.", f, user_text="",
                           gpa_requested=False)
    assert any("historically offered" in p for p in problems)
    progress = _facts(tools, ("get_progress", {"include_gpa": False}))
    problems = check_reply("This is from your Degree Navigator audit.", progress, user_text="",
                           gpa_requested=False)
    assert any("estimated" in p for p in problems)


def test_cards_and_descriptions_come_from_tool_data() -> None:
    desc = CATALOG["01:700:101"]["description"]
    short = shorten(desc)
    assert desc.startswith(short.rstrip("…")) and len(short) <= 241
    assert shorten(None) is None and shorten("   ") is None
    long = "Word " * 100 + "end."
    assert shorten(long).endswith("…") and set(shorten(long)[:-1].split()) <= {"Word"}


# --------------------------------------------------------------------------
# No apply without a tap
# --------------------------------------------------------------------------


def test_apply_is_not_a_model_tool() -> None:
    assert not {n for n in MODEL_TOOLS if "apply" in n}
    tools = SyntheticTools("alex")
    out = run_tool(tools, "apply_confirmed_plan_change", {"preview_id": "x"}, gpa_allowed=False)
    assert not out.ok and out.error == "unknown_tool"


async def test_apply_is_impossible_without_a_confirm_token() -> None:
    script = [json.dumps({"tool_calls": [
        {"name": "apply_confirmed_plan_change", "args": {"preview_id": "pv"}},
        {"name": "apply_confirmed", "args": {"preview_id": "pv"}},
        {"name": "apply_preference", "args": {"preference": {"avoid_days": ["F"]}}}]})]
    tools, _, assistant = _assistant(script=script)
    result = await assistant.turn("Make spring lighter")
    assert tools.plan_revision() == 1 and tools.applied == []
    assert {c["error"] for c in result.tool_calls[:3]} == {"unknown_tool"}
    assert result.reply.proposals and not result.reply.undo_available
    # Only the card's action, executed for a tap, applies it.
    done = actions.execute(tools, result.reply.proposals[0].action)
    assert done.undo_available and tools.plan_revision() == 2


# --------------------------------------------------------------------------
# Caps
# --------------------------------------------------------------------------


async def test_the_tool_call_cap_is_enforced() -> None:
    many = json.dumps({"tool_calls": [{"name": "get_plan", "args": {}}] * 4})
    tools, _, assistant = _assistant(script=[many] * 6, max_tool_calls=8)
    result = await assistant.turn("What do I still need?")
    assert len(result.tool_calls) <= 8
    assert result.reply.generated_by in ("model", "template")


async def test_the_turn_token_budget_is_enforced() -> None:
    tools, default, assistant = _assistant(turn_token_budget=10)
    result = await assistant.turn("What do I still need for Core?")
    assert result.reply.generated_by == "template"
    assert result.usage.calls <= 2


async def test_out_of_scope_makes_no_model_or_tool_call() -> None:
    tools, default, assistant = _assistant()
    result = await assistant.turn("Can you register me for 01:198:211 in WebReg?")
    assert result.reply.generated_by == "router" and default.calls == 0
    assert result.tool_calls == [] and "NetID" in result.reply.text


# --------------------------------------------------------------------------
# Privacy
# --------------------------------------------------------------------------


async def test_gpa_is_not_sent_unless_asked() -> None:
    tools = SyntheticTools("alex")
    assert "gpa" not in run_tool(tools, "get_progress", {"include_gpa": True},
                                 gpa_allowed=False).data
    assert run_tool(tools, "get_progress", {"include_gpa": True},
                    gpa_allowed=True).data["gpa"] == "3.41"
    sent: list[str] = []

    class Spy(FakeAssistantProvider):
        async def complete(self, **kw):
            sent.extend(m.content for m in kw["messages"])
            return await super().complete(**kw)

    script = [json.dumps({"tool_calls": [{"name": "get_progress",
                                          "args": {"include_gpa": True}}]})]
    assistant = Assistant(tools, default=Spy("spy", script=script))
    await assistant.turn("What do I still need for Core?")
    results = [m for m in sent if m.startswith(TOOL_RESULTS)]
    assert results and not any("3.41" in m or '"gpa"' in m for m in results)
    sent.clear()
    await Assistant(tools, default=Spy("spy")).turn("What's my GPA?")
    assert any("3.41" in m for m in sent if m.startswith(TOOL_RESULTS))


# --------------------------------------------------------------------------
# Guided swap (B1b)
# --------------------------------------------------------------------------


async def test_the_clarify_step_is_skipped_when_preferences_are_known() -> None:
    # Devon's saved format is online; the message answers the rest.
    tools, default, assistant = _assistant("devon")
    result = await assistant.turn("I don't want Art History. Same requirement, nothing else.")
    assert result.reply.questions == [] and result.reply.cards
    assert all({"online", "async"} & set(c.delivery) for c in result.reply.cards)
    assert default.calls == 0                       # deterministic: no model call
    asked = await _assistant("alex")[2].turn("I don't want Art History.")
    assert [q.id for q in asked.reply.questions] == ["instead", "format", "else"]


async def test_the_carousel_shows_only_valid_options() -> None:
    tools, _, assistant = _assistant()
    first = await assistant.turn("I don't want Art History. Same requirement, any format, "
                                 "nothing else.")
    cards = first.reply.cards
    assert 5 <= len(cards) <= 10
    for c in cards:
        assert tools.valid_option(c.course, c.term_code)
        assert c.offering == CATALOG[c.course]["offered"][c.term_code]
        assert c.action and c.action["change"]["replacement"] == c.course
        assert not EASY.search(" ".join([c.title, *(c.why), c.description or ""]))
    # The completed / planned / prerequisite-blocked courses are never offered.
    shown = {c.course for c in cards}
    assert not shown & (tools.completed | set(tools._where()))


async def test_choose_this_applies_with_undo_and_a_big_effect_needs_a_second_tap() -> None:
    tools, _, assistant = _assistant()
    first = await assistant.turn("I don't want Art History. Same requirement, any format, "
                                 "nothing else.")
    by = {c.course: c for c in first.reply.cards}
    big = actions.execute(tools, by["01:195:150"].action)      # moves Physics to Fall
    assert tools.plan_revision() == 1 and big.proposals[0].reason == "second_tap"
    assert big.proposals[0].diff["forced_moves"][0]["course"] == "01:750:203"
    small = actions.execute(tools, by["01:700:101"].action)
    assert tools.plan_revision() == 2 and small.undo_available
    assert "graduation unchanged" in small.applied_summary
    assert any(s.value.startswith("sections:01:700:101") for s in small.suggestions)
    state = next(c for t in tools.plan["terms"] for c in t["courses"]
                 if c["course"] == "01:700:101")
    assert state["state"] == "user_selected"


async def test_the_section_pick_respects_blocked_times() -> None:
    tools, _, assistant = _assistant()          # Alex works Tue/Thu 9:00-12:00
    result = await assistant.turn("", answers={"sections": "01:640:152|20271"})
    assert [s.index_number for s in result.reply.sections] == ["08811"]
    assert result.reply.sections[0].meetings[0].startswith("Mon 10:00am")


async def test_a_stale_proposal_is_previewed_again_not_applied() -> None:
    tools, _, assistant = _assistant()
    result = await assistant.turn("Make spring lighter and move physics to fall")
    assert result.escalated and len(result.reply.proposals) == 2
    actions.execute(tools, result.reply.proposals[0].action)
    again = actions.execute(tools, result.reply.proposals[1].action)
    assert tools.plan_revision() == 2 and again.proposals[0].reason == "refreshed"


def test_facts_include_course_keys_inside_tool_text() -> None:
    f = facts_from("explain_course_placement",
                   {"course": "01:198:206", "reasons": ["needs 01:198:205 first"]})
    assert {"01:198:206", "01:198:205"} <= set(f.courses)


def test_a_preview_rejects_invented_courses() -> None:
    tools = SyntheticTools("alex")
    out = tools.preview_plan_change(PlanChange(kind="add", course="01:999:420",
                                               term_code="20271"))
    assert not out["valid"] and out["errors"][0].startswith("unknown_course")


# --------------------------------------------------------------------------
# The golden eval (B4) - fake provider, both tiers
# --------------------------------------------------------------------------


@pytest.mark.parametrize("escalation", [True, False])
async def test_the_golden_eval_passes(escalation) -> None:
    results = await run_all(fake_factory, escalation_mode=escalation)
    s = summarize(results)
    assert s["conversations"] >= 30
    assert s["pass_rate"] >= 0.9, s["failed"]
    assert s["no_auto_apply_rate"] == 1.0
    assert s["no_invented_courses_rate"] == 1.0
    for r in results:
        for t in r.transcript:
            assert not EASY.search(t["assistant"].replace("can't say whether", "")), t


# --------------------------------------------------------------------------
# Providers
# --------------------------------------------------------------------------


def test_openai_is_the_default_and_anthropic_is_optional(monkeypatch) -> None:
    from app.core.config import Settings
    from app.llm.providers.openai import OpenAIProvider
    from app.llm.registry import build_assistant_provider
    from app.services.assistant import service

    monkeypatch.delenv("ASSISTANT_PROVIDER", raising=False)
    s = Settings(_env_file=None, openai_api_key="sk-test-not-real", anthropic_api_key=None)
    assert s.assistant_provider == "openai"
    assert (s.assistant_model, s.assistant_escalation_model) == ("gpt-5.6-luna",
                                                                 "gpt-5.6-terra")
    default = build_assistant_provider(s, Role.ASSISTANT)
    escalation = build_assistant_provider(s, Role.ASSISTANT_ESCALATION)
    assert isinstance(default, OpenAIProvider) and default._model == "gpt-5.6-luna"
    assert escalation._model == "gpt-5.6-terra"
    assert service.availability(s) == (True, None)
    # No key: unavailable, with no exception and no Anthropic involvement.
    none = Settings(_env_file=None, openai_api_key=None, anthropic_api_key=None)
    enabled, reason = service.availability(none)
    assert not enabled and "set up" in reason
    off = Settings(_env_file=None, assistant_provider="none")
    assert service.availability(off)[0] is False


def test_tests_never_use_a_real_model() -> None:
    from app.core.config import get_settings

    get_settings.cache_clear()
    assert get_settings().assistant_provider == "fake"
