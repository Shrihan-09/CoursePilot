"""Message router (Prompt 11 Part B, B2): deterministic, typed, testable.

Each message is classified before any model call:

    out_of_scope       "write my essay", "register me in WebReg" - a polite
                       refusal plus what CoursePilot can do; NO model call
    guided_swap        "I don't want Art History, what's better?" - the B1b flow
    change_request     swap / move / remove / add / lock / lighten
    preference_change  "no Fridays from now on", "I prefer online"
    question           everything else

It also extracts what the message already answers (format, days, times,
topic, "same requirement"), so the guided flow never asks a question the
student just answered, and counts the plan changes asked for - two or more
send the turn to the escalation model.

A regex router is deliberately conservative. Its job is safety and cost
(out-of-scope never reaches a model; the change count picks the tier), not
understanding: the model still reads the message.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.services.assistant.contracts import COURSE_KEY

_DAY = {"monday": "M", "tuesday": "T", "wednesday": "W", "thursday": "H", "friday": "F",
        "saturday": "S", "sunday": "U"}

_OUT_OF_SCOPE = (
    ("registration", re.compile(
        r"\b(register|enrol+|sign)\s+me\b|\b(can|could|will|would) you\s+(please\s+)?"
        r"(register|enrol+|sign me up)|\bregister (it|them|this|these|those) for me\b"
        r"|\b(log|sign)\s*(me\s+)?in(to)?\s+(to\s+)?webreg|\bmy (netid|password|duo)\b"
        r"|\badd (it|them|this|these) (to|in) (my )?webreg\b|\bsubmit (it|them|my registration)"
        r"\b", re.I)),
    ("coursework", re.compile(
        r"\b(write|draft|finish|do)\s+(my|an|the|this)\s+(essay|paper|homework|assignment|"
        r"lab report|discussion post|problem set)|\b(answers?|solutions?) (to|for) (my|the|"
        r"this) (exam|quiz|homework|midterm|final)|\btake my (exam|quiz|test)\b", re.I)),
    ("unrelated", re.compile(
        r"\b(weather|recipe|stock price|bitcoin|crypto|lottery|horoscope|write (me )?a "
        r"(poem|song|story))\b", re.I)),
)

_CHANGE = re.compile(
    r"\b(swap|replace|instead|rather|don'?t want|do not want|not (want to )?take|drop|remove|"
    r"get rid of|move|push|switch|lighter|lighten|fewer credits|less work|reduce|add|lock|"
    r"unlock|take (it|that|them) (in|during)|put .{1,40} in (spring|fall|summer|winter))\b",
    re.I)
_GUIDED = re.compile(
    r"\b(don'?t want|do not want|not (want to )?take|instead of|what'?s better|better option|"
    r"something (better|else|different)|alternatives?|other options?|(swap|replace|switch)\b"
    r".{0,60}\b(for|with) (something|a|an|another|some)\b|rather (do|take|have) (a|an|some)|"
    r"a different)\b", re.I)
_PREFERENCE = re.compile(
    r"\b(from now on|always|never schedule|i prefer|my preferences?|set my|update my "
    r"preferences?|in general)\b", re.I)
_GPA = re.compile(r"\b(gpa|grade point)\b", re.I)
_SPLIT = re.compile(r"\b(?:and then|and also|also|then|plus|and)\b|[;,.]|\n", re.I)


@dataclass
class Known:
    """What the message itself answers."""

    format: str | None = None                # online | async | hybrid | in_person | none
    avoid_days: list[str] = field(default_factory=list)
    earliest: str | None = None
    latest: str | None = None
    instead: str | None = None               # same | most | different | <words>
    topic: str | None = None
    lighter: bool = False
    nothing_else: bool = False

    def any_constraint(self) -> bool:
        return bool(self.avoid_days or self.earliest or self.latest or self.topic
                    or self.lighter or self.nothing_else)


@dataclass
class Route:
    intent: str
    change_count: int = 0
    asks_gpa: bool = False
    out_of_scope: str | None = None
    known: Known = field(default_factory=Known)
    courses: list[str] = field(default_factory=list)


def extract_known(text: str) -> Known:
    t = text.lower()
    k = Known()
    if re.search(r"\basync|asynchronous|self[- ]paced\b", t):
        k.format = "async"
    elif re.search(r"\bhybrid\b", t):
        k.format = "hybrid"
    elif re.search(r"\bin[- ]person\b|\bon campus\b|\bface[- ]to[- ]face\b", t):
        k.format = "in_person"
    elif re.search(r"\bonline\b|\bremote\b|\bvirtual\b", t):
        k.format = "online"
    elif re.search(r"\bno preference\b|\bany format\b|\bformat doesn'?t matter\b", t):
        k.format = "none"
    for name, code in _DAY.items():
        if re.search(rf"\b(no|not on|avoid|without|free) {name}s?\b|\b{name}s? off\b"
                     rf"|\bnothing on {name}s?\b", t):
            k.avoid_days.append(code)
    if re.search(r"\bno (early )?mornings?\b|\bnot in the morning\b|\bno morning classes\b", t):
        k.earliest = "12:00"
    elif m := re.search(r"\b(?:nothing|no classes?) before (\d{1,2})(?::(\d\d))?\s*(am|pm)?", t):
        hour = int(m.group(1)) + (12 if m.group(3) == "pm" and int(m.group(1)) < 12 else 0)
        k.earliest = f"{hour:02d}:{m.group(2) or '00'}"
    elif re.search(r"\bno early classes\b", t):
        k.earliest = "10:00"
    if re.search(r"\bno (evening|night) classes\b|\bno evenings\b", t):
        k.latest = "18:00"
    elif m := re.search(r"\b(?:nothing|no classes?) after (\d{1,2})(?::(\d\d))?\s*(am|pm)?", t):
        hour = int(m.group(1)) + (12 if (m.group(3) or "pm") == "pm" and int(m.group(1)) < 12
                                  else 0)
        k.latest = f"{hour:02d}:{m.group(2) or '00'}"
    if re.search(r"\bsame requirement\b|\bstill (counts?|fills?)\b|\bsame (core|category)\b", t):
        k.instead = "same"
    elif re.search(r"\b(whatever|what(ever)?) counts (the )?most\b|\bfills? the most\b"
                   r"|\bdouble[- ]count|\btwo requirements\b|\bcounts? for more\b", t):
        k.instead = "most"
    elif m := re.search(r"\b(?:rather (?:do|take|have)|instead (?:do|take)) (?:a |an |some )?"
                        r"([a-z&\- ]{3,40}?) (?:course|class)", t):
        k.instead = m.group(1).strip()
    if m := re.search(r"\b(?:something|anything|a course|a class|courses?)\s+(?:about|on|in|"
                      r"related to|involving|with)\s+([a-z0-9&\- ]{2,40})", t):
        topic = re.sub(r"\b(that|which|and|but|or|instead|online|async|hybrid|in person|please|"
                       r"this|next|spring|fall|summer|winter)\b.*$", "", m.group(1)).strip()
        if topic and topic not in ("it", "that", "this"):
            k.topic = topic
    elif m := re.search(r"\binterested in ([a-z0-9&\- ]{2,40})", t):
        k.topic = m.group(1).strip()
    if re.search(r"\blighter\b|\bless work\b|\blower workload\b|\bnot too heavy\b|\blight\b", t):
        k.lighter = True
    if re.search(r"^\s*(no|nope|nothing( else)?|that'?s it|just show (me )?(the )?options)\s*"
                 r"[.!]?\s*$", t) or re.search(r"\bnothing else\b|\bthat'?s (it|all)\b", t):
        k.nothing_else = True
    return k


def count_changes(text: str) -> int:
    clauses = [c for c in _SPLIT.split(text) if c and c.strip()]
    return sum(1 for c in clauses if _CHANGE.search(c))


def route(text: str) -> Route:
    courses = COURSE_KEY.findall(text)
    for kind, pattern in _OUT_OF_SCOPE:
        if pattern.search(text):
            return Route("out_of_scope", out_of_scope=kind, courses=courses)
    known = extract_known(text)
    changes = count_changes(text)
    asks_gpa = bool(_GPA.search(text))
    if changes and _GUIDED.search(text) and not re.search(
            r"\b(swap|replace)\b.*\b\d{2}:\d{3}:\d{3}\b.*\b(for|with)\b.*\b\d{2}:\d{3}:\d{3}\b",
            text, re.I):
        return Route("guided_swap", max(changes, 1), asks_gpa, known=known, courses=courses)
    if changes:
        return Route("change_request", changes, asks_gpa, known=known, courses=courses)
    if _PREFERENCE.search(text) or (known.avoid_days and not re.search(r"\?\s*$", text)) or (
            re.search(r"\bprefer\b", text, re.I) and known.format):
        return Route("preference_change", 1, asks_gpa, known=known, courses=courses)
    return Route("question", 0, asks_gpa, known=known, courses=courses)


REFUSALS = {
    "registration": ("I can't register you or sign in to WebReg - that would need your NetID, "
                     "password and Duo, and Coursenav never asks for them. I can get you "
                     "ready instead: pick sections that fit your week, then copy their index "
                     "numbers into WebReg yourself."),
    "coursework": ("I can't do coursework, but I can help you plan around it - for example, "
                   "a lighter semester, or which requirements you still need."),
    "unrelated": ("That's outside what I can help with. I can answer questions about your plan "
                  "and progress, suggest course options, and preview changes for you to "
                  "confirm."),
}


__all__ = ["REFUSALS", "Known", "Route", "count_changes", "extract_known", "route"]
