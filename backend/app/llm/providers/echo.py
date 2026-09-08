"""Deterministic no-network provider.

This is the default. It exists so that a fresh clone, and CI, run with no API
key and no network. It is also what tests bind, which keeps test outcomes
deterministic — an LLM in a test suite makes failures unreproducible.

It echoes its input rather than reasoning. Anything that depends on real model
output must not be tested against this provider.
"""

from __future__ import annotations

from typing import Any

from app.llm.base import Completion, Message, Role, Usage


class EchoProvider:
    name = "echo"

    def __init__(self, model: str = "echo-model") -> None:
        self._model = model

    async def complete(
        self,
        *,
        role: Role,
        messages: list[Message],
        system: str | None = None,
        schema: dict[str, Any] | None = None,
        max_tokens: int = 16_000,
    ) -> Completion:
        last = messages[-1].content if messages else ""
        return Completion(
            text=f"[echo:{role.value}] {last}",
            model=self._model,
            provider=self.name,
            usage=Usage(input_tokens=len(last.split()), output_tokens=0),
            structured={} if schema is not None else None,
            stop_reason="end_turn",
        )
