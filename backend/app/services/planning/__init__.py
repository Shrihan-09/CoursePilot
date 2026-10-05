"""Planning — proposes; never decides.

Phase 6.5: the deterministic Planning Engine lives in this package
(`engine`, `service`, `dependencies`, `offerings`, `terms`). It builds a
semester-level course plan from the Degree Engine and the course-eligibility
service alone. There is NO LLM call anywhere in it, and none may be added:
an LLM may at most EXPLAIN a plan this engine produced, never produce or
alter one. See docs/DATA_MODEL.md section 40.

The `Planner` protocol below is the original agent contract (see
docs/AGENT_ARCHITECTURE.md section 3.4). It is still NOT IMPLEMENTED, is not
used by the deterministic engine, and is kept unchanged so that removing it
remains an explicit decision rather than a side effect of Phase 6.5:

The planner turns a student's situation plus retrieved candidates into a
`PlanResponse` proposal. It is the only place an LLM is allowed to influence
academic content, and even there it is constrained:

  * it selects from a retrieved candidate set; it does not name courses freely;
  * its output is parsed into typed models, never consumed as prose;
  * its proposal is not an answer until `app.services.validation` signs off.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.domain.plan import PlanResponse


@runtime_checkable
class Planner(Protocol):
    async def propose(self, context: object) -> PlanResponse: ...
