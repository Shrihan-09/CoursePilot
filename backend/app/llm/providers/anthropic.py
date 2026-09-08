"""Anthropic provider — NOT IMPLEMENTED YET.

Intentionally left as a stub. The agent layer is out of scope for the current
phase, and wiring a live provider before the deterministic core exists would
invite exactly the "LLM as source of truth" failure the architecture is meant
to prevent.

When this is implemented (see docs/AGENT_ARCHITECTURE.md for the gating
criteria), it should:

  * use the official `anthropic` SDK, not raw HTTP;
  * default to the model from `settings.llm_model_*` (currently
    `claude-opus-5`) and adaptive thinking (`thinking={"type": "adaptive"}`);
  * set `output_config={"effort": settings.llm_effort}`;
  * use structured outputs (`output_config.format`) for planner/router calls
    rather than parsing prose;
  * stream long requests and read the final message via the SDK helper;
  * translate SDK exceptions into provider-neutral errors before they cross
    the `LLMProvider` boundary.
"""

from __future__ import annotations

from typing import Any

from app.llm.base import Completion, Message, ProviderNotConfiguredError, Role


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, *, api_key: str | None, model: str, effort: str = "high") -> None:
        if not api_key:
            raise ProviderNotConfiguredError(
                "ANTHROPIC_API_KEY is not set. Set LLM_PROVIDER=echo for local development."
            )
        self._api_key = api_key
        self._model = model
        self._effort = effort

    async def complete(
        self,
        *,
        role: Role,
        messages: list[Message],
        system: str | None = None,
        schema: dict[str, Any] | None = None,
        max_tokens: int = 16_000,
    ) -> Completion:
        raise NotImplementedError(
            "AnthropicProvider is a deliberate stub for this phase. "
            "See docs/AGENT_ARCHITECTURE.md before implementing."
        )
