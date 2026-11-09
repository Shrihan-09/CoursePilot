"""Run and score the golden conversations (Prompt 11 Part B, B4).

Each conversation is scored on five criteria, every turn:

    grounded        the reply passes the grounding gate on its own turn's facts,
                    and no course key outside the synthetic catalog appears
                    (one the student typed may be repeated)
    tools           expected tools called, forbidden ones not, the tool cap held,
                    escalation as expected (escalation mode only)
    proposal_valid  proposals / cards / questions / sections as expected, every
                    card a valid option, every section clear of blocked times
    no_auto_apply   the plan changes only on a tap, and only as expected
    labels          card offering labels equal the catalog's, expected label
                    text present

A conversation passes when every criterion holds on every turn. Two rates
are reported separately because the prompt requires 100% on them:
no auto-apply and no invented courses.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from app.llm.base import LLMProvider
from app.services.assistant import actions
from app.services.assistant.agent import AgentConfig, Assistant
from app.services.assistant.contracts import COURSE_KEY, AssistantReply
from app.services.assistant.eval.golden import CASES
from app.services.assistant.eval.world import CATALOG, SyntheticTools
from app.services.assistant.grounding import check_reply
from app.services.assistant.pricing import cost_usd

CRITERIA = ("grounded", "tools", "proposal_valid", "no_auto_apply", "labels")
ProviderFactory = Callable[[dict], tuple[LLMProvider, LLMProvider | None]]


@dataclass
class CaseResult:
    id: str
    category: str
    student: str
    criteria: dict[str, bool] = field(default_factory=lambda: dict.fromkeys(CRITERIA, True))
    notes: list[str] = field(default_factory=list)
    invented: bool = False
    auto_applied: bool = False
    by_model: dict[str, list[int]] = field(default_factory=dict)
    escalated_turns: int = 0
    rejections: int = 0
    transcript: list[dict] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(self.criteria.values())

    def fail(self, criterion: str, note: str) -> None:
        self.criteria[criterion] = False
        self.notes.append(f"{criterion}: {note}")

    @property
    def tokens(self) -> tuple[int, int]:
        return (sum(v[0] for v in self.by_model.values()),
                sum(v[2] for v in self.by_model.values()))


def _action(spec: str, last: AssistantReply | None) -> dict | None:
    if last is None:
        return None
    kind, _, arg = spec.partition(":")
    if kind == "choose":
        cards = last.cards
        card = cards[0] if arg == "first" and cards else next(
            (c for c in cards if c.course == arg), None)
        return card.action if card else None
    if kind == "confirm":
        i = int(arg or 0)
        return last.proposals[i].action if i < len(last.proposals) else None
    return None


def _keys(reply: AssistantReply) -> set[str]:
    keys = set(COURSE_KEY.findall(reply.text))
    keys |= {c.course for c in reply.cards} | {s.course for s in reply.sections}
    for p in reply.proposals:
        keys |= {v for v in p.change.values() if isinstance(v, str) and COURSE_KEY.fullmatch(v)}
    return keys


def _online(delivery: list[str]) -> bool:
    return bool({"online", "async"} & set(delivery))


def _blocked(tools: SyntheticTools) -> list[tuple[str, int, int]]:
    prefs = tools.profile["preferences"]
    return [(d, int(b["start"][:2]) * 60 + int(b["start"][3:]),
             int(b["end"][:2]) * 60 + int(b["end"][3:]))
            for b in prefs.get("blocked_times") or [] for d in b["days"]]


def _score(res: CaseResult, turn: dict, reply: AssistantReply, *, tools: SyntheticTools,
           result, rev_before: int, user_text: str, escalation_mode: bool,
           max_tool_calls: int, cards_valid: dict[str, bool]) -> None:
    e = turn.get("expect") or {}
    text_low = reply.text.lower()
    is_action = "action" in turn

    # grounded
    if result is not None:
        problems = check_reply(reply.text, result.facts, user_text=user_text,
                               gpa_requested=result.route.asks_gpa) \
            if reply.generated_by == "model" else []
        if problems:
            res.fail("grounded", "; ".join(problems))
    invented = sorted(k for k in _keys(reply) if k not in CATALOG and k not in user_text)
    if invented:
        res.invented = True
        res.fail("grounded", f"invented courses {invented}")
    for x in e.get("excludes") or []:
        if x.lower() in text_low:
            res.fail("grounded", f"reply contains {x!r}")
    if e.get("rejections_min") and result is not None and \
            len(result.grounding_failures) < e["rejections_min"]:
        res.fail("grounded", "the gate did not reject the scripted reply")

    # tools
    if result is not None:
        called = [c["name"] for c in result.tool_calls if c.get("ok")]
        for t in e.get("tools_all") or []:
            if t not in called:
                res.fail("tools", f"{t} not called")
        if e.get("tools_any") and not set(e["tools_any"]) & set(called):
            res.fail("tools", f"none of {e['tools_any']} called")
        for t in e.get("tools_none") or []:
            if t in called:
                res.fail("tools", f"{t} called")
        if len(result.tool_calls) > max_tool_calls:
            res.fail("tools", f"{len(result.tool_calls)} tool calls > cap")
        if escalation_mode and "escalated" in e and result.escalated != e["escalated"]:
            res.fail("tools", f"escalated={result.escalated}, expected {e['escalated']}")

    # proposal_valid
    n = len(reply.proposals)
    if "proposals" in e and n != e["proposals"]:
        res.fail("proposal_valid", f"{n} proposals, expected {e['proposals']}")
    if n < e.get("proposals_min", 0):
        res.fail("proposal_valid", f"{n} proposals, expected at least {e['proposals_min']}")
    kinds = [p.change.get("kind") for p in reply.proposals]
    for k in e.get("proposal_kinds") or []:
        if k not in kinds:
            res.fail("proposal_valid", f"no {k} proposal")
    for p in reply.proposals:
        if p.preview_id and p.preview_id not in tools.previews:
            res.fail("proposal_valid", f"proposal {p.title!r} has no live preview")
    if len(reply.cards) < e.get("cards_min", 0):
        res.fail("proposal_valid", f"{len(reply.cards)} cards, expected {e['cards_min']}+")
    for c in reply.cards:
        if not cards_valid.get(c.course, False):
            res.fail("proposal_valid", f"card {c.course} is not a valid option")
        if e.get("cards_online") and not _online(c.delivery):
            res.fail("proposal_valid", f"card {c.course} is not online")
        if e.get("card_codes") and e["card_codes"] not in c.requirement_codes:
            res.fail("proposal_valid", f"card {c.course} does not fill {e['card_codes']}")
        if c.description and CATALOG[c.course]["description"] and \
                not CATALOG[c.course]["description"].startswith(c.description.rstrip("…")):
            res.fail("proposal_valid", f"card {c.course} description is not the catalog's")
    if "questions" in e and [q.id for q in reply.questions] != e["questions"]:
        res.fail("proposal_valid", f"questions {[q.id for q in reply.questions]}, expected "
                                   f"{e['questions']}")
    if e.get("refusal") and reply.generated_by != "router":
        res.fail("proposal_valid", "not refused")
    if "second_tap" in e and any(p.reason == "second_tap" for p in reply.proposals) != \
            e["second_tap"]:
        res.fail("proposal_valid", "second tap expectation")
    if e.get("refreshed") and not any(p.reason == "refreshed" for p in reply.proposals):
        res.fail("proposal_valid", "stale proposal was not refreshed")
    if len(reply.sections) < e.get("sections_min", 0):
        res.fail("proposal_valid", f"{len(reply.sections)} sections")
    blocks = _blocked(tools)
    for s in reply.sections:
        raw = next((x for x in CATALOG[s.course]["sections"]
                    if x["index_number"] == s.index_number), None)
        for m in (raw or {}).get("meetings") or []:
            if any(m["day"] == d and m["start_minute"] < end and start < m["end_minute"]
                   for d, start, end in blocks):
                res.fail("proposal_valid", f"section {s.index_number} overlaps a blocked time")
    if e.get("webreg_term") and reply.webreg_term != e["webreg_term"]:
        res.fail("proposal_valid", "WebReg panel not offered")

    # no_auto_apply
    changed = tools.plan_revision() != rev_before
    if not is_action and (changed or reply.undo_available):
        res.auto_applied = True
        res.fail("no_auto_apply", "the plan changed without a tap")
    if is_action and "applied" in e and changed != e["applied"]:
        res.fail("no_auto_apply", f"applied={changed}, expected {e['applied']}")
    if not is_action and e.get("applied") is True:
        res.fail("no_auto_apply", "expected an apply on a non-action turn")

    # labels
    for c in reply.cards:
        want = CATALOG[c.course]["offered"].get(c.term_code or "")
        if c.offering != want:
            res.fail("labels", f"{c.course} offering {c.offering!r} != {want!r}")
    for x in e.get("includes") or []:
        if x.lower() not in text_low:
            res.fail("labels", f"reply lacks {x!r}")


async def run_case(case: dict, make: ProviderFactory, *, escalation_mode: bool,
                   config: AgentConfig | None = None) -> CaseResult:
    tools = SyntheticTools(case["student"])
    default, escalation = make(case)
    cfg = config or AgentConfig()
    cfg.escalation_enabled = escalation_mode
    assistant = Assistant(tools, default=default,
                          escalation=escalation if escalation_mode else None, config=cfg)
    res = CaseResult(case["id"], case["category"], case["student"])
    history: list[dict] = []
    flow: dict | None = None
    last: AssistantReply | None = None
    for turn in case["turns"]:
        rev = tools.plan_revision()
        user_text = turn.get("user") or ""
        result = None
        if "action" in turn:
            action = _action(turn["action"], last)
            if action is None:
                res.fail("proposal_valid", f"no card for {turn['action']}")
                break
            reply = actions.execute(tools, action)
        else:
            result = await assistant.turn(user_text, history=history, flow=flow,
                                          answers=turn.get("answers"))
            reply = result.reply
            for model, row in result.usage.by_model.items():
                acc = res.by_model.setdefault(model, [0, 0, 0])
                for i in range(3):
                    acc[i] += row[i]
            res.escalated_turns += int(result.escalated)
            res.rejections += len(result.grounding_failures)
        cards_valid = {c.course: tools.valid_option(c.course, c.term_code or "",
                                                    ignore=c.replaces) or
                       (c.course in tools._where() and "action" in turn) for c in reply.cards}
        _score(res, turn, reply, tools=tools, result=result, rev_before=rev,
               user_text=user_text, escalation_mode=escalation_mode,
               max_tool_calls=cfg.max_tool_calls, cards_valid=cards_valid)
        res.transcript.append(_transcript(turn, reply, result))
        if user_text:
            history.append({"role": "user", "content": user_text})
        history.append({"role": "assistant", "content": reply.text})
        flow = reply.flow if result is not None else None
        # Taps act on the latest reply that SHOWED cards or proposals (a
        # "Done." reply after a tap does not hide the earlier deck).
        if reply.cards or reply.proposals or last is None:
            last = reply
    return res


def _transcript(turn: dict, reply: AssistantReply, result) -> dict:
    return {
        "student": turn.get("user") or (f"[tap {turn['action']}]" if "action" in turn
                                        else f"[chips {turn.get('answers')}]"),
        "assistant": reply.text,
        "generated_by": reply.generated_by,
        "activity": [a.label for a in reply.activity],
        "questions": [{"prompt": q.prompt, "chips": [c.label for c in q.chips]}
                      for q in reply.questions],
        "cards": [{"course": c.course, "title": c.title, "why": c.why, "delivery": c.delivery,
                   "offering": c.offering, "description": c.description} for c in reply.cards],
        "proposals": [{"title": p.title, "summary": p.summary, "big": p.big,
                       "reason": p.reason} for p in reply.proposals],
        "sections": [{"index": s.index_number, "meetings": s.meetings,
                      "warnings": s.warnings} for s in reply.sections],
        "escalated": bool(result and result.escalated),
        "rejections": [p for p in (result.grounding_failures if result else [])],
    }


def summarize(results: list[CaseResult], *, prices: dict | None = None,
              alias: dict[str, str] | None = None) -> dict:
    n = len(results) or 1
    costs = [cost_usd(r.by_model, prices, alias) for r in results]
    known = [c for c in costs if c is not None]
    tin = [r.tokens[0] for r in results]
    tout = [r.tokens[1] for r in results]
    return {
        "conversations": len(results),
        "passed": sum(r.passed for r in results),
        "pass_rate": round(sum(r.passed for r in results) / n, 4),
        "criteria": {c: round(sum(r.criteria[c] for r in results) / n, 4) for c in CRITERIA},
        "no_auto_apply_rate": round(1 - sum(r.auto_applied for r in results) / n, 4),
        "no_invented_courses_rate": round(1 - sum(r.invented for r in results) / n, 4),
        "avg_input_tokens": round(sum(tin) / n, 1),
        "avg_output_tokens": round(sum(tout) / n, 1),
        "avg_cost_usd": round(sum(known) / len(known), 6) if known else None,
        "total_cost_usd": round(sum(known), 6) if known else None,
        "conversations_escalated": sum(1 for r in results if r.escalated_turns),
        "gate_rejections": sum(r.rejections for r in results),
        "failed": [{"id": r.id, "notes": r.notes} for r in results if not r.passed],
    }


async def run_all(make: ProviderFactory, *, escalation_mode: bool,
                  ids: set[str] | None = None, config_factory=None) -> list[CaseResult]:
    out = []
    for case in CASES:
        if ids and case["id"] not in ids:
            continue
        out.append(await run_case(case, make, escalation_mode=escalation_mode,
                                  config=config_factory() if config_factory else None))
    return out


def fake_factory(case: dict) -> tuple[LLMProvider, LLMProvider]:
    from app.services.assistant.fake import FakeAssistantProvider

    return (FakeAssistantProvider("fake-assistant", script=case.get("script")),
            FakeAssistantProvider("fake-assistant_escalation"))


def markdown(results: list[CaseResult], ids: list[str]) -> str:
    lines = []
    for r in results:
        if r.id not in ids:
            continue
        lines.append(f"### {r.id} ({r.category}, {r.student}) - "
                     f"{'PASS' if r.passed else 'FAIL'}")
        for t in r.transcript:
            lines.append(f"**Student:** {t['student']}")
            if t["activity"]:
                lines.append(f"_{' · '.join(t['activity'])}_")
            lines.append(f"**Assistant ({t['generated_by']}"
                         f"{', escalated' if t['escalated'] else ''}):** {t['assistant']}")
            for q in t["questions"]:
                lines.append(f"- {q['prompt']} " + " ".join(f"[{c}]" for c in q["chips"]))
            for c in t["cards"]:
                lines.append(f"- card {c['course']} {c['title']} · {' · '.join(c['why'])} · "
                             f"{', '.join(c['delivery'])} · {c['offering']}"
                             + (f" — {c['description']}" if c["description"] else ""))
            for p in t["proposals"]:
                lines.append(f"- proposal: {p['title']} — {p['summary']}"
                             f"{' (one more tap)' if p['reason'] == 'second_tap' else ''}")
            for s in t["sections"]:
                lines.append(f"- section #{s['index']} {', '.join(s['meetings'])} "
                             f"{' '.join(s['warnings'])}")
            if t["rejections"]:
                lines.append(f"- gate rejected {len(t['rejections'])} draft(s): "
                             + re.sub(r"\s+", " ", "; ".join(t["rejections"][0]))[:200])
            lines.append("")
    return "\n".join(lines)


__all__ = ["CRITERIA", "CaseResult", "fake_factory", "markdown", "run_all", "run_case",
           "summarize"]
