"""One turn's tool runner: caps, accumulated facts, the activity line."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.services.assistant.contracts import Activity, Facts
from app.services.assistant.tools import AssistantTools, ToolOutcome, run_tool


class ToolBudgetExceeded(RuntimeError):
    """More tool calls than `assistant_max_tool_calls` in one turn."""


@dataclass
class TurnContext:
    tools: AssistantTools
    gpa_allowed: bool
    max_tool_calls: int = 8
    facts: Facts = field(default_factory=Facts)
    activity: list[Activity] = field(default_factory=list)
    calls: list[dict] = field(default_factory=list)
    #: Raw option/search items by course - what cards are built from.
    items: dict[str, dict] = field(default_factory=dict)
    outcomes: list[ToolOutcome] = field(default_factory=list)
    #: Streams "Checked your plan" to the UI as each tool finishes.
    on_activity: Callable[[Activity], None] | None = None

    @property
    def remaining(self) -> int:
        return self.max_tool_calls - len(self.calls)

    def call(self, name: str, args: dict | None = None) -> ToolOutcome:
        if self.remaining <= 0:
            raise ToolBudgetExceeded(name)
        outcome = run_tool(self.tools, name, args or {}, gpa_allowed=self.gpa_allowed)
        self.calls.append({"name": name, "args": outcome.args, "ok": outcome.ok,
                           "error": outcome.error})
        self.outcomes.append(outcome)
        if outcome.ok:
            self.facts.merge(outcome.facts)
            act = Activity(tool=name, label=outcome.activity)
            self.activity.append(act)
            if self.on_activity is not None:
                self.on_activity(act)
            self._collect(name, outcome.data)
        else:
            self.facts.tools_called.append(name)
        return outcome

    def _collect(self, name: str, data: Any) -> None:
        if name == "list_course_options":
            for g in data.get("groups") or []:
                for o in g.get("options") or []:
                    self.items.setdefault(o["course"], o)
        elif name == "search_courses":
            for h in data.get("results") or []:
                self.items.setdefault(h["course"], h)

    def last(self, name: str) -> ToolOutcome | None:
        return next((o for o in reversed(self.outcomes) if o.name == name and o.ok), None)

    def activity_summary(self) -> list[Activity]:
        """Collapsed duplicates: "Checked your plan · Found 6 options"."""
        seen: dict[str, Activity] = {}
        for a in self.activity:
            seen[a.label] = a
        return list(seen.values())


__all__ = ["ToolBudgetExceeded", "TurnContext"]
