"""Deterministic validators — the academic source of truth.

NOT IMPLEMENTED YET. This package defines the contract only.

Every validator is a pure function of (proposal, authoritative data) -> findings.
Rules for anything that lands here:

  * No LLM calls. Ever. A validator that consults a model is not deterministic
    and defeats the purpose of the layer.
  * No network I/O. Validators receive data; they do not fetch it. This keeps
    them fast, unit-testable, and free of hidden failure modes.
  * Missing data yields `CheckStatus.INDETERMINATE`, never `PASSED`. Silence
    is not consent.
  * Every finding about a Rutgers rule carries the `SourceRef` backing it.

See docs/AGENT_ARCHITECTURE.md for the validator catalogue.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.domain.validation import Finding


@runtime_checkable
class Validator(Protocol):
    """One academic rule check."""

    name: str

    def check(self, context: object) -> list[Finding]:
        """Return findings. An empty list means the check passed cleanly.

        `context` is typed as `object` for now; the concrete
        `ValidationContext` is defined once the data model is implemented.
        """
        ...
