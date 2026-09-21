"""Grounding validation (Phase 5.1, Parts J and K).

## What this can and cannot do

This is **not** general-purpose fact checking, and pretending otherwise would
be worse than not validating at all. It checks the things CoursePilot holds
as structured data, where "wrong" is decidable:

```
checkable                              not checkable here
---------                              ------------------
course key matches the decision        whether prose is persuasive
requirement codes are real             whether a summary is well written
credit values match the audit          arbitrary natural-language claims
citations exist in the evidence
no unsupported academic verbs
```

The last check is a heuristic and is treated as one: it flags a response
asserting satisfaction or prerequisites that the decision facts never
established. It catches the failure that matters most - a fluent sentence
quietly upgrading "allocated to" into "you have completed your degree" - but
it cannot catch every phrasing, which is why it is one of four layers rather
than the only one.

A rejected response is never repaired or partially used. The service falls
back to the deterministic explanation, which was correct the whole time.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from app.services.explanations.evidence import ExplanationEvidence

#: Claims that only the Degree Engine may make. Flagged when they appear in a
#: response but are absent from the decision facts.
_ACADEMIC_CLAIMS = (
    (r"\bprerequisite", "prerequisite"),
    (r"\byou (have )?(completed|finished|fulfilled)\b", "completion"),
    (r"\bgraduat(e|ed|ion)\b", "graduation"),
    (r"\ball (of your |your )?requirements?\b", "blanket requirement claim"),
    (r"\byou (only )?need\b", "remaining-work claim"),
    (r"\bguarantee", "guarantee"),
)

_REQUIREMENT_CODE = re.compile(r"\b[A-Z][A-Z0-9_]{2,}\b")

#: Tokens that look like requirement codes but are ordinary words or units.
_CODE_ALLOWLIST = frozenset(
    {"CS", "SAS", "NB", "GPA", "AND", "THE", "FOR", "NOT", "ALL", "JSON"}
)


@dataclass(frozen=True, slots=True)
class Explanation:
    """The structured, validated result. Machine-readable, never HTML."""

    summary: str
    reasons: tuple[str, ...] = ()
    course_information: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    citations: tuple[str, ...] = ()
    generated_by: str = "deterministic"

    def render(self) -> str:
        lines = [self.summary]
        lines += [f"- {r}" for r in self.reasons]
        if self.course_information:
            lines += ["", *[f"- {c}" for c in self.course_information]]
        if self.limitations:
            lines += ["", "Not established by CoursePilot:"]
            lines += [f"- {limitation}" for limitation in self.limitations]
        if self.citations:
            lines += ["", "Sources: " + "; ".join(self.citations)]
        return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class ValidationResult:
    ok: bool
    explanation: Explanation | None = None
    problems: tuple[str, ...] = field(default_factory=tuple)


def _all_text(payload: dict) -> str:
    parts: list[str] = [str(payload.get("summary", ""))]
    for key in ("reasons", "course_information", "limitations"):
        value = payload.get(key) or []
        if isinstance(value, list):
            parts += [str(v) for v in value]
    return "\n".join(parts)


def validate_response(raw: str, evidence: ExplanationEvidence) -> ValidationResult:
    """Parse and ground-check a model response against the evidence."""
    problems: list[str] = []

    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return ValidationResult(False, None, ("response was not valid JSON",))

    if not isinstance(payload, dict):
        return ValidationResult(False, None, ("response was not a JSON object",))
    if not str(payload.get("summary", "")).strip():
        problems.append("summary is empty")

    for key in ("reasons", "course_information", "limitations", "citations"):
        value = payload.get(key, [])
        if value and not isinstance(value, list):
            problems.append(f"{key} must be a list")

    text = _all_text(payload)
    lowered = text.lower()

    # 1. The course must be the one the decision is about.
    other_keys = re.findall(r"\b\d{2}:\d{3}:\d{3}\b", text)
    wrong = {k for k in other_keys if k != evidence.course_key}
    if wrong:
        problems.append(
            f"mentions course keys not in the decision: {sorted(wrong)}"
        )

    # 2. Requirement codes must come from the evidence.
    allowed_codes = set(evidence.requirement_codes)
    mentioned = {
        code
        for code in _REQUIREMENT_CODE.findall(text)
        if code not in _CODE_ALLOWLIST and "_" in code or code in allowed_codes
    }
    invented = {c for c in mentioned if c not in allowed_codes}
    if invented:
        problems.append(f"mentions requirement codes not in the evidence: {sorted(invented)}")

    # 3. Credit values must match what the audit applied.
    known_credits = {
        f.credits for f in evidence.decision_facts if f.credits is not None
    }
    known_credits |= {
        _as_decimal(f.value) for f in evidence.course_facts if f.label == "credits"
    }
    known_credits.discard(None)
    for number in re.findall(r"(\d+(?:\.\d+)?)\s*credits?\b", lowered):
        value = _as_decimal(number)
        if known_credits and value not in known_credits:
            problems.append(
                f"states {number} credits, which no fact establishes "
                f"(known: {sorted(str(c) for c in known_credits)})"
            )

    # 4. Citations must exist in the evidence.
    available = set(evidence.citations())
    for citation in payload.get("citations") or []:
        if str(citation) not in available:
            problems.append(f"cites a source not in the evidence: {citation!r}")

    # 5. Academic claims the decision facts never made.
    decision_text = " ".join(f.statement.lower() for f in evidence.decision_facts)
    for pattern, label in _ACADEMIC_CLAIMS:
        if re.search(pattern, lowered) and not re.search(pattern, decision_text):
            problems.append(f"makes an unsupported academic claim ({label})")

    if problems:
        return ValidationResult(False, None, tuple(problems))

    return ValidationResult(
        True,
        Explanation(
            summary=str(payload["summary"]).strip(),
            reasons=tuple(str(r) for r in payload.get("reasons") or ()),
            course_information=tuple(
                str(c) for c in payload.get("course_information") or ()
            ),
            limitations=tuple(str(x) for x in payload.get("limitations") or ()),
            citations=tuple(str(c) for c in payload.get("citations") or ()),
            generated_by="model",
        ),
        (),
    )


def _as_decimal(value: str) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


__all__ = ["Explanation", "ValidationResult", "validate_response"]
