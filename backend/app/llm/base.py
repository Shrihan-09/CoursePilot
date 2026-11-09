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
    #: Prompt 11 Part B: the chat assistant's two tiers.
    ASSISTANT = "assistant"
    ASSISTANT_ESCALATION = "assistant_escalation"


@dataclass(frozen=True, slots=True)
class ToolDef:
    """A function the model may call (Prompt 11 Part B). `parameters` is a
    JSON schema; nothing vendor-specific."""

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ToolCall:
    """One function call the model asked for. `arguments` is None when the
    model's argument string was not a JSON object (the caller treats that as
    a validation failure)."""

    id: str
    name: str
    arguments: dict[str, Any] | None
    raw_arguments: str = ""


@dataclass(frozen=True, slots=True)
class Message:
    role: str  # "user" | "assistant" | "system" | "tool"
    content: str
    #: assistant turns: the calls the model made.
    tool_calls: tuple[ToolCall, ...] = ()
    #: tool turns: which call this is the output of.
    tool_call_id: str | None = None
    #: Opaque items the SAME provider replays on the next call (e.g. OpenAI
    #: Responses reasoning items with encrypted content), and the model that
    #: produced them - another model must not be sent them.
    provider_items: tuple[dict[str, Any], ...] = ()
    items_model: str | None = None


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
    #: Function calls, when the request supplied `tools`.
    tool_calls: tuple[ToolCall, ...] = ()
    #: See `Message.provider_items`.
    provider_items: tuple[dict[str, Any], ...] = ()


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
        """Run one completion. A provider that also supports native tool
        calling sets `supports_tools = True` and accepts `tools` and
        `tool_choice` keyword arguments; callers check the flag first. Implementations must not raise vendor-specific
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
