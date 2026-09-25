"""OpenAI provider adapter (Phase 5.13).

Sits at the same vendor boundary as `AnthropicProvider` and implements the
same `LLMProvider` protocol, so nothing above it learns that the provider
changed:

```
Explanation Service -> ExplanationModel -> LLMProvider -> OpenAIProvider -> OpenAI
```

## What this adapter owns

Everything vendor-specific, and nothing else:

  * constructing the SDK client, with the server-owned timeout and retry
    budget;
  * turning CoursePilot `Message`/`system` into OpenAI's message list;
  * asking for structured output when the caller supplied a schema;
  * pulling text and token usage back out of the response shape;
  * **translating every SDK exception into `ProviderError`**, so a caller
    that catches OpenAI's exception types cannot exist.

## Retries are the SDK's, and there is exactly one layer of them

`max_retries` is handed to the client, which retries only transient
failures - connection errors, timeouts, 408/409/429/5xx. Authentication and
validation failures are never retried, because they would fail identically.
CoursePilot adds no second retry layer: the explanation service calls the
model exactly once per request, and a second application-level retry would
turn one user request into several billable calls.

## The model id is configuration, not code

`OPENAI_MODEL` selects it, server-side. No request field reaches it - a
client that could choose the model could choose a cheaper, weaker or
differently-aligned one, and the cost is the operator's.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.llm.base import (
    Completion,
    Message,
    ProviderError,
    ProviderNotConfiguredError,
    Role,
    Usage,
)

logger = logging.getLogger(__name__)

#: Roles the Chat Completions API accepts from us. Anything else in the
#: message list is dropped rather than guessed at.
_ALLOWED_ROLES = ("user", "assistant")


class OpenAIProvider:
    """Adapts the official OpenAI SDK to the `LLMProvider` protocol."""

    name = "openai"

    def __init__(
        self,
        *,
        api_key: str | None,
        model: str,
        timeout_seconds: float = 30.0,
        max_retries: int = 2,
    ) -> None:
        if not api_key:
            raise ProviderNotConfiguredError(
                "OPENAI_API_KEY is not set. Set EXPLANATION_PROVIDER=none (or "
                "'echo') for local development."
            )
        try:
            from openai import AsyncOpenAI
        except ModuleNotFoundError as exc:  # pragma: no cover - env dependent
            raise ProviderNotConfiguredError(
                "The 'openai' package is not installed. Install the optional "
                "extra (pip install -e '.[ai]') or set EXPLANATION_PROVIDER=none."
            ) from exc

        # Held only to be handed to the SDK. Never logged, never returned,
        # and never part of any error message - see `_provider_error`.
        self._api_key = api_key
        self._model = model
        self._timeout = timeout_seconds
        self._client = AsyncOpenAI(
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
        payload: list[dict[str, str]] = []
        if system:
            # The grounding instructions. Sent as a system message so the
            # evidence that follows is unambiguously data, not instruction -
            # the prompt-injection boundary Phase 5.1 established.
            payload.append({"role": "system", "content": system})
        payload.extend(
            {"role": m.role, "content": m.content}
            for m in messages
            if m.role in _ALLOWED_ROLES
        )
        if not any(m["role"] == "user" for m in payload):
            raise ProviderError("no user message supplied")

        request: dict[str, Any] = {
            "model": self._model,
            "messages": payload,
            "max_completion_tokens": max_tokens,
        }
        if schema is not None:
            # Structured output. A schema proves SHAPE, never truthfulness -
            # the response is still validated against CoursePilot's own
            # decision facts downstream, and that check is what actually
            # protects the student.
            request["response_format"] = {"type": "json_object"}

        try:
            response = await self._client.chat.completions.create(**request)
        except Exception as exc:
            raise self._provider_error(exc) from None

        text = self._extract_text(response)
        structured = _parse_json(text) if schema is not None else None

        usage = getattr(response, "usage", None)
        cached = 0
        details = getattr(usage, "prompt_tokens_details", None)
        if details is not None:
            cached = getattr(details, "cached_tokens", 0) or 0

        return Completion(
            text=text,
            model=getattr(response, "model", self._model),
            provider=self.name,
            usage=Usage(
                input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                output_tokens=getattr(usage, "completion_tokens", 0) or 0,
                cached_input_tokens=cached,
            ),
            structured=structured,
            stop_reason=self._extract_stop_reason(response),
        )

    # -- helpers -------------------------------------------------------

    @staticmethod
    def _extract_text(response: Any) -> str:
        choices = getattr(response, "choices", None) or []
        if not choices:
            return ""
        message = getattr(choices[0], "message", None)
        return (getattr(message, "content", None) or "") if message else ""

    @staticmethod
    def _extract_stop_reason(response: Any) -> str | None:
        choices = getattr(response, "choices", None) or []
        return getattr(choices[0], "finish_reason", None) if choices else None

    def _provider_error(self, exc: Exception) -> ProviderError:
        """One provider-neutral error, carrying only the exception CLASS.

        Deliberately broad: transport, rate limit, auth, timeout and server
        errors all become the same type, because the explanation layer's
        response to every one of them is identical - discard and fall back.

        The message never includes the exception's own text. SDK errors can
        echo the request body, and the request body is the student's
        academic evidence; some also quote request headers, which carry the
        API key.
        """
        logger.warning(
            "openai_call_failed", extra={"error_type": type(exc).__name__}
        )
        return ProviderError(f"provider call failed ({type(exc).__name__})")


def _parse_json(text: str) -> dict[str, Any] | None:
    """Best-effort JSON extraction.

    Returns None rather than raising: a malformed response is a normal
    outcome the explanation layer already handles by discarding the output
    and falling back. Raising here would turn a handled case into an error
    path.

    Mirrors the Anthropic adapter deliberately - two vendors, one contract.
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


__all__ = ["OpenAIProvider"]
