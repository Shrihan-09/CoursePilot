"""Cards built from tool data by code (Prompt 11 Part B, B1b/B3).

Nothing on a card is written by a model. A description is the catalog's own
text, cut to its first one or two sentences; a course without a catalog
description says so instead of getting a generated one. Labels (offering,
open status, requirement names) are copied verbatim.
"""

from __future__ import annotations

import re
from decimal import Decimal

from app.services.assistant.contracts import OptionCard, ProposalCard, SectionCard, term_label

DESCRIPTION_CHARS = 240
_DAYS = {"M": "Mon", "T": "Tue", "W": "Wed", "H": "Thu", "F": "Fri", "S": "Sat", "U": "Sun"}
_DELIVERY = {"asynchronous": "async", "async": "async", "synchronous_online": "online",
             "online": "online", "hybrid": "hybrid", "in_person": "in person"}


def shorten(description: str | None, *, limit: int = DESCRIPTION_CHARS) -> str | None:
    """The first 1-2 sentences, cut at a word boundary. Never adds a word."""
    if not description or not description.strip():
        return None
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", description)).strip()
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z])", text)
    out = sentences[0]
    if len(sentences) > 1 and len(out) + 1 + len(sentences[1]) <= limit:
        out = f"{out} {sentences[1]}"
    if len(out) > limit:
        out = out[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return out


def _delivery_words(delivery: list[str]) -> list[str]:
    return list(dict.fromkeys(_DELIVERY.get(d, d.replace("_", " ")) for d in delivery))


def why_it_works(item: dict, *, wanted_format: str | None = None,
                 avoid_days: list[str] | None = None, topic: str | None = None) -> list[str]:
    """"async · fills HST + WCd · similar to a course you did well in" - every
    part read from the tool result or the student's own stated preference."""
    why: list[str] = []
    delivery = _delivery_words(item.get("delivery") or [])
    if wanted_format and wanted_format != "none":
        match = {"online": {"online", "async"}, "async": {"async"}, "hybrid": {"hybrid"},
                 "in_person": {"in person"}}.get(wanted_format, set())
        hit = [d for d in delivery if d in match]
        if hit:
            why.append(hit[0])
    codes = item.get("requirement_codes") or []
    if codes:
        why.append("fills " + " + ".join(codes[:3]))
    if item.get("fills_multiple") and len(codes) < 2:
        why.append("fills more than one requirement")
    if avoid_days:
        why.append("fits " + ", ".join(f"no {_DAYS[d]}" for d in avoid_days if d in _DAYS))
    if topic and item.get("why") and "topic" in str(item.get("why")).lower():
        why.append(f"matches \"{topic}\"")
    if item.get("similar_to"):
        why.append("similar to a course you did well in")
    return why


def option_card(item: dict, *, term_code: str | None, replaces: str | None,
                wanted_format: str | None = None, avoid_days: list[str] | None = None,
                topic: str | None = None) -> OptionCard:
    return OptionCard(
        course=item["course"], title=item.get("title") or "",
        credits=item.get("credits"), term_code=term_code,
        description=shorten(item.get("description")),
        description_source="Rutgers catalog" if item.get("description") else None,
        why=why_it_works(item, wanted_format=wanted_format, avoid_days=avoid_days, topic=topic),
        requirements=list(item.get("requirements") or []),
        requirement_codes=list(item.get("requirement_codes") or []),
        delivery=_delivery_words(item.get("delivery") or []),
        workload_weight=item.get("workload_weight"), contact_hours=item.get("contact_hours"),
        open_status=item.get("open_status"), offering=item.get("offering"),
        replaces=replaces,
        action=({"kind": "choose_option", "change": {
            "kind": "swap", "course": replaces, "term_code": term_code,
            "replacement": item["course"]}} if replaces and term_code else None))


def proposal_card(preview: dict, *, title: str, reason: str = "confirm") -> ProposalCard:
    diff = preview.get("diff") or {}
    return ProposalCard(
        title=title, change=dict(preview.get("change") or {}),
        preview_id=str(preview.get("preview_id") or ""), summary=str(diff.get("summary") or ""),
        diff=diff, big=bool(diff.get("big")), reason=reason,
        action={"kind": "confirm_change", "preview_id": str(preview.get("preview_id") or ""),
                "change": dict(preview.get("change") or {})})


def change_title(change: dict, names: dict[str, str]) -> str:
    def n(key: str | None) -> str:
        return f"{key} {names.get(key, '')}".strip() if key else ""

    term = term_label(change.get("term_code")) or ""
    match change.get("kind"):
        case "swap":
            return f"Swap {n(change.get('course'))} for {n(change.get('replacement'))}"
        case "move":
            return f"Move {n(change.get('course'))} to {term_label(change.get('to_term'))}"
        case "remove":
            return f"Remove {n(change.get('course'))} from {term}"
        case "add":
            return f"Add {n(change.get('course'))} in {term}"
        case "lighten_term":
            return f"Lighten {term}"
        case "lock":
            return f"Lock {n(change.get('course'))}"
        case "unlock":
            return f"Unlock {n(change.get('course'))}"
        case "lock_term":
            return f"Lock {term}"
        case "unlock_term":
            return f"Unlock {term}"
        case "choose_alternative":
            return f"Choose {', '.join(change.get('alternatives') or [])}"
    return "Change your plan"


def _clock(minute: int | None) -> str:
    if minute is None:
        return "?"
    h, m = divmod(int(minute), 60)
    suffix = "am" if h < 12 else "pm"
    return f"{(h - 1) % 12 + 1}:{m:02d}{suffix}"


def meeting_lines(meetings: list[dict]) -> list[str]:
    out = []
    for m in meetings:
        if m.get("day") is None:
            out.append("Asynchronous" if m.get("kind") in ("async", "asynchronous", "online")
                       else "Time not published")
            continue
        where = f" · {m['campus']}" if m.get("campus") else ""
        out.append(f"{_DAYS.get(m['day'], m['day'])} {_clock(m.get('start_minute'))}"
                   f"–{_clock(m.get('end_minute'))}{where}")
    return out


def section_card(s: dict, *, course: str, title: str, term_code: str) -> SectionCard:
    warnings = []
    if s.get("open_status") == "closed":
        warnings.append("Closed — consider a watch")
    if s.get("special_permission"):
        warnings.append("Special permission required")
    return SectionCard(
        course=course, title=title, term_code=term_code, index_number=s["index_number"],
        section_number=s.get("section_number") or "", campus=s.get("campus"),
        instructors=list(s.get("instructors") or []), meetings=meeting_lines(
            s.get("meetings") or []), open_status=s.get("open_status") or "unknown",
        delivery=s.get("delivery"), warnings=warnings)


def credits_text(value: Decimal | None) -> str:
    if value is None:
        return ""
    v = Decimal(value).normalize()
    return f"{v:f} credits"


__all__ = ["change_title", "credits_text", "meeting_lines", "option_card", "proposal_card",
           "section_card", "shorten", "why_it_works"]
