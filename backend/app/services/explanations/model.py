"""LLM provider abstraction and the strict prompt (Phase 5.1, Parts H and I).

## Provider independence

`ExplanationModel` is a two-method protocol. CoursePilot's domain model never
imports a vendor SDK, and no provider name appears in the service layer.
Swapping Anthropic for OpenAI, a local model, or nothing at all is a
constructor argument.

`is_available()` exists because "no model" is a normal operating state, not
an error. Development, CI and every test in this repository run without a
provider, and the deterministic fallback has to be exercised as the ordinary
path rather than as a rarely-tested branch.

## The prompt is a constraint, not a personality

The system prompt does one job: stop the model from doing anything except
phrasing facts it was given. It never asks for judgement, never invites the
model to assess a course, and explicitly tells it that absent information
must be reported as absent.

Prompting is the *weakest* of the three safeguards here and is treated that
way. It is backed by structured output, by validation that rejects a
response contradicting the facts, and by a fallback that does not need a
model at all. A prompt alone is a request; the validator is the rule.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from app.services.explanations.evidence import ExplanationEvidence, ExplanationType

SYSTEM_PROMPT = """\
You are writing a short explanation of a decision that CoursePilot has
ALREADY made. CoursePilot is a Rutgers degree-planning system whose academic
reasoning is deterministic.

THE DECISION FACTS YOU ARE GIVEN ARE AUTHORITATIVE.
Do not alter them, reinterpret them, soften them, or contradict them. You are
not being asked whether the decision was correct. You are being asked to say
what it was, readably.

You must distinguish three kinds of statement and never blur them:
  1. a CoursePilot decision   - from DECISION FACTS
  2. catalog information      - from COURSE FACTS or SOURCE DOCUMENTS
  3. unknown                  - anything not present above

NEVER invent any of the following, under any circumstances:
  - requirements or requirement names
  - prerequisites
  - credit values
  - course content or topics
  - whether a course counts toward a degree
  - a student's progress, standing, or remaining work
  - a reason a course was or was not recommended
  - a source, catalog year, or citation

If a question cannot be answered from the supplied facts, say plainly that
CoursePilot does not have that information. An incomplete answer is correct.
A plausible invented one is not.

Quote catalog descriptions rather than paraphrasing them, and attribute them
to the source given. Do not add encouragement, advice, or opinions about
whether a course is a good choice.

Respond ONLY with a JSON object of this exact shape:

{
  "summary": "one or two sentences stating the decision",
  "reasons": ["short factual statements, each drawn from the facts"],
  "course_information": ["catalog facts, attributed"],
  "limitations": ["anything the facts do not establish"],
  "citations": ["the sources you relied on, copied exactly"]
}
"""


@dataclass(frozen=True, slots=True)
class ModelRequest:
    """What a provider is asked to do. Deliberately small and inspectable."""

    system_prompt: str
    context: str
    explanation_type: ExplanationType


@runtime_checkable
class ExplanationModel(Protocol):
    """Any provider that can turn grounded context into JSON."""

    name: str

    def is_available(self) -> bool: ...

    def generate(self, request: ModelRequest) -> str: ...


class NoModel:
    """The default: no provider configured.

    Not a stub that returns empty text - it reports unavailability so the
    service takes the deterministic path explicitly. A silent empty string
    would look like a model that produced nothing useful, which is a
    different and much harder failure to notice.
    """

    name = "none"

    def is_available(self) -> bool:
        return False

    def generate(self, request: ModelRequest) -> str:  # pragma: no cover
        raise RuntimeError(
            "No explanation model is configured; callers must use the "
            "deterministic fallback."
        )


@dataclass(slots=True)
class ScriptedModel:
    """A provider that replays canned responses. For tests only.

    Kept in the production package rather than the test tree so the protocol
    has at least one in-repo implementation, and so a contributor adding a
    real provider has a shape to copy.
    """

    responses: list[str]
    name: str = "scripted"
    calls: list[ModelRequest] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.calls = []

    def is_available(self) -> bool:
        return bool(self.responses)

    def generate(self, request: ModelRequest) -> str:
        self.calls.append(request)
        if not self.responses:
            raise RuntimeError("ScriptedModel exhausted")
        return self.responses.pop(0)


def render_context(evidence: ExplanationEvidence) -> str:
    """Lay the evidence out for a model, with every section labelled.

    Sections are named so the prompt's three-way distinction is visible in
    the input as well as the instructions - the model can see which lines are
    decisions and which are catalog text.
    """
    lines: list[str] = [
        f"EXPLANATION TYPE: {evidence.explanation_type.value}",
        f"COURSE: {evidence.course_key} {evidence.course_title}".rstrip(),
        "",
        "DECISION FACTS (authoritative, from the CoursePilot degree audit):",
    ]
    lines += [f"  - {f.statement}" for f in evidence.decision_facts] or ["  (none)"]

    if evidence.requirement_facts:
        lines += ["", "REQUIREMENT SOURCE TEXT (quoted from curated Rutgers wording):"]
        lines += [
            f"  - {f.requirement_code} {f.requirement_name}: {f.statement}"
            for f in evidence.requirement_facts
        ]

    if evidence.course_facts:
        lines += ["", "COURSE FACTS (verbatim from the source named in brackets):"]
        lines += [
            f"  - {f.label}: {f.value}  [{f.citation()}]" for f in evidence.course_facts
        ]

    if evidence.source_documents:
        lines += ["", "SOURCE DOCUMENTS (retrieved, verbatim):"]
        lines += [
            f"  - {f.course_key} {f.label}: {f.value}  [{f.citation()}]"
            for f in evidence.source_documents
        ]

    if evidence.notes:
        lines += ["", "NOTES:"] + [f"  - {n}" for n in evidence.notes]

    lines += [
        "",
        "AVAILABLE CITATIONS (use these exact strings, invent none):",
    ] + [f"  - {c}" for c in evidence.citations()]
    return "\n".join(lines)


__all__ = [
    "SYSTEM_PROMPT",
    "ExplanationModel",
    "ModelRequest",
    "NoModel",
    "ScriptedModel",
    "render_context",
]
