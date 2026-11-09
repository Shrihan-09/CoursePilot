"""Deterministic offline stand-in for a model (Prompt 11 Part B: "fake provider in CI").

It speaks the same JSON protocol as a real model - tool calls, then a reply -
so CI exercises the whole pipeline (router, tool layer, caps, grounding gate,
cards, tokens) with no network and no key. Its replies are built only from
the tool results it was sent, the way a well-behaved model should answer.

`script` makes it misbehave on purpose: each scripted string is returned
verbatim before the policy runs. The eval uses that to check the gate - an
invented course, an "I've updated your plan", a GPA nobody asked about - is
rejected and regenerated, not shown.

It is NOT a model, and nothing about real model quality can be measured with
it. The real-provider eval run does that.
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.llm.base import Completion, Message, Role, ToolCall, Usage
from app.services.assistant.contracts import COURSE_KEY, term_label
from app.services.assistant.matching import plan_courses, resolve
from app.services.assistant.prompts import EXPAND_SYSTEM, REJECTED, STUDENT, TOOL_RESULTS

EXPANSIONS = {
    "tech": ["computer", "computing", "technology", "digital", "data", "information",
             "software"],
    "technology": ["computer", "computing", "technology", "digital", "data", "information",
                   "software"],
    "ai": ["artificial", "intelligence", "machine", "learning", "data", "computing"],
    "art": ["art", "visual", "design", "painting", "museum"],
    "music": ["music", "sound", "song", "jazz", "composition"],
    "health": ["health", "medicine", "public", "nutrition", "disease"],
    "environment": ["environment", "climate", "ecology", "sustainability", "energy"],
    "money": ["economics", "finance", "money", "markets", "business"],
    "games": ["games", "game", "play", "media", "digital"],
}
_SEASON = {"spring": "1", "fall": "9", "summer": "7", "winter": "0"}


def _tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _keywords(topic: str) -> list[str]:
    out: list[str] = []
    for w in re.findall(r"[a-z]+", topic.lower()):
        out += EXPANSIONS.get(w, [w])
    return list(dict.fromkeys(out))[:8]


class FakeAssistantProvider:
    """`native=True` (the default) mirrors the OpenAI Responses path: tools
    arrive as function definitions and the policy's choices come back as
    function calls, the answer as a `reply` call. `native=False` speaks the
    JSON-in-text protocol used for providers without native tools."""

    name = "fake"

    def __init__(self, model: str = "fake-assistant", script: list[str] | None = None, *,
                 native: bool = True) -> None:
        self._model = model
        self.script = list(script or [])
        self.calls = 0
        self.supports_tools = native
        self._ids = 0

    async def complete(self, *, role: Role, messages: list[Message], system: str | None = None,
                       schema: dict | None = None, max_tokens: int = 16_000,
                       tools: list | None = None, tool_choice: str | None = None) -> Completion:
        self.calls += 1
        if system == EXPAND_SYSTEM:
            text = json.dumps({"keywords": _keywords(messages[-1].content)})
        elif self.script:
            text = self.script.pop(0)
        else:
            text = json.dumps(policy(messages), default=str)
        prompt = (system or "") + "".join(m.content for m in messages)
        if tools is not None:
            prompt += json.dumps([t.parameters for t in tools])
            return self._native(text, prompt)
        return Completion(text=text, model=self._model, provider=self.name,
                          usage=Usage(input_tokens=_tokens(prompt), output_tokens=_tokens(text)),
                          structured=None, stop_reason="end_turn")

    def _native(self, text: str, prompt: str) -> Completion:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = None
        calls: list[ToolCall] = []
        if isinstance(payload, dict) and isinstance(payload.get("reply"), dict):
            calls.append(self._call("reply", payload["reply"]))
        elif isinstance(payload, dict) and isinstance(payload.get("tool_calls"), list):
            calls += [self._call(str(c.get("name")), c.get("args") or {})
                      for c in payload["tool_calls"] if isinstance(c, dict)]
        # Anything else is plain text with no call - a protocol violation the
        # agent must reject.
        return Completion(text="" if calls else text, model=self._model, provider=self.name,
                          usage=Usage(input_tokens=_tokens(prompt), output_tokens=_tokens(text)),
                          stop_reason="completed", tool_calls=tuple(calls))

    def _call(self, name: str, args: dict) -> ToolCall:
        self._ids += 1
        raw = json.dumps(args, default=str)
        return ToolCall(id=f"call_{self._ids}", name=name, arguments=json.loads(raw),
                        raw_arguments=raw)


# --------------------------------------------------------------------------
# The policy
# --------------------------------------------------------------------------


def _state(messages: list[Message]) -> tuple[str, dict[str, list[dict]]]:
    """The current student message and every tool result since it."""
    start = max(i for i, m in enumerate(messages)
                if m.role == "user" and m.content.startswith(f"{STUDENT}: "))
    text = messages[start].content[len(STUDENT) + 2:]
    results: dict[str, list[dict]] = {}
    for m in messages[start + 1:]:
        if m.role == "user" and m.content.startswith(TOOL_RESULTS):
            for r in json.loads(m.content[len(TOOL_RESULTS) + 1:]):
                results.setdefault(r["name"], []).append(r)
        elif m.role == "tool" and not m.content.startswith(REJECTED):
            r = json.loads(m.content)
            if isinstance(r, dict) and r.get("name"):
                results.setdefault(r["name"], []).append(r)
    return text, results


def _ok(results: dict, name: str) -> list[dict]:
    return [r["result"] for r in results.get(name, []) if r.get("ok")]


def _calls(*calls: tuple[str, dict]) -> dict:
    return {"tool_calls": [{"name": n, "args": a} for n, a in calls]}


def _reply(text: str, **extra: Any) -> dict:
    return {"reply": {"text": text, **extra}}


def _name(c: dict) -> str:
    return f"{c['course']} {c.get('title', '')}".strip()


def _term_for(text: str, plan: dict, *, after: str | None = None) -> str | None:
    terms = [t["term_code"] for t in plan.get("terms") or []]
    low = text.lower()
    for word, digit in _SEASON.items():
        if re.search(rf"\b{word}\b", low):
            year = re.search(rf"\b{word} (\d{{4}})\b", low)
            for t in terms:
                if t[4] == digit and (not year or t[:4] == year.group(1)) and (
                        after is None or t != after):
                    return t
    if re.search(r"\bnext (semester|term)\b|\bthis (semester|term)\b", low) and terms:
        return terms[0]
    return None


def _clauses(text: str) -> list[str]:
    parts = re.split(r"\b(?:and then|and also|also|then|plus|and)\b|[;.]", text, flags=re.I)
    return [p.strip() for p in parts if p and p.strip()]


def _change_for(clause: str, plan: dict) -> dict | str | None:
    low = clause.lower()
    courses = plan_courses(plan)
    keys = COURSE_KEY.findall(clause)
    if re.search(r"\blighter|lighten|fewer credits|less work\b", low):
        term = _term_for(clause, plan) or (plan["terms"][0]["term_code"] if plan.get("terms")
                                           else None)
        return {"kind": "lighten_term", "term_code": term} if term else None
    if re.search(r"\bswap|replace\b", low) and len(keys) >= 2:
        hit = resolve(keys[0], courses)
        return {"kind": "swap", "course": keys[0], "term_code": hit[0]["term_code"],
                "replacement": keys[1]} if hit else f"{keys[0]} is not in your plan."
    if re.search(r"\bmove|push\b", low):
        target = re.split(r"\bto\b", clause, maxsplit=1)
        hit = resolve(target[0], courses)
        if len(hit) != 1:
            return None
        to = _term_for(target[1] if len(target) > 1 else clause, plan,
                       after=hit[0]["term_code"])
        if to is None:
            m = re.search(r"\b(spring|fall|summer|winter) (\d{4})\b", low)
            if m:
                to = m.group(2) + _SEASON[m.group(1)]
        return {"kind": "move", "course": hit[0]["course"], "term_code": hit[0]["term_code"],
                "to_term": to} if to else None
    if re.search(r"\bremove|drop\b", low):
        hit = resolve(clause, courses)
        return {"kind": "remove", "course": hit[0]["course"],
                "term_code": hit[0]["term_code"]} if len(hit) == 1 else None
    if re.search(r"\badd\b", low) and keys:
        term = _term_for(clause, plan) or (plan["terms"][0]["term_code"] if plan.get("terms")
                                           else None)
        return {"kind": "add", "course": keys[0], "term_code": term} if term else None
    if re.search(r"\b(un)?lock\b", low):
        hit = resolve(clause, courses)
        kind = "unlock" if "unlock" in low else "lock"
        return {"kind": kind, "course": hit[0]["course"],
                "term_code": hit[0]["term_code"]} if len(hit) == 1 else None
    return None


def policy(messages: list[Message]) -> dict:
    text, results = _state(messages)
    low = text.lower()
    plan = (_ok(results, "get_plan") or [None])[-1]
    keys = COURSE_KEY.findall(text)

    # Preferences: "no Fridays from now on".
    if re.search(r"\bfrom now on|always|i prefer|my preferences?\b", low):
        prefs = _ok(results, "update_planning_preference")
        if not prefs:
            pref: dict = {}
            days = {"monday": "M", "tuesday": "T", "wednesday": "W", "thursday": "H",
                    "friday": "F"}
            avoid = [c for d, c in days.items() if re.search(rf"\b(no|avoid) {d}s?\b", low)]
            if avoid:
                pref["avoid_days"] = avoid
            if "online" in low:
                pref["delivery"] = "online"
            if re.search(r"\bno mornings?\b", low):
                pref["earliest_start"] = "12:00"
            if not pref:
                return _reply("Which preference would you like to change - format, days, "
                              "or times?")
            return _calls(("update_planning_preference", {"preference": pref}))
        return _reply("Here's that preference change. It's saved only if you confirm.",
                      propose_preference=prefs[-1]["after"])

    # Prerequisite / course-detail questions.
    if re.search(r"\bprereq|prerequisite|tell me about|what is\b|\bdescription\b", low) and (
            keys or "prereq" in low):
        if keys:
            details = _ok(results, "get_course_details")
            errors = [r for r in results.get("get_course_details", []) if not r.get("ok")]
            if not details and not errors:
                return _calls(("get_course_details", {"course": keys[0]}))
            if errors:
                return _reply(f"I couldn't find {keys[0]} in the Rutgers catalog data "
                              "CoursePilot has, so I can't tell you about it.")
            d = details[-1]
            pre = d.get("prerequisite_text") or "none published"
            return _reply(f"{_name(d)}: the published prerequisite text is \"{pre}\".")
        if plan is None:
            return _calls(("get_plan", {}))
        hit = resolve(text, plan_courses(plan))
        if len(hit) == 1:
            details = _ok(results, "get_course_details")
            if not details:
                return _calls(("get_course_details", {"course": hit[0]["course"]}))
            d = details[-1]
            pre = d.get("prerequisite_text") or "none published"
            return _reply(f"{_name(d)}: the published prerequisite text is \"{pre}\".")
        return _reply("Which course do you mean? Give me its number, like 01:198:111.")

    # Named-course lookups ("tell me about Advanced Dragon Studies").
    if re.search(r"\btell me about|is there a course|do you have a course|what about the "
                 r"course\b", low):
        found = _ok(results, "search_courses")
        if not found:
            topic = re.sub(r"^.*?\b(about|course|called|named)\b", "", text, flags=re.I)
            return _calls(("search_courses", {"text": text[:300], "topic": topic.strip()[:120]
                                              or None}))
        hits = found[-1].get("results") or []
        if not hits:
            return _reply("I couldn't find a course like that in the catalog data CoursePilot "
                          "has, so I won't describe one.")
        return _reply(f"Here {'is' if len(hits) == 1 else 'are'} the closest match"
                      f"{'' if len(hits) == 1 else 'es'} I found.",
                      show_options=[h["course"] for h in hits[:6]])

    # Everything else needs the plan first.
    if plan is None:
        return _calls(("get_plan", {}))
    courses = plan_courses(plan)

    # Plan changes - one per clause.
    if re.search(r"\b(lighter|lighten|move|push|swap|replace|remove|drop|add|lock|unlock|"
                 r"fewer credits|less work)\b", low):
        changes, notes = [], []
        for clause in _clauses(text):
            c = _change_for(clause, plan)
            if isinstance(c, dict):
                changes.append(c)
            elif isinstance(c, str):
                notes.append(c)
        previews = _ok(results, "preview_plan_change")
        if changes and not previews:
            return _calls(*(("preview_plan_change", {"change": c}) for c in changes[:3]))
        if not changes:
            return _reply(" ".join(notes) or "Which course and semester do you mean? For "
                          "example: \"Move 01:750:203 to Fall 2027\".")
        valid = [p for p in previews if p.get("valid")]
        refused = [p for p in previews if not p.get("valid")]
        parts = []
        for p in refused:
            why = "; ".join(e.split(":", 1)[-1].strip() for e in p.get("errors") or [])
            parts.append(f"That change can't be made: {why}.")
        if valid:
            parts.append("Here's what " + ("these changes" if len(valid) > 1 else "that change")
                         + " would do. Nothing changes until you confirm.")
        return _reply(" ".join(parts), propose=[p["change"] for p in valid])

    # "Why is CS 206 in Fall 2027?"
    if re.search(r"\bwhy\b", low):
        hit = resolve(text, courses)
        if len(hit) != 1:
            return _reply("Which course do you mean? Give me its number, like 01:198:206.")
        placed = _ok(results, "explain_course_placement")
        if not placed:
            return _calls(("explain_course_placement", {"course": hit[0]["course"],
                                                        "term_code": hit[0]["term_code"]}))
        p = placed[-1]
        reasons = "; ".join(p.get("reasons") or []) or "the planner placed it there"
        when = term_label(p.get("term_code")) or p.get("term_code")
        return _reply(f"{_name(p)} is in {when} because: {reasons}. Its offering label is "
                      f"\"{p.get('offering')}\".")

    # "What happens if I fail Calc II again?"
    if re.search(r"\bfail|don'?t pass|not pass\b", low):
        hit = resolve(text, courses)
        if len(hit) != 1:
            return _reply("Which course do you mean?")
        placed = _ok(results, "explain_course_placement")
        if not placed:
            return _calls(("explain_course_placement", {"course": hit[0]["course"],
                                                        "term_code": hit[0]["term_code"]}))
        p = placed[-1]
        unlocks = ", ".join(p.get("unlocks") or [])
        when = term_label(p.get("term_code")) or p.get("term_code")
        tail = (f" The courses it unlocks ({unlocks}) would have to wait." if unlocks else "")
        return _reply(f"Your plan has {_name(p)} in {when}. If you don't pass it, add the "
                      f"result to your record and rebuild your plan.{tail}")

    # "Which of my options are online?" / "Show online options".
    if re.search(r"\bonline|async|remote\b", low):
        found = _ok(results, "search_courses")
        if not found:
            term = _term_for(text, plan) or plan["terms"][0]["term_code"]
            return _calls(("search_courses", {"text": "online", "term_code": term,
                                              "filters": {"online": True}, "limit": 8}))
        hits = found[-1].get("results") or []
        term = found[-1].get("interpretation", {}).get("term_code")
        if not hits:
            return _reply(f"I didn't find valid online courses for {term_label(term)}.")
        return _reply(f"Here are {len(hits)} online options for {term_label(term)}.",
                      show_options=[h["course"] for h in hits[:8]])

    # "Is my writing requirement done?" - status labels verbatim.
    if re.search(r"\b(done|complete|completed|finished|counted|satisfied)\b", low) and \
            re.search(r"\brequirement|core|writing|history|arts|science\b", low):
        progress = _ok(results, "get_progress")
        if not progress:
            return _calls(("get_progress", {"include_gpa": False}))
        p = progress[-1]
        words = {w for w in re.findall(r"[a-z]{4,}", low)} - {"requirement", "done", "complete",
                                                              "completed", "finished"}
        reqs = [r for r in p.get("requirements") or []
                if words & set(re.findall(r"[a-z]{4,}", r["name"].lower()))]
        if not reqs:
            return _reply("I couldn't find that requirement in your progress.")
        lines = []
        for r in reqs[:3]:
            label = r.get("label_text") or r["status"].replace("_", " ")
            note = f" ({r['note']})" if r.get("note") else ""
            lines.append(f"{r['name']} ({r['code']}): {label}{note}")
        return _reply(f"Per your progress ({p.get('source_label')}): " + "; ".join(lines) + ".")

    # "What do I still need for Core?"
    if re.search(r"\bcore|still need|left|remaining|requirements?\b", low):
        progress = _ok(results, "get_progress")
        if not progress:
            return _calls(("get_progress", {"include_gpa": False}))
        p = progress[-1]
        reqs = [r for r in p.get("requirements") or [] if r.get("status") == "open"]
        if "core" in low:
            reqs = [r for r in reqs if r.get("system") == "core"] or reqs
        if not reqs:
            return _reply(f"Your progress ({p.get('source_label')}) shows nothing open there.")
        listed = ", ".join(f"{r['name']} ({r['code']})" for r in reqs[:8])
        return _reply(f"Still open, per your progress ({p.get('source_label')}): {listed}.")

    # GPA (only when asked - the tool layer enforces it too).
    if re.search(r"\bgpa\b", low):
        progress = _ok(results, "get_progress")
        if not progress:
            return _calls(("get_progress", {"include_gpa": True}))
        gpa = progress[-1].get("gpa")
        return _reply(f"Your recorded GPA is {gpa}." if gpa else
                      "I don't have a GPA on your record.")

    if re.search(r"\bgraduat", low):
        return _reply(f"Your plan's graduation term is {plan.get('graduation')}.")

    when = term_label(plan["terms"][0]["term_code"]) if plan.get("terms") else None
    return _reply(f"Your plan has {len(plan.get('terms') or [])} semesters"
                  f"{', starting ' + when if when else ''}. Ask me about a course, a semester, "
                  "or what you still need.")


__all__ = ["EXPANSIONS", "FakeAssistantProvider", "policy"]
