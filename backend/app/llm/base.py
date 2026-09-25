"""LLM provider abstraction.

Purpose: keep the application from binding to one vendor's SDK. Callers ask
for a *role* ("planner", "router") and get whatever provider is configured.

Two deliberate constraints, both enforced by the shape of this interface:

1. There is no `get_text()` convenience returning a bare string. Every call
   returns a `Completion` carrying the model id and token usage, so cost and
   provenance are always attributable.

2. `schema` is not optional-by-convention — planner and router calls are
   expected to request structured output. Free-text model responses are
   advisory only and never feed the validator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable


class Role(StrEnum):
    """Logical model roles. Callers name a role; config maps roles to models,
    so swapping the planner model is a config change, not a code change."""

    ROUTER = "router"
    PLANNER = "planner"
    SUMMARIZER = "summarizer"


@dataclass(frozen=True, slots=True)
class Message:
    role: str  # "user" | "assistant" | "system"
    content: str


@dataclass(frozen=True, slots=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0


@dataclass(frozen=True, slots=True)
class Completion:
    text: str
    model: str
    provider: str
    usage: Usage = field(default_factory=Usage)
    # Populated when the request supplied a `schema`.
    structured: dict[str, Any] | None = None
    stop_reason: str | None = None


@runtime_checkable
class LLMProvider(Protocol):
    """Implemented per vendor. Kept intentionally small — anything
    vendor-specific (caching, thinking config, effort) is handled inside the
    implementation and configured via settings, not leaked into callers."""

    name: str

    async def complete(
        self,
        *,
        role: Role,
        messages: list[Message],
        system: str | None = None,
        schema: dict[str, Any] | None = None,
        max_tokens: int = 16_000,
    ) -> Completion:
        """Run one completion. Implementations must not raise vendor-specific
        exception types across this boundary."""
        ...


class ProviderNotConfiguredError(RuntimeError):
    """Raised when the configured provider cannot be constructed."""


class ProviderError(RuntimeError):
    """A provider call failed. Carries no vendor type and no credentials.

    Moved here in Phase 5.13 when a second vendor adapter arrived. It was
    defined inside the Anthropic module, which meant an OpenAI adapter would
    have had to import from `providers.anthropic` to raise the shared error -
    coupling one vendor to another through the very boundary that exists to
    keep them apart. `providers.anthropic` re-exports it, so every existing
    import keeps working.
    """
