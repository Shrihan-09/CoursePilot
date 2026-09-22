"""Bridging the explanation layer to the LLM provider registry (Phase 5.2).

## Two abstractions, on purpose

CoursePilot already had two, from different phases and for different reasons:

```
app/llm/base.py                LLMProvider      async, roles, token usage
app/services/explanations/     ExplanationModel sync, two methods
```

Neither was deleted. `LLMProvider` is the vendor boundary - it knows about
models, roles and usage accounting. `ExplanationModel` is the *port the
explanation service depends on*: two methods, no vendor concepts, trivially
faked in a test. Collapsing them would drag token accounting and async
plumbing into a layer whose only question is "can you phrase this?".

This module is the adapter between them, and it is the only place the two
meet.

## Why the sync/async bridge lives here

The audit engine and the search index are synchronous, so an explanation
request does its database work in a worker thread. `LLMProvider.complete` is
async. `anyio.from_thread.run` is exactly the tool for calling async code
from a worker thread that an event loop is already driving, and confining it
to this file keeps the awkwardness in one visible place rather than smeared
through the service.

## Failure is normal, not exceptional

Any provider failure is reported by raising, and the caller
(`RecommendationExplanationService._finish`) already treats that as "use the
deterministic explanation". No error text from a provider is ever shown to a
user or written into an explanation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.core.config import Settings
from app.llm.base import LLMProvider, Message, ProviderNotConfiguredError, Role
from app.services.explanations.model import ModelRequest, NoModel

logger = logging.getLogger(__name__)

#: Explanation-layer provider values. Deliberately separate from
#: `llm_provider`: the explanation layer and the (future) agent layer are
#: different consumers with different risk profiles, and turning one on must
#: not silently turn on the other. Credentials and model names are NOT
#: duplicated - they are read from the existing llm_* settings.
PROVIDER_NONE = "none"
PROVIDER_ECHO = "echo"
PROVIDER_ANTHROPIC = "anthropic"


@dataclass(slots=True)
class LLMProviderExplanationModel:
    """Adapts an `LLMProvider` to the `ExplanationModel` port.

    Also the place server-owned COST bounds are applied. `max_output_tokens`
    and `max_context_chars` come from settings and are not reachable from a
    request body - there is no field for them, by design.
    """

    provider: LLMProvider
    name: str = field(default="")
    max_output_tokens: int = 1_500
    max_context_chars: int = 12_000

    def __post_init__(self) -> None:
        if not self.name:
            self.name = getattr(self.provider, "name", "provider")

    def is_available(self) -> bool:
        return True

    def generate(self, request: ModelRequest) -> str:
        """Run the provider from synchronous code.

        Raises on any failure; the service treats that as "fall back", which
        is why nothing is caught here.

        The context is truncated to a server-owned bound before it is sent.
        Evidence is already small - decision facts plus one course
        description - so this is a backstop against a pathological catalog
        entry rather than an expected path, and truncating costs tokens
        rather than correctness: the deterministic explanation is unaffected.
        """
        import anyio.from_thread

        context = request.context
        if len(context) > self.max_context_chars:
            logger.warning(
                "explanation context truncated",
                extra={"chars": len(context), "limit": self.max_context_chars},
            )
            context = context[: self.max_context_chars]

        async def _call() -> str:
            completion = await self.provider.complete(
                role=Role.SUMMARIZER,
                messages=[Message(role="user", content=context)],
                system=request.system_prompt,
                # Asking for structured output. The response is still
                # validated against CoursePilot's facts downstream - a schema
                # proves shape, not truthfulness.
                schema={"type": "object"},
                max_tokens=self.max_output_tokens,
            )
            return completion.text

        try:
            return anyio.from_thread.run(_call)
        except RuntimeError:
            # Not inside a worker thread with a running loop (a plain script
            # or a sync test). Drive the coroutine directly.
            import anyio

            return anyio.run(_call)


def build_explanation_model(settings: Settings):
    """Construct the configured explanation model.

    Returns `NoModel` for every case where a live provider is not both
    selected AND usable, so a missing key, a missing package or an unknown
    value all degrade to the deterministic path rather than failing a
    request. Phase 5.1's safety property is preserved: **no credentials still
    works.**
    """
    choice = (getattr(settings, "explanation_provider", PROVIDER_NONE) or "").lower()

    if choice in ("", PROVIDER_NONE):
        return NoModel()

    if choice == PROVIDER_ECHO:
        from app.llm.providers.echo import EchoProvider

        return LLMProviderExplanationModel(
            EchoProvider(),
            name="echo",
            max_output_tokens=settings.explanation_max_output_tokens,
            max_context_chars=settings.explanation_max_context_chars,
        )

    if choice == PROVIDER_ANTHROPIC:
        try:
            from app.llm.providers.anthropic import AnthropicProvider

            provider = AnthropicProvider(
                api_key=settings.anthropic_api_key,
                model=settings.llm_model_planner,
                effort=settings.llm_effort,
                timeout_seconds=settings.explanation_timeout_seconds,
                max_retries=settings.explanation_provider_retries,
            )
        except ProviderNotConfiguredError as exc:
            # Expected in development. Logged without the key, and without
            # the exception text, which names the variable.
            logger.info("explanation provider unavailable: %s", exc)
            return NoModel()
        return LLMProviderExplanationModel(
            provider,
            name="anthropic",
            max_output_tokens=settings.explanation_max_output_tokens,
            max_context_chars=settings.explanation_max_context_chars,
        )

    logger.warning("unknown EXPLANATION_PROVIDER %r; using deterministic output", choice)
    return NoModel()


__all__ = [
    "PROVIDER_ANTHROPIC",
    "PROVIDER_ECHO",
    "PROVIDER_NONE",
    "LLMProviderExplanationModel",
    "build_explanation_model",
]
