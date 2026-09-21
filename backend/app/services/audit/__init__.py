"""Deterministic degree audit.

No LLM. No network. Pure functions over (requirement tree + student record +
course eligibility). The same inputs always produce the same audit, which is
what makes the result testable and explainable.

See `allocation.py` for the allocation strategy and `engine.py` for
evaluation.
"""

from app.services.audit.allocation import AllocationPlan, allocate
from app.services.audit.engine import DegreeAuditEngine

__all__ = ["AllocationPlan", "DegreeAuditEngine", "allocate"]
