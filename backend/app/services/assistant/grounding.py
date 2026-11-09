"""Grounding validation for assistant replies (Prompt 11 Part B, B2).

The Phase 5.1 rule, extended from one decision to a multi-tool turn: every
factual claim in a reply must come from a tool result in THIS turn. A reply
that fails is never repaired or partly shown. The agent regenerates it with
the problems fed back, at most twice, and then falls back to a template
built from the tool data.

    checked here                              how
    ------------                              ---
    course keys / "CS 206"-style numbers      in this turn's tool results
    course-like Title Case names              a returned title, requirement or the student's words
    SAS Core / internal requirement codes     in this turn's tool results
    "N credits"                               a number a tool returned
    "Spring 2027"                             a term a tool returned (or the student named)
    "confirmed" for a course                  that course's offering label IS confirmed
    "from your Degree Navigator audit"        progress source is the DN audit
    prerequisite / graduation / completion    a tool that establishes it ran this turn
    easy / hard, grade or GPA promises        never
    "I registered you", "I changed your plan" never - only a tap applies a change
    GPA                                       only when the student asked about it
    carousel / proposal courses               returned by a validity-gated tool

Like `services.explanations.validation`, this is decidable checking over
structured facts plus a few heuristics, one of several layers: the cards a
student acts on are built from tool data by code, not from the reply text.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from app.services.assistant.contracts import COURSE_KEY, Facts
from app.services.explanations.validation import _ACADEMIC_CLAIMS  # the Phase 5.1 patterns

#: The SAS Core codes (models/core_codes.py). A code outside this universe that
#: still looks internal (has an underscore) is checked too.
CORE_CODES = frozenset({"AHo", "AHp", "AHq", "AHr", "CCD", "CCO", "HST", "ITR", "NS", "QQ",
                        "QR", "SCL", "WC", "WCd", "WCr"})
_TOKEN = re.compile(r"\b[A-Za-z][A-Za-z0-9_]{1,40}\b")
_SUBJECT_NUMBER = re.compile(r"\b([A-Z][A-Za-z]{1,7})\.? ?(\d{3})\b")
_CREDITS = re.compile(r"(\d+(?:\.\d+)?)\s*(?:-\s*)?(?:credits?|cr)\b", re.I)
_TERM = re.compile(r"\b(Spring|Fall|Summer|Winter) (\d{4})\b")
_TITLE_PHRASE = re.compile(
    r"\b(?:[A-Z][a-z'’]+|[A-Z]{2,}|I{1,3}V?)(?:(?: (?:of|and|to|in|for|the|&|on|with))*"
    r" (?:[A-Z][a-z'’]+|[A-Z]{2,}|I{1,3}V?|\d{3})){1,6}\b")

_NEVER = (
    (re.compile(r"\b(easy|easier|easiest|hard|harder|hardest|difficult|bird course|gpa "
                r"booster|blow-?off)\b", re.I), "rates a course easy or hard"),
    (re.compile(r"\bguarantee", re.I), "makes a guarantee"),
    (re.compile(r"\byou(?:'ll| will) (?:get|earn|receive) an? [A-F]\b|\b(?:boost|raise|"
                r"improve|protect) your (?:gpa|grades?)\b|\bgrade (?:will|would) be\b", re.I),
     "promises a grade or GPA outcome"),
    (re.compile(r"\b(?:i(?:'ve| have)?|we(?:'ve| have)?) (?:registered|enrolled|signed you up)"
                r"|\byou(?:'re| are) (?:now )?(?:registered|enrolled)\b", re.I),
     "claims to register the student"),
    (re.compile(r"\b(?:i(?:'ve| have)?|we(?:'ve| have)?) (?:just )?(?:applied|saved|made|"
                r"changed|updated|moved|swapped|removed|added|replaced|locked)\b(?! (?:a |the )?"
                r"(?:preview|proposal))|\byour plan (?:is|has been|was) (?:now )?(?:updated|"
                r"changed|saved)\b|\bdone[,!.] (?:your|the) plan\b", re.I),
     "claims a change was applied (only the student's tap applies one)"),
    (re.compile(r"\b(?:as your advisor|official(?:ly)? approved|this is official advice)\b",
                re.I), "claims to give official advising"),
)

_ALLOWED_PHRASES = {
    "degree navigator", "coursepilot", "webreg", "rutgers", "sas core", "core", "new brunswick",
    "choose this", "more like this", "not now", "confirm", "undo", "ready for webreg",
    "historically offered", "needs review", "preview not yet reviewed",
    "computer science", "school of arts and sciences", "arts and humanities",
}
_DAYS_MONTHS = {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
                "january", "february", "march", "april", "may", "june", "july", "august",
                "september", "october", "november", "december", "spring", "fall", "summer",
                "winter"}


def _dec(value: str) -> Decimal | None:
    try:
        return Decimal(value).normalize()
    except (InvalidOperation, ValueError):
        return None


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]


def check_reply(text: str, facts: Facts, *, user_text: str, gpa_requested: bool,
                show_options: list[str] | None = None,
                proposal_courses: list[str] | None = None) -> list[str]:
    """Problems with a reply; empty means grounded."""
    problems: list[str] = []
    lowered = text.lower()
    user_lower = user_text.lower()
    known = facts.courses

    # 1. Course keys and "CS 206"-style references.
    unknown = sorted({k for k in COURSE_KEY.findall(text) if k not in known
                      and k not in user_text})
    if unknown:
        problems.append(f"names courses no tool returned this turn: {unknown}")
    numbers = {k[-3:] for k in known}
    for subject, number in _SUBJECT_NUMBER.findall(text):
        if subject.lower() in _DAYS_MONTHS or subject in ("Room", "Section", "Index"):
            continue
        if number not in numbers and f"{subject} {number}".lower() not in user_lower \
                and number not in user_text:
            problems.append(f"names a course ({subject} {number}) no tool returned")

    # 2. Course-like names (an invented "Advanced Underwater Ethics").
    vocabulary = " | ".join([*(f.title.lower() for f in known.values()),
                             *(n.lower() for n in facts.requirement_names),
                             *(lbl.lower() for lbl in facts.labels), user_lower])
    for phrase in {m.group(0) for m in _TITLE_PHRASE.finditer(text)}:
        words = [w for w in re.split(r"[ &]+", phrase) if w]
        content = [w for w in words if w.lower() not in ("of", "and", "to", "in", "for",
                                                         "the", "on", "with")]
        if len(content) < 2:
            continue
        low = phrase.lower()
        if low in _ALLOWED_PHRASES or low in vocabulary or all(
                w.lower() in _DAYS_MONTHS or w.isdigit() or w.lower() in vocabulary
                for w in content):
            continue
        if _TERM.fullmatch(phrase) or re.fullmatch(r"[A-Z][a-z]+ \d{3}", phrase):
            continue
        if text.find(phrase) == 0 or re.search(rf"(?:^|[.!?]\s+){re.escape(phrase)}", text):
            # Sentence-initial capitals ("Here Are") are not names when the
            # rest of the phrase would not be capitalised either.
            if not all(w[0].isupper() for w in content[1:]):
                continue
        problems.append(f"names something no tool returned: {phrase!r}")

    # 3. Requirement codes.
    for token in set(_TOKEN.findall(text)):
        internal = "_" in token and token.upper() == token
        if (token in CORE_CODES or internal) and token not in facts.requirement_codes \
                and token not in user_text:
            problems.append(f"names a requirement code no tool returned: {token}")

    # 4. Credits.
    for value in _CREDITS.findall(text):
        d = _dec(value)
        if d is not None and d not in facts.numbers and value not in user_text:
            problems.append(f"states {value} credits, which no tool returned")

    # 5. Terms.
    labels = facts.term_labels
    for season, year in _TERM.findall(text):
        lbl = f"{season} {year}"
        if lbl not in labels and lbl.lower() not in user_lower:
            problems.append(f"names {lbl}, which no tool returned this turn")

    # 6. Labels pass through unchanged.
    for sentence in _sentences(text):
        s_low = sentence.lower()
        if "confirmed" in s_low:
            for key, fact in known.items():
                named = key in sentence or (fact.title and fact.title.lower() in s_low)
                if named and fact.offering and fact.offering != "confirmed":
                    problems.append(f"calls {key} confirmed; its label is {fact.offering!r}")
    if "degree navigator audit" in lowered and facts.progress_source not in (
            "degree_navigator", None) and "not from your degree navigator" not in lowered:
        problems.append("attributes progress to the Degree Navigator audit; it is estimated")

    # 7. Academic claims only a tool can establish (the Phase 5.1 patterns).
    established = {
        "prerequisite": facts.prerequisite_info,
        "graduation": any("graduat" in lbl.lower() or re.search(r"\b(Spring|Fall|Summer|"
                                                                  r"Winter) \d{4}\b", lbl)
                          for lbl in facts.labels) or bool(facts.previews)
        or "get_plan" in facts.tools_called,
        "completion": bool({"get_progress", "get_plan"} & set(facts.tools_called)),
        "blanket requirement claim": "get_progress" in facts.tools_called,
        "remaining-work claim": bool({"get_progress", "get_plan"} & set(facts.tools_called)),
        "guarantee": False,
    }
    for pattern, label in _ACADEMIC_CLAIMS:
        if re.search(pattern, lowered) and not established.get(label, False):
            problems.append(f"makes an academic claim no tool established ({label})")

    # 8. Never. A sentence that DECLINES to rate ("I can't say whether a course
    # is easy") is not a rating.
    for pattern, label in _NEVER:
        for sentence in _sentences(text):
            if pattern.search(sentence) and not (
                    label == "rates a course easy or hard"
                    and re.search(r"\b(can'?t|cannot|don'?t|won'?t|not|never|no way)\b",
                                  sentence, re.I)):
                problems.append(label)
                break

    # 9. GPA only when asked.
    if not gpa_requested and (re.search(r"\bgpa\b|grade point", lowered)
                              or (facts.gpa and facts.gpa in text)):
        problems.append("mentions GPA, which the student did not ask about")

    # 10. What the student can act on came from validity-gated tools.
    stray = sorted(set(show_options or []) - facts.option_courses)
    if stray:
        problems.append(f"shows options no options/search tool returned: {stray}")
    stray = sorted({c for c in proposal_courses or [] if c not in known})
    if stray:
        problems.append(f"proposes courses no tool returned: {stray}")
    return list(dict.fromkeys(problems))


__all__ = ["CORE_CODES", "check_reply"]
