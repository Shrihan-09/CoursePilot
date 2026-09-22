"""Anthropic provider (implemented in Phase 5.2).

Phase 0 left this as a deliberate stub, on the reasoning that wiring a live
provider before the deterministic core existed would invite the "LLM as
source of truth" failure the architecture exists to prevent. That gate is now
passed: the Degree Engine decides, Phase 5.1 built grounded evidence and
validation around it, and this provider is only ever asked to phrase facts it
was handed.

## What crosses this boundary

Nothing vendor-specific. The SDK is imported **here and nowhere else**, and
SDK exceptions are translated into `ProviderError` before they can reach the
application. A caller depends on `LLMProvider`, so replacing Anthropic is a
new file plus a registry line.

## Structured output

Callers pass a `schema`, and the reliable way to hold a model to it is to ask
for JSON and then *verify*. This provider requests JSON and parses it; it
does not claim the result is valid. Validation lives downstream in
`app.services.explanations.validation`, where the check is against
CoursePilot's own facts rather than against a shape.

That split is deliberate. A schema tells you the response is well-formed. It
says nothing about whether the content contradicts the degree audit, which is
the failure that actually matters.

## Secrets

The API key is held on the instance and never logged, never echoed in an
error message, and never included in a `Completion`. `ProviderNotConfigured
Error` names the environment variable, not its value.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.llm.base import (
    Completion,
    Message,
    ProviderNotConfiguredError,
    Role,
    Usage,
)

logger = logging.getLogger(__name__)

#: Raised for every provider-side failure. Vendor exception types must not
#: cross the `LLMProvider` boundary - a caller that has to catch
#: `anthropic.APIError` is coupled to the vendor after all.
class ProviderError(RuntimeError):
    """A provider call failed. Carries no vendor type and no credentials."""


class AnthropicProvider:
    name = "anthropic"

    def __init__(
        self,
        *,
        api_key: str | None,
        model: str,
        effort: str = "high",
        timeout_seconds: float = 30.0,
        max_retries: int = 2,
    ) -> None:
        if not api_key:
            raise ProviderNotConfiguredError(
                "ANTHROPIC_API_KEY is not set. Set LLM_PROVIDER=echo (or "
                "EXPLANATION_PROVIDER=none) for local development."
            )
        try:
            from anthropic import AsyncAnthropic
        except ModuleNotFoundError as exc:  # pragma: no cover - env dependent
            raise ProviderNotConfiguredError(
                "The 'anthropic' package is not installed. Install the optional "
                "extra (pip install -e '.[ai]') or set EXPLANATION_PROVIDER=none."
            ) from exc

        self._api_key = api_key
        self._model = model
        self._effort = effort
        self._timeout = timeout_seconds
        self._client = AsyncAnthropic(
            api_key=api_key, timeout=timeout_seconds, max_retries=max_retries
        )

    async def complete(
        self,
        *,
        role: Role,
        messages: list[Message],
        system: str | None = None,
        schema: dict[str, Any] | None = None,
        max_tokens: int = 16_000,
    ) -> Completion:
        payload = [
            {"role": m.role, "content": m.content}
            for m in messages
            if m.role in ("user", "assistant")
        ]
        if not payload:
            raise ProviderError("no user message supplied")

        try:
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=max_tokens,
                system=system or "",
                messages=payload,
            )
        except Exception as exc:
            # Deliberately broad: ANY vendor exception - transport, rate
            # limit, auth, timeout - becomes one provider-neutral error. The
            # message is the exception class, never its content, because SDK
            # errors can echo request bodies containing student data.
            logger.warning(
                "anthropic call failed", extra={"error_type": type(exc).__name__}
            )
            raise ProviderError(
                f"provider call failed ({type(exc).__name__})"
            ) from None

        text = "".join(
            block.text for block in response.content if getattr(block, "type", "") == "text"
        )

        structured: dict[str, Any] | None = None
        if schema is not None:
            structured = _parse_json(text)

        usage = getattr(response, "usage", None)
        return Completion(
            text=text,
            model=getattr(response, "model", self._model),
            provider=self.name,
            usage=Usage(
                input_tokens=getattr(usage, "input_tokens", 0) or 0,
                output_tokens=getattr(usage, "output_tokens", 0) or 0,
            ),
            structured=structured,
            stop_reason=getattr(response, "stop_reason", None),
        )


def _parse_json(text: str) -> dict[str, Any] | None:
    """Best-effort JSON extraction.

    Returns None rather than raising: a malformed response is a normal
    outcome that the explanation layer already handles by discarding the
    output and falling back. Raising here would turn a handled case into an
    error path.
    """
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = candidate.strip("`")
        candidate = candidate.split("\n", 1)[-1] if "\n" in candidate else candidate
    start, end = candidate.find("{"), candidate.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        parsed = json.loads(candidate[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


__all__ = ["AnthropicProvider", "ProviderError"]
