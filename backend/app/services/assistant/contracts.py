"""Shapes the assistant passes around (Prompt 11 Part B).

Two kinds of thing live here:

* `Facts`: everything this turn's tool calls established. Grounding checks a
  reply against it and nothing else, so a course, requirement, credit value,
  term or label the tools did not return this turn cannot reach the student.
* The reply the UI renders: text plus typed cards. Every card field is copied
  from tool data by code (`cards.py`); the model never writes a card, a
  description or a label.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.core.config import APP_NAME

COURSE_KEY = re.compile(r"\b\d{2}:\d{3}:\d{3}\b")
SEASONS = {"0": "Winter", "1": "Spring", "7": "Summer", "9": "Fall"}

Intent = Literal["question", "change_request", "preference_change", "out_of_scope",
                 "guided_swap"]


def term_label(code: str | None) -> str | None:
    if not code or len(code) != 5 or code[4] not in SEASONS:
        return None
    return f"{SEASONS[code[4]]} {code[:4]}"


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


# --------------------------------------------------------------------------
# Facts
# --------------------------------------------------------------------------


@dataclass
class CourseFact:
    course: str
    title: str = ""
    credits: Decimal | None = None
    offering: str | None = None          # "confirmed" | "historically offered" | ...
    requirement_codes: set[str] = field(default_factory=set)
    delivery: list[str] = field(default_factory=list)
    description: str | None = None


@dataclass
class PreviewFact:
    preview_id: str
    valid: bool
    big: bool
    summary: str
    change: dict
    errors: list[str] = field(default_factory=list)


@dataclass
class Facts:
    courses: dict[str, CourseFact] = field(default_factory=dict)
    #: Courses a validity-gated tool (options / search) returned: the only ones
    #: a carousel or a proposal may name.
    option_courses: set[str] = field(default_factory=set)
    #: Courses the student's own plan contains (get_plan).
    planned: dict[str, str] = field(default_factory=dict)          # course -> term
    requirement_codes: set[str] = field(default_factory=set)
    requirement_names: set[str] = field(default_factory=set)
    terms: set[str] = field(default_factory=set)
    numbers: set[Decimal] = field(default_factory=set)
    labels: set[str] = field(default_factory=set)
    prerequisite_info: bool = False
    gpa: str | None = None
    progress_source: str | None = None
    previews: dict[str, PreviewFact] = field(default_factory=dict)
    tools_called: list[str] = field(default_factory=list)

    @property
    def term_labels(self) -> set[str]:
        return {lbl for t in self.terms if (lbl := term_label(t))}

    def merge(self, other: Facts) -> None:
        for key, fact in other.courses.items():
            mine = self.courses.get(key)
            if mine is None:
                self.courses[key] = fact
                continue
            mine.title = mine.title or fact.title
            mine.credits = mine.credits if mine.credits is not None else fact.credits
            mine.offering = mine.offering or fact.offering
            mine.requirement_codes |= fact.requirement_codes
            mine.delivery = mine.delivery or fact.delivery
            mine.description = mine.description or fact.description
        self.option_courses |= other.option_courses
        self.planned.update(other.planned)
        self.requirement_codes |= other.requirement_codes
        self.requirement_names |= other.requirement_names
        self.terms |= other.terms
        self.numbers |= other.numbers
        self.labels |= other.labels
        self.prerequisite_info |= other.prerequisite_info
        self.gpa = self.gpa or other.gpa
        self.progress_source = self.progress_source or other.progress_source
        self.previews.update(other.previews)
        self.tools_called += other.tools_called


def facts_from(tool: str, data: Any) -> Facts:
    """Everything a tool result establishes, read from its (shared) shape.

    Generic on purpose: the database tools and the eval's synthetic tools
    return the same shapes, so one reader serves both and neither can widen
    what counts as grounded."""
    f = Facts(tools_called=[tool])
    gated = tool in ("list_course_options", "search_courses")

    def walk(node: Any, parent_key: str = "") -> None:
        if isinstance(node, dict):
            key = node.get("course")
            if isinstance(key, str) and COURSE_KEY.fullmatch(key):
                fact = CourseFact(
                    course=key, title=str(node.get("title") or ""),
                    credits=_decimal(node.get("credits")),
                    offering=node.get("offering") if isinstance(node.get("offering"), str)
                    else None,
                    requirement_codes=set(node.get("requirement_codes") or []),
                    delivery=list(node.get("delivery") or []),
                    description=node.get("description"))
                f.merge(Facts(courses={key: fact}))
                if gated and parent_key in ("options", "results"):
                    f.option_courses.add(key)
            for k in ("replacement", "replaces"):
                if isinstance(node.get(k), str) and COURSE_KEY.fullmatch(node[k]):
                    f.courses.setdefault(node[k], CourseFact(course=node[k]))
            for k, v in node.items():
                if k in ("requirement_code", "code") and isinstance(v, str) and v:
                    f.requirement_codes.add(v)
                elif k == "requirement_codes" and isinstance(v, list):
                    f.requirement_codes |= {str(c) for c in v}
                elif k in ("requirement_name", "name") and isinstance(v, str) and v:
                    f.requirement_names.add(v)
                elif k == "requirements" and isinstance(v, list):
                    f.requirement_names |= {x for x in v if isinstance(x, str)}
                elif k in ("term_code", "to_term", "offering_term", "planned_term",
                           "graduation_term", "next_term") and isinstance(v, str):
                    f.terms.add(v)
                elif k in ("credits", "credits_before", "credits_after", "credits_completed",
                           "credits_required", "credits_remaining", "workload_weight",
                           "contact_hours", "count", "budget", "credit_max", "credit_min"):
                    if (d := _decimal(v)) is not None:
                        f.numbers.add(d.normalize())
                elif k in ("offering", "source_label", "support", "label_text") and \
                        isinstance(v, str):
                    f.labels.add(v)
                elif k == "labels" and isinstance(v, list):
                    f.labels |= {str(x) for x in v}
                elif k == "prerequisite" or k == "prerequisite_text":
                    f.prerequisite_info = True
                elif k == "gpa" and v is not None:
                    f.gpa = str(v)
                elif k == "source" and parent_key == "" and tool == "get_progress":
                    f.progress_source = str(v)
                elif k in ("graduation", "graduation_before", "graduation_after") and \
                        isinstance(v, str):
                    f.labels.add(v)
                    for t in re.findall(r"\b\d{4}[0179]\b", v):
                        f.terms.add(t)
                    for season, year in re.findall(r"\b(Winter|Spring|Summer|Fall) (\d{4})\b",
                                                   v):
                        digit = next(d for d, n in SEASONS.items() if n == season)
                        f.terms.add(f"{year}{digit}")
                walk(v, k)
        elif isinstance(node, list):
            for item in node:
                walk(item, parent_key)
        elif isinstance(node, str):
            for k in COURSE_KEY.findall(node):
                f.courses.setdefault(k, CourseFact(course=k))

    walk(data)
    if tool == "get_plan" and isinstance(data, dict):
        for term in data.get("terms") or []:
            for c in term.get("courses") or []:
                f.planned[c["course"]] = term["term_code"]
    if tool == "preview_plan_change" and isinstance(data, dict):
        diff = data.get("diff") or {}
        pid = str(data.get("preview_id") or "")
        f.previews[pid or f"invalid-{len(str(data))}"] = PreviewFact(
            preview_id=pid, valid=bool(data.get("valid")), big=bool(diff.get("big")),
            summary=str(diff.get("summary") or ""), change=dict(data.get("change") or {}),
            errors=list(data.get("errors") or []))
        for t in diff.get("terms") or []:
            for side in ("before", "after"):
                for c in t.get(side) or []:
                    f.courses.setdefault(c["course"], CourseFact(
                        course=c["course"], title=c.get("title", ""),
                        credits=_decimal(c.get("credits"))))
    return f


# --------------------------------------------------------------------------
# What the UI renders
# --------------------------------------------------------------------------


class Chip(BaseModel):
    label: str
    value: str
    #: The clarify question it answers (instead / format / else / which_course),
    #: or None for a suggested prompt.
    question: str | None = None


class Question(BaseModel):
    id: str
    prompt: str
    chips: list[Chip] = Field(default_factory=list)


class OptionCard(BaseModel):
    course: str
    title: str
    credits: Decimal | None = None
    term_code: str | None = None
    #: First 1-2 sentences of the Rutgers catalog description, cut by code.
    #: None when the catalog has none - never written by a model.
    description: str | None = None
    description_source: str | None = None
    why: list[str] = Field(default_factory=list)
    requirements: list[str] = Field(default_factory=list)
    requirement_codes: list[str] = Field(default_factory=list)
    delivery: list[str] = Field(default_factory=list)
    workload_weight: Decimal | None = None
    contact_hours: Decimal | None = None
    open_status: str | None = None
    offering: str | None = None              # verbatim: confirmed / historically offered
    replaces: str | None = None
    #: Set by the service: tapping "Choose this" sends it back.
    action_token: str | None = None
    action: dict | None = Field(default=None, exclude=True)


class ProposalCard(BaseModel):
    title: str
    change: dict
    preview_id: str
    summary: str
    diff: dict
    big: bool = False
    #: confirm (an ordinary proposal) | second_tap (a choice with a big side effect)
    #: | refreshed (the plan changed since the first preview).
    reason: str = "confirm"
    action_token: str | None = None
    action: dict | None = Field(default=None, exclude=True)


class SectionCard(BaseModel):
    course: str
    title: str = ""
    term_code: str
    index_number: str
    section_number: str
    campus: str | None = None
    instructors: list[str] = Field(default_factory=list)
    meetings: list[str] = Field(default_factory=list)
    open_status: str = "unknown"
    delivery: str | None = None
    warnings: list[str] = Field(default_factory=list)


class Activity(BaseModel):
    tool: str
    label: str


FOOTER = (f"{APP_NAME} is a planning aid; your advisor and Degree Navigator are "
          "authoritative.")


class AssistantReply(BaseModel):
    text: str
    intent: str = "question"
    questions: list[Question] = Field(default_factory=list)
    cards: list[OptionCard] = Field(default_factory=list)
    proposals: list[ProposalCard] = Field(default_factory=list)
    sections: list[SectionCard] = Field(default_factory=list)
    suggestions: list[Chip] = Field(default_factory=list)
    activity: list[Activity] = Field(default_factory=list)
    #: A term whose "Ready for WebReg" panel the UI should show.
    webreg_term: str | None = None
    #: Shown after a choice was applied: the 10-second Undo toast.
    applied_summary: str | None = None
    undo_available: bool = False
    #: model | template | router | action
    generated_by: str = "model"
    footer: str = FOOTER
    #: Persisted only (never rendered): where a multi-turn flow stands.
    flow: dict | None = Field(default=None, exclude=True)


@dataclass
class TurnUsage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    by_model: dict[str, list[int]] = field(default_factory=dict)  # model -> [in, cached, out]

    def add(self, model: str, input_tokens: int, output_tokens: int, cached: int = 0) -> None:
        self.calls += 1
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.cached_input_tokens += cached
        row = self.by_model.setdefault(model, [0, 0, 0])
        row[0] += input_tokens
        row[1] += cached
        row[2] += output_tokens

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class TurnResult:
    reply: AssistantReply
    usage: TurnUsage
    tool_calls: list[dict] = field(default_factory=list)       # name + args (no results)
    escalated: bool = False
    grounding_failures: list[list[str]] = field(default_factory=list)
    route: Any = None
    #: What this turn's tools established (the eval re-checks grounding with it).
    facts: Any = None


__all__ = ["COURSE_KEY", "FOOTER", "Activity", "AssistantReply", "Chip", "CourseFact", "Facts",
           "Intent", "OptionCard", "PreviewFact", "ProposalCard", "Question", "SectionCard",
           "TurnResult", "TurnUsage", "facts_from", "term_label"]
