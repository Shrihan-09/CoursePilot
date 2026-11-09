"""Guided swap (Prompt 11 Part B, B1b): "I don't want Art History, what's better?"

    1. ask first      1-3 questions with chips; any the message or the saved
                      preferences already answer is skipped
    2. show options   list_course_options / search_courses with the answers ->
                      a carousel of 5-10 cards built from tool data
    3. choose         the card's token; executed by service.execute_action
                      (preview -> apply, or one more tap for a big side effect)
    4. section        find_sections, respecting blocked times
    5. WebReg         the "Ready for WebReg" panel - no automation

The flow is deterministic: which course, which questions, which search, which
cards. A model is used for one thing here - turning a vague topic ("something
about tech") into search keywords, because search is BM25 + aliases and only
matches words it is given.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from decimal import Decimal

from app.services.assistant.cards import option_card
from app.services.assistant.contracts import AssistantReply, Chip, Question, term_label
from app.services.assistant.matching import plan_courses, resolve
from app.services.assistant.router import Known, extract_known
from app.services.assistant.turn import TurnContext

Expand = Callable[[str], Awaitable[list[str]]]
MAX_CARDS = 10

FORMAT_CHIPS = [Chip(label="Async / online", value="online", question="format"),
                Chip(label="Hybrid", value="hybrid", question="format"),
                Chip(label="In person", value="in_person", question="format"),
                Chip(label="No preference", value="none", question="format")]
ELSE_CHIPS = [Chip(label="No Fridays", value="avoid:F", question="else"),
              Chip(label="No mornings", value="earliest:12:00", question="else"),
              Chip(label="Something about tech", value="topic:technology", question="else"),
              Chip(label="Lighter workload", value="lighter", question="else"),
              Chip(label="No, show options", value="nothing", question="else")]


def _name(c: dict) -> str:
    return f"{c['course']} {c.get('title', '')}".strip()


def _saved(profile: dict) -> dict:
    prefs = profile.get("preferences") or {}
    out: dict = {}
    if prefs.get("delivery"):
        out["format"] = prefs["delivery"]
    if prefs.get("avoid_days"):
        out["avoid_days"] = list(prefs["avoid_days"])
    for k in ("earliest", "latest"):
        if prefs.get(k):
            out[k] = prefs[k]
    return out


def _from_known(k: Known) -> dict:
    out: dict = {}
    if k.format:
        out["format"] = k.format
    if k.instead:
        out["instead"] = k.instead
    if k.avoid_days:
        out["avoid_days"] = k.avoid_days
    if k.earliest:
        out["earliest"] = k.earliest
    if k.latest:
        out["latest"] = k.latest
    if k.topic:
        out["topic"] = k.topic
    if k.lighter:
        out["lighter"] = True
    if k.any_constraint():
        out["else_answered"] = True
    return out


def apply_chip(answers: dict, question: str | None, value: str) -> dict:
    a = dict(answers)
    if question == "instead":
        a["instead"] = value
    elif question == "format":
        a["format"] = value
    elif question == "else":
        a["else_answered"] = True
        if value.startswith("avoid:"):
            a["avoid_days"] = sorted({*a.get("avoid_days", []), value[6:]})
        elif value.startswith("earliest:"):
            a["earliest"] = value[9:]
        elif value.startswith("topic:"):
            a["topic"] = value[6:]
        elif value == "lighter":
            a["lighter"] = True
    return a


def _open_requirements(progress: dict, exclude: set[str]) -> list[dict]:
    return [r for r in progress.get("requirements") or []
            if r.get("status") == "open" and r.get("code") not in exclude][:3]


async def start(ctx: TurnContext, text: str, known: Known, *, expand: Expand
          ) -> tuple[AssistantReply, dict]:
    plan = ctx.call("get_plan").data
    profile = ctx.call("get_profile_summary").data
    candidates = plan_courses(plan)
    matches = resolve(text, candidates)
    answers = {**_saved(profile), **_from_known(known)}
    if len(matches) != 1:
        pool = matches or [c for c in candidates if c.get("state") != "locked"][:6]
        chips = [Chip(label=_name(c), value=f"{c['course']}|{c['term_code']}",
                      question="which_course") for c in pool[:6]]
        prompt = ("Which course do you mean?" if matches else
                  "Which planned course would you like to replace?")
        return (AssistantReply(text=prompt, intent="guided_swap", generated_by="template",
                               questions=[Question(id="which_course", prompt=prompt,
                                                   chips=chips)]),
                {"kind": "guided_swap", "stage": "which", "answers": answers})
    return await after_target(ctx, matches[0], answers, expand=expand)


async def after_target(ctx: TurnContext, target: dict, answers: dict, *, expand: Expand
                 ) -> tuple[AssistantReply, dict]:
    course, term = target["course"], target["term_code"]
    options = ctx.call("list_course_options", {"course": course, "term_code": term})
    if not options.ok:
        return (AssistantReply(text=f"I couldn't load options for {_name(target)}.",
                               intent="guided_swap", generated_by="template"), None)
    data = options.data
    title = data.get("title") or target.get("title", "")
    flow = {"kind": "guided_swap", "stage": "ask", "course": course, "term_code": term,
            "title": title, "answers": answers,
            "same": [{"code": g["requirement_code"], "name": g["requirement_name"]}
                     for g in data.get("groups") or [] if g["kind"] == "same_requirement"]}
    if data.get("locked"):
        why = data.get("locked_reason") or "it is locked"
        moves = ", ".join(term_label(t) or t for t in data.get("move_terms") or [])
        extra = f" It can move to {moves}." if moves else ""
        return (AssistantReply(
            text=f"{_name({'course': course, 'title': title})} stays in your plan because "
                 f"{why}.{extra}", intent="guided_swap", generated_by="template"), None)
    progress = ctx.call("get_progress", {"include_gpa": False}).data
    same_codes = {s["code"] for s in flow["same"]}
    open_reqs = _open_requirements(progress, same_codes)
    flow["open"] = [{"code": r["code"], "name": r["name"]} for r in open_reqs]

    questions: list[Question] = []
    if not answers.get("instead"):
        chips = [Chip(label=f"Same requirement ({s['name']})", value="same", question="instead")
                 for s in flow["same"][:1]]
        chips += [Chip(label=r["name"], value=f"req:{r['code']}", question="instead")
                  for r in open_reqs]
        chips.append(Chip(label="Whatever counts the most", value="most", question="instead"))
        questions.append(Question(id="instead", prompt="What should it count for instead?",
                                  chips=chips))
    if not answers.get("format"):
        questions.append(Question(id="format", prompt="Any format you prefer?",
                                  chips=FORMAT_CHIPS))
    if not answers.get("else_answered"):
        questions.append(Question(id="else", prompt="Anything else?", chips=ELSE_CHIPS))
    if questions:
        when = term_label(term) or term
        return (AssistantReply(
            text=f"Let's find something to take instead of {course} {title} in {when}.",
            intent="guided_swap", generated_by="template", questions=questions), flow)
    return await show_options(ctx, flow, expand=expand)


async def continue_flow(ctx: TurnContext, flow: dict, text: str, answers_in: dict | None, *,
                  expand: Expand) -> tuple[AssistantReply, dict | None]:
    answers = dict(flow.get("answers") or {})
    for q, v in (answers_in or {}).items():
        answers = apply_chip(answers, q, str(v))
    if flow.get("stage") == "which":
        which = (answers_in or {}).get("which_course") or ""
        if "|" in which:
            course, term = which.split("|", 1)
            return await after_target(ctx, {"course": course, "term_code": term}, answers,
                                expand=expand)
        plan = ctx.call("get_plan").data
        matches = resolve(text, plan_courses(plan))
        if len(matches) == 1:
            return await after_target(ctx, matches[0], {**answers, **_from_known(extract_known(text))},
                                expand=expand)
        return await start(ctx, text, extract_known(text), expand=expand)
    if text.strip():
        known = _from_known(extract_known(text))
        answers = {**answers, **known, "else_answered": True}
        if not known.get("instead"):
            low = text.lower()
            for r in [*flow.get("open", []), *flow.get("same", [])]:
                if r["name"].lower() in low or r["code"].lower() in re.findall(r"\w+", low):
                    answers["instead"] = f"req:{r['code']}"
    more = (answers_in or {}).get("more_like")
    flow = {**flow, "answers": answers}
    if more:
        return await more_like(ctx, flow, str(more), expand=expand)
    return await show_options(ctx, flow, expand=expand)


def _filters(answers: dict) -> dict:
    f: dict = {}
    fmt = answers.get("format")
    if fmt == "online":
        f["online"] = True
    elif fmt == "async":
        f["async_only"] = True
    elif fmt == "in_person":
        f["in_person"] = True
    if answers.get("avoid_days"):
        f["avoid_days"] = list(answers["avoid_days"])
    if answers.get("earliest"):
        f["earliest"] = answers["earliest"]
    if answers.get("latest"):
        f["latest"] = answers["latest"]
    return f


def _requirements(flow: dict, answers: dict) -> tuple[list[list[str]] | None, str | None, str]:
    """(requirement groups, extra topic words, a phrase for the reply)."""
    instead = answers.get("instead") or "same"
    names = {r["code"]: r["name"] for r in [*flow.get("open", []), *flow.get("same", [])]}
    if instead == "same" and flow.get("same"):
        s = flow["same"][0]
        return [[s["code"]]], None, f"that still fill {s['name']}"
    if instead.startswith("req:"):
        code = instead[4:]
        return [[code]], None, f"that fill {names.get(code, code)}"
    if instead == "most":
        return None, None, "that fill more than one requirement"
    if instead != "same":
        return None, instead, f"about {instead}"
    return None, None, ""


async def show_options(ctx: TurnContext, flow: dict, *, expand: Expand,
                 exclude: list[str] | None = None, like: str | None = None
                 ) -> tuple[AssistantReply, dict]:
    answers = flow.get("answers") or {}
    course, term, title = flow["course"], flow["term_code"], flow.get("title", "")
    requirements, extra_topic, phrase = _requirements(flow, answers)
    filters = _filters(answers)
    if answers.get("instead") == "most":
        filters["fills_two"] = True
    topic_words = " ".join(x for x in (answers.get("topic"), extra_topic, like) if x)
    keywords = await expand(topic_words) if topic_words else []
    shown_before = list(exclude or [])
    items: list[dict]
    if requirements and not filters and not keywords and not shown_before \
            and answers.get("instead", "same") == "same":
        opts = ctx.last("list_course_options") or ctx.call(
            "list_course_options", {"course": course, "term_code": term})
        items = [o for g in opts.data.get("groups") or [] if g["kind"] == "same_requirement"
                 for o in g.get("options") or []]
    else:
        args: dict = {"term_code": term, "exclude": [course, *shown_before], "limit": 10}
        if requirements:
            args["requirements"] = requirements
        if keywords:
            args["keywords"] = keywords
        if topic_words:
            args["topic"] = topic_words[:120]
        if filters:
            args["filters"] = filters
        found = ctx.call("search_courses", args)
        items = list(found.data.get("results") or []) if found.ok else []
    items = [i for i in items if i["course"] != course and i["course"] not in shown_before]
    if answers.get("lighter"):
        items.sort(key=lambda i: (i.get("workload_weight") is None,
                                  Decimal(str(i.get("workload_weight") or 0))))
    items = items[:MAX_CARDS]
    for i in items:
        ctx.items.setdefault(i["course"], i)
    cards = [option_card(i, term_code=term, replaces=course,
                         wanted_format=answers.get("format"),
                         avoid_days=answers.get("avoid_days"), topic=answers.get("topic"))
             for i in items]
    when = term_label(term) or term
    fmt = {"online": "online or async", "async": "async", "in_person": "in person"}.get(
        answers.get("format") or "")
    bits = [b for b in (phrase, fmt) if b]
    flow = {**flow, "stage": "shown", "shown": [*shown_before, *(c.course for c in cards)],
            "cards": {c.course: {"codes": c.requirement_codes, "title": c.title}
                      for c in cards}}
    if not cards:
        relax = [Chip(label="Any format", value="none", question="format"),
                 Chip(label="Whatever counts the most", value="most", question="instead")]
        return (AssistantReply(
            text=(f"No valid course for {when} matches all of that"
                  f"{' (' + ', '.join(bits) + ')' if bits else ''}. Some catalog entries have "
                  "no description, and some terms are not loaded yet, so try loosening one "
                  "answer."), intent="guided_swap", generated_by="template",
            questions=[Question(id="relax", prompt="Loosen one?", chips=relax)]), flow)
    return (AssistantReply(
        text=(f"Here {'is' if len(cards) == 1 else 'are'} {len(cards)} option"
              f"{'' if len(cards) == 1 else 's'} for {when} instead of {course} {title}"
              f"{' ' + ', '.join(bits) if bits else ''}. Tap Choose this to swap one in - "
              "you can undo it."),
        intent="guided_swap", generated_by="template", cards=cards), flow)


async def more_like(ctx: TurnContext, flow: dict, course: str, *, expand: Expand
              ) -> tuple[AssistantReply, dict]:
    """More courses that fill what this card fills, not shown yet. The card's
    title words order the results (they never filter them: BM25 on a short
    title would leave almost nothing), and format filters are dropped so the
    student sees what else exists."""
    info = (flow.get("cards") or {}).get(course) or {}
    words = {w.lower() for w in re.findall(r"[A-Za-z]{4,}", info.get("title") or "")} - {
        "introduction", "intro", "topics", "studies", "special"}
    shown = list(flow.get("shown") or [])
    codes = info.get("codes") or []
    args: dict = {"term_code": flow["term_code"], "exclude": [flow["course"], *shown],
                  "limit": 10}
    if codes:
        args["requirements"] = [[codes[0]]]
    found = ctx.call("search_courses", args)
    items = list(found.data.get("results") or []) if found.ok else []

    def overlap(i: dict) -> int:
        hay = f"{i.get('title', '')} {i.get('description') or ''}".lower()
        return -sum(1 for w in words if w in hay)

    items.sort(key=overlap)
    for i in items:
        ctx.items.setdefault(i["course"], i)
    term, target = flow["term_code"], flow["course"]
    cards = [option_card(i, term_code=term, replaces=target) for i in items[:MAX_CARDS]]
    flow = {**flow, "stage": "shown", "shown": [*shown, *(c.course for c in cards)],
            "cards": {**(flow.get("cards") or {}),
                      **{c.course: {"codes": c.requirement_codes, "title": c.title}
                         for c in cards}}}
    when = term_label(term) or term
    if not cards:
        return (AssistantReply(text=f"That's every valid option like {course} for {when}.",
                               intent="guided_swap", generated_by="template"), flow)
    return (AssistantReply(
        text=f"Here are {len(cards)} more options like {course} for {when}, in any format.",
        intent="guided_swap", generated_by="template", cards=cards), flow)


__all__ = ["ELSE_CHIPS", "FORMAT_CHIPS", "after_target", "apply_chip", "continue_flow",
           "more_like", "show_options", "start"]
