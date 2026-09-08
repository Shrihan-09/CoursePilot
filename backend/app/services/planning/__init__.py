"""Planning — proposes; never decides.

NOT IMPLEMENTED YET. Contract only.

The planner turns a student's situation plus retrieved candidates into a
`PlanResponse` proposal. It is the only place an LLM is allowed to influence
academic content, and even there it is constrained:

  * it selects from a retrieved candidate set; it does not name courses freely;
  * its output is parsed into typed models, never consumed as prose;
  * its proposal is not an answer until `app.services.validation` signs off.

The plan/validate/replan loop lives here. See docs/AGENT_ARCHITECTURE.md.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.domain.plan import PlanResponse


@runtime_checkable
class Planner(Protocol):
    async def propose(self, context: object) -> PlanResponse: ...
