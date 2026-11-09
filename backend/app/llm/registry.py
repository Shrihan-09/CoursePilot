"""Provider construction.

The single place that knows which concrete provider class to build. Callers
depend on the `LLMProvider` protocol only.
"""

from __future__ import annotations

from functools import lru_cache

from app.core.config import Settings, get_settings
from app.llm.base import LLMProvider, ProviderNotConfiguredError, Role
from app.llm.providers.anthropic import AnthropicProvider
from app.llm.providers.echo import EchoProvider
from app.llm.providers.openai import OpenAIProvider


def _model_for(settings: Settings, role: Role) -> str:
    match role:
        case Role.ROUTER:
            return settings.llm_model_router
        case _:
            return settings.llm_model_planner


def build_provider(settings: Settings, role: Role = Role.PLANNER) -> LLMProvider:
    match settings.llm_provider:
        case "echo":
            return EchoProvider()
        case "anthropic":
            return AnthropicProvider(
                api_key=settings.anthropic_api_key,
                model=_model_for(settings, role),
                effort=settings.llm_effort,
            )
        case "openai":
            return OpenAIProvider(
                api_key=settings.openai_api_key,
                model=settings.openai_model,
            )
        case unknown:
            raise ProviderNotConfiguredError(f"Unknown LLM_PROVIDER: {unknown!r}")


def build_assistant_provider(settings: Settings, role: Role = Role.ASSISTANT) -> LLMProvider:
    """The assistant's provider for one tier (Prompt 11 Part B).

    Same vendor adapters as every other consumer; only the model differs by
    tier. `assistant_provider` is separate from `llm_provider` and
    `explanation_provider`: turning one on never turns on another."""
    model = (settings.assistant_escalation_model if role is Role.ASSISTANT_ESCALATION
             else settings.assistant_model)
    match settings.assistant_provider:
        case "fake":
            from app.services.assistant.fake import FakeAssistantProvider

            return FakeAssistantProvider(model=f"fake-{role.value}")
        case "openai":
            return OpenAIProvider(api_key=settings.openai_api_key, model=model,
                                  timeout_seconds=settings.assistant_timeout_seconds,
                                  max_retries=settings.assistant_provider_retries)
        case "anthropic":
            return AnthropicProvider(api_key=settings.anthropic_api_key, model=model,
                                     effort=settings.llm_effort,
                                     timeout_seconds=settings.assistant_timeout_seconds,
                                     max_retries=settings.assistant_provider_retries)
        case "none":
            raise ProviderNotConfiguredError("The assistant is off (ASSISTANT_PROVIDER=none).")
        case unknown:
            raise ProviderNotConfiguredError(f"Unknown ASSISTANT_PROVIDER: {unknown!r}")


@lru_cache
def get_provider(role: Role = Role.PLANNER) -> LLMProvider:
    return build_provider(get_settings(), role)
