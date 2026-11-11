"""The assistant's turn (Prompt 11 Part B, B2), following docs/AGENT_ARCHITECTURE.md:

    router ──out of scope──> refusal + what it can do            (no model call)
       │
       ├─ guided swap / its follow-ups ─> guided.py               (deterministic)
       │
       └─ question / change / preference
             model ⇄ tools  (≤ max_tool_calls, ≤ token budget)
             reply ─> grounding gate ─> cards from tool data
                 rejected -> regenerate with the problems (on the escalation
                 model), rejected again -> template answer from tool data

## Two tiers

The default model answers. The escalation model takes the turn when the
message asks for two or more plan changes, and takes the retry when the
default model's reply fails grounding or validation (bad JSON, bad tool
arguments, a refused proposal). `escalation_enabled=False` keeps the retry on
the default model - the eval's "default-only" mode.

## Nothing here applies a change

A proposal card carries an `action` the service turns into a one-time token.
The model's tool list has no apply, so no model output can reach one.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass

from pydantic import ValidationError

from app.domain.plan_edits import PlanChange
from app.llm.base import LLMProvider, Message, ProviderError, ProviderNotConfiguredError, Role
from app.services.assistant import guided
from app.services.assistant.cards import change_title, option_card, proposal_card, section_card
from app.services.assistant.contracts import (
    Activity,
    AssistantReply,
    Chip,
    ProposalCard,
    TurnResult,
    TurnUsage,
    term_label,
)
from app.services.assistant.grounding import check_reply
from app.services.assistant.prompts import EXPAND_SYSTEM, REJECTED, STUDENT, TOOL_RESULTS, system_prompt
from app.services.assistant.router import REFUSALS, Route, extract_known, route
from app.services.assistant.tools import AssistantTools
from app.services.assistant.turn import ToolBudgetExceeded, TurnContext

SUGGESTIONS = [Chip(label="Make next semester lighter", value="Make next semester lighter"),
               Chip(label="What Core do I have left?", value="What Core do I have left?"),
               Chip(label="Show online options", value="Show online options for next semester")]
_KEYWORD = re.compile(r"^[a-z][a-z\-]{2,20}$")
_FLOW_ANSWER = re.compile(r"\?\s*$")


@dataclass
class AgentConfig:
    max_tool_calls: int = 8
    max_output_tokens: int = 400
    turn_token_budget: int = 60_000
    escalation_enabled: bool = True
    max_steps: int = 5
    history_messages: int = 6
    result_chars: int = 7_000

    @classmethod
    def from_settings(cls, settings) -> AgentConfig:
        return cls(max_tool_calls=settings.assistant_max_tool_calls,
                   max_output_tokens=settings.assistant_max_output_tokens,
                   turn_token_budget=settings.assistant_turn_token_budget,
                   escalation_enabled=settings.assistant_escalation_enabled)


def _fit(data, limit: int) -> str:
    """JSON within `limit` chars: halve the longest list until it fits, so the
    model always receives valid JSON (a cut string would not be)."""
    text = json.dumps(data, default=str, separators=(",", ":"))
    for _ in range(12):
        if len(text) <= limit:
            return text
        lists: list[list] = []

        def find(node):
            if isinstance(node, list):
                lists.append(node)
                for x in node:
                    find(x)
            elif isinstance(node, dict):
                for v in node.values():
                    find(v)
        find(data)
        if not lists:
            break
        longest = max(lists, key=lambda x: len(json.dumps(x, default=str)))
        if len(longest) <= 1:
            break
        del longest[max(1, len(longest) // 2):]
        text = json.dumps(data, default=str, separators=(",", ":"))
    return text[:limit]


def _item_term(item: dict) -> str | None:
    """The term an option/search item was checked for."""
    return item.get("term_code") or item.get("offering_term") or item.get("planned_term")


def _parse(text: str) -> dict | None:
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = candidate.strip("`").split("\n", 1)[-1]
    start, end = candidate.find("{"), candidate.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        value = json.loads(candidate[start:end + 1])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


class Assistant:
    def __init__(self, tools: AssistantTools, *, default: LLMProvider,
                 escalation: LLMProvider | None = None, config: AgentConfig | None = None
                 ) -> None:
        self.tools = tools
        self.default = default
        self.escalation = escalation
        self.config = config or AgentConfig()

    # ------------------------------------------------------------------
    async def turn(self, text: str, *, history: list[dict] | None = None,
                   flow: dict | None = None, answers: dict | None = None,
                   on_activity: Callable[[Activity], None] | None = None) -> TurnResult:
        text = (text or "").strip()
        r = route(text) if text else Route("guided_swap" if flow else "question")
        usage = TurnUsage()
        ctx = TurnContext(self.tools, gpa_allowed=r.asks_gpa,
                          max_tool_calls=self.config.max_tool_calls, on_activity=on_activity)
        escalated = False
        failures: list[list[str]] = []

        async def expand(topic: str) -> list[str]:
            return await self._expand(topic, usage)

        try:
            if r.intent == "out_of_scope":
                reply = self._refusal(r)
            elif answers and answers.get("sections"):
                reply = self._sections(ctx, str(answers["sections"]))
            elif answers and answers.get("webreg"):
                term = str(answers["webreg"])
                reply = AssistantReply(
                    text=(f"Here's your WebReg checklist for {term_label(term) or term}. Copy "
                          "the index numbers, then add them in WebReg yourself - Coursenav "
                          "never signs in for you."),
                    intent="question", generated_by="template", webreg_term=term)
            elif flow and flow.get("kind") == "guided_swap" and self._continues(r, text, answers):
                reply, new_flow = await guided.continue_flow(ctx, flow, text, answers,
                                                             expand=expand)
                reply.flow = new_flow
            elif r.intent == "guided_swap":
                reply, new_flow = await guided.start(ctx, text, r.known, expand=expand)
                reply.flow = new_flow
            else:
                reply, escalated, failures = await self._model_turn(ctx, text, history or [],
                                                                    r, usage)
        except ToolBudgetExceeded:
            reply = self._template(ctx, r)
        reply.activity = ctx.activity_summary()
        if reply.intent == "question" and r.intent != "question":
            reply.intent = r.intent
        if not reply.questions and not reply.cards and not reply.proposals \
                and not reply.sections and r.intent != "out_of_scope":
            reply.suggestions = reply.suggestions or []
        return TurnResult(reply=reply, usage=usage, tool_calls=ctx.calls, escalated=escalated,
                          grounding_failures=failures, route=r,
                          facts=ctx.facts)

    @staticmethod
    def _continues(r: Route, text: str, answers: dict | None) -> bool:
        """Is this message an answer to the open guided flow, or a new topic?"""
        if answers:
            return True
        if r.intent in ("out_of_scope", "change_request") or r.courses:
            return False
        if r.intent == "guided_swap":
            return False
        k = extract_known(text)
        return bool(k.format or k.instead or k.any_constraint()) or not _FLOW_ANSWER.search(text)

    # ------------------------------------------------------------------
    def _refusal(self, r: Route) -> AssistantReply:
        chips = list(SUGGESTIONS)
        if r.out_of_scope == "registration":
            chips = [Chip(label="Pick sections for next semester",
                          value="Help me pick sections that fit my week"), *chips[:2]]
        return AssistantReply(text=REFUSALS[r.out_of_scope or "unrelated"],
                              intent="out_of_scope", generated_by="router", suggestions=chips)

    def _sections(self, ctx: TurnContext, value: str) -> AssistantReply:
        course, _, term = value.partition("|")
        out = ctx.call("find_sections", {"course": course, "term_code": term})
        when = term_label(term) or term
        if not out.ok:
            return AssistantReply(text=f"I couldn't load sections of {course} for {when}.",
                                  generated_by="template")
        data = out.data
        cards = [section_card(s, course=course, title=data.get("title", ""), term_code=term)
                 for s in data.get("sections") or []][:3]
        if not cards:
            notes = " ".join(data.get("notes") or [])
            return AssistantReply(
                text=(f"No section of {course} in {when} fits your blocked times. {notes}"
                      ).strip(), generated_by="template")
        return AssistantReply(
            text=(f"Here {'is the best section' if len(cards) == 1 else f'are the best {len(cards)} sections'} "
                  f"of {course} for {when} that fit your blocked times. Tap one to save it to "
                  "that semester's schedule."),
            generated_by="template", sections=cards,
            suggestions=[Chip(label="Copy index numbers for WebReg", value=f"webreg:{term}")])

    async def _expand(self, topic: str, usage: TurnUsage) -> list[str]:
        base = [w for w in re.findall(r"[a-z]{3,}", topic.lower())][:4]
        try:
            completion = await self.default.complete(
                role=Role.ASSISTANT, messages=[Message("user", topic[:120])],
                system=EXPAND_SYSTEM, schema={"type": "object"}, max_tokens=300)
        except (ProviderError, ProviderNotConfiguredError):
            return base
        usage.add(completion.model, completion.usage.input_tokens,
                  completion.usage.output_tokens, completion.usage.cached_input_tokens)
        payload = completion.structured or _parse(completion.text) or {}
        words = [str(w).lower().strip() for w in payload.get("keywords") or []
                 if isinstance(w, str)]
        return list(dict.fromkeys([*base, *(w for w in words if _KEYWORD.match(w))]))[:10]

    # ------------------------------------------------------------------
    async def _model_turn(self, ctx: TurnContext, text: str, history: list[dict], r: Route,
                          usage: TurnUsage) -> tuple[AssistantReply, bool, list[list[str]]]:
        cfg = self.config
        can_escalate = cfg.escalation_enabled and self.escalation is not None
        escalated = can_escalate and r.change_count >= 2
        provider = self.escalation if escalated else self.default
        messages = [Message(m["role"], str(m["content"])[:2_000])
                    for m in history[-cfg.history_messages:]
                    if m.get("role") in ("user", "assistant") and m.get("content")]
        messages.append(Message("user", f"{STUDENT}: {text}"))
        failures: list[list[str]] = []
        for attempt in (1, 2):
            role = Role.ASSISTANT_ESCALATION if escalated else Role.ASSISTANT
            payload, error = await self._loop(provider, role, messages, ctx, usage)
            if payload is None:
                problems = [error or "no reply"]
            else:
                reply, problems = self._build(payload, ctx, text, r)
                if not problems:
                    return reply, escalated, failures
            failures.append(problems)
            if attempt == 2 or error in ("provider_error", "token_budget"):
                break
            messages.append(Message("user", REJECTED + "\n" + json.dumps(
                {"problems": problems[:6], "instruction": "Reply again using only this "
                 "turn's tool results. Call tools if you need more facts."})))
            if can_escalate and not escalated:
                provider, escalated = self.escalation, True
        return self._template(ctx, r), escalated, failures

    async def _loop(self, provider: LLMProvider, role: Role, messages: list[Message],
                    ctx: TurnContext, usage: TurnUsage) -> tuple[dict | None, str | None]:
        cfg = self.config
        system = system_prompt()
        for _ in range(cfg.max_steps):
            if usage.total >= cfg.turn_token_budget:
                return None, "token_budget"
            try:
                completion = await provider.complete(
                    role=role, messages=messages, system=system, schema={"type": "object"},
                    max_tokens=cfg.max_output_tokens)
            except (ProviderError, ProviderNotConfiguredError):
                return None, "provider_error"
            usage.add(completion.model, completion.usage.input_tokens,
                      completion.usage.output_tokens, completion.usage.cached_input_tokens)
            payload = completion.structured or _parse(completion.text)
            messages.append(Message("assistant", completion.text[:4_000] or "{}"))
            if not payload:
                return None, "the reply was not a JSON object"
            if isinstance(payload.get("reply"), dict):
                return payload["reply"], None
            calls = payload.get("tool_calls")
            if not isinstance(calls, list) or not calls:
                return None, "the reply had neither tool_calls nor reply"
            results = []
            for call in calls[:4]:
                if not isinstance(call, dict):
                    continue
                if ctx.remaining <= 0:
                    results.append({"name": call.get("name"), "ok": False,
                                    "error": "tool_budget_reached: reply now"})
                    continue
                o = ctx.call(str(call.get("name")), call.get("args"))
                results.append({"name": o.name, "ok": o.ok, "error": o.error,
                                "result": o.data if o.ok else None})
            messages.append(Message("user", TOOL_RESULTS + "\n" + _fit(
                results, cfg.result_chars)))
        return None, "no reply within the step limit"

    # ------------------------------------------------------------------
    def _build(self, payload: dict, ctx: TurnContext, user_text: str, r: Route
               ) -> tuple[AssistantReply, list[str]]:
        problems: list[str] = []
        text = str(payload.get("text") or "").strip()
        if not text:
            problems.append("the reply text is empty")
        show = [k for k in payload.get("show_options") or [] if isinstance(k, str)][:10]
        replaces = payload.get("replaces") if isinstance(payload.get("replaces"), str) else None
        if replaces and replaces not in ctx.facts.planned:
            problems.append(f"replaces {replaces}, which is not in the plan get_plan returned")
            replaces = None
        raw = payload.get("propose") or []
        raw = [raw] if isinstance(raw, dict) else [p for p in raw if isinstance(p, dict)]
        proposals: list[ProposalCard] = []
        proposal_courses: list[str] = []
        for p in raw[:3]:
            try:
                change = PlanChange.model_validate(p)
            except ValidationError as exc:
                problems.append(f"an invalid proposed change: {exc.errors()[0]['msg']}")
                continue
            dumped = change.model_dump(mode="json", exclude_none=True,
                                       exclude_defaults=True)
            proposal_courses += [c for c in (change.course, change.replacement) if c]
            preview = self._preview_for(ctx, dumped)
            if preview is None:
                problems.append("the proposed change could not be previewed")
                continue
            if not preview.get("valid"):
                problems.append("proposes a change the preview refused: "
                                + "; ".join(preview.get("errors") or [])[:300])
                continue
            names = {k: f.title for k, f in ctx.facts.courses.items()}
            proposals.append(proposal_card(preview, title=change_title(dumped, names)))
        pref = payload.get("propose_preference")
        if isinstance(pref, dict) and pref:
            out = ctx.last("update_planning_preference")
            if out is None:
                try:
                    out = ctx.call("update_planning_preference", {"preference": pref})
                except ToolBudgetExceeded:
                    out = None
            if out is not None and out.ok:
                proposals.append(ProposalCard(
                    title="Update your planning preferences", change={"kind": "preference",
                                                                      **out.data["after"]},
                    preview_id="", summary=out.data.get("summary", ""), diff={},
                    action={"kind": "confirm_preference", "preference": out.args["preference"]}))
            else:
                problems.append("the preference change could not be previewed")
        problems += check_reply(text, ctx.facts, user_text=user_text,
                                gpa_requested=r.asks_gpa, show_options=show,
                                proposal_courses=proposal_courses)
        term = ctx.facts.planned.get(replaces) if replaces else None
        known = extract_known(user_text)
        cards = [option_card(ctx.items[k], term_code=term or _item_term(ctx.items[k]),
                             replaces=replaces, wanted_format=known.format,
                             avoid_days=known.avoid_days)
                 for k in show if k in ctx.items]
        if not replaces:
            for c in cards:
                c.action = None
        return AssistantReply(text=text, intent=r.intent, cards=cards, proposals=proposals,
                              generated_by="model"), problems

    def _preview_for(self, ctx: TurnContext, change: dict) -> dict | None:
        for o in reversed(ctx.outcomes):
            if o.name == "preview_plan_change" and o.ok:
                previewed = {k: v for k, v in (o.data.get("change") or {}).items()
                             if v not in (None, [], "")}
                if all(previewed.get(k) == v for k, v in change.items()):
                    return o.data
        try:
            out = ctx.call("preview_plan_change", {"change": change})
        except ToolBudgetExceeded:
            return None
        return out.data if out.ok else None

    def _template(self, ctx: TurnContext, r: Route) -> AssistantReply:
        """The fallback: only what tools returned, phrased by code."""
        names = {k: f.title for k, f in ctx.facts.courses.items()}
        valid = [o.data for o in ctx.outcomes
                 if o.name == "preview_plan_change" and o.ok and o.data.get("valid")]
        refused = [o.data for o in ctx.outcomes
                   if o.name == "preview_plan_change" and o.ok and not o.data.get("valid")]
        if valid:
            return AssistantReply(
                text="Here's what that change would do. Nothing changes until you confirm.",
                intent=r.intent, generated_by="template",
                proposals=[proposal_card(p, title=change_title(p.get("change") or {}, names))
                           for p in valid[:3]])
        if refused:
            errors = "; ".join(e.split(":", 1)[-1].strip() for p in refused
                               for e in p.get("errors") or [])
            return AssistantReply(text=f"That change can't be made: {errors}",
                                  intent=r.intent, generated_by="template")
        if ctx.items:
            cards = [option_card(i, term_code=_item_term(i), replaces=None)
                     for i in list(ctx.items.values())[:8]]
            for c in cards:
                c.action = None
            return AssistantReply(text="Here are the courses I found.", intent=r.intent,
                                  generated_by="template", cards=cards)
        return AssistantReply(
            text=("I couldn't put together an answer I could check against your records, so I "
                  "won't guess. Try asking about one course or one semester, or open your Plan "
                  "page."), intent=r.intent, generated_by="template", suggestions=SUGGESTIONS)


__all__ = ["SUGGESTIONS", "AgentConfig", "Assistant"]
