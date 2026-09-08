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
        case unknown:
            raise ProviderNotConfiguredError(f"Unknown LLM_PROVIDER: {unknown!r}")


@lru_cache
def get_provider(role: Role = Role.PLANNER) -> LLMProvider:
    return build_provider(get_settings(), role)
