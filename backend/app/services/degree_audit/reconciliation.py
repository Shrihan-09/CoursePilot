"""Compare each Degree Navigator requirement row with CoursePilot's own audit (Phase 7.4).

Labels (one per DN row):

    agrees               both say met (or both not met) and allocate the same courses
    dn_satisfied_cp_not  DN met (or in progress), CoursePilot not
    cp_satisfied_dn_not  CoursePilot met, DN not
    allocation_differs   same verdict, different courses
    unmapped             no reviewed CoursePilot node for this DN row
    unknown              DN's status unreadable, or the node is not in this audit

CoursePilot's side is the plain Degree Engine audit of the record as it is now
(no DN overlay - this is the cross-check). Every disagreement is logged for
the developer report WITHOUT anything that identifies the student or their
grades: mapping key, node code and label only.
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy.orm import Session

from app.domain.audit import RequirementResult, RequirementStatus
from app.models import Student
from app.models.degree_audit import DegreeAuditImport
from app.services.audit.engine import DegreeAuditEngine

logger = logging.getLogger("coursepilot.degree_audit")

MET = (RequirementStatus.SATISFIED, RequirementStatus.PROVISIONALLY_SATISFIED)
DISAGREEMENTS = ("dn_satisfied_cp_not", "cp_satisfied_dn_not", "allocation_differs")


def _index(results: list[RequirementResult]) -> dict[str, RequirementResult]:
    out = {}

    def walk(nodes):
        for node in nodes:
            out.setdefault(node.requirement_code, node)
            walk(node.children)

    walk(results)
    return out


def _allocated(node: RequirementResult) -> set[str]:
    courses = {c.course_string for c in node.allocated_courses}
    for child in node.children:
        courses |= _allocated(child)
    return courses


def label(dn_status: str, dn_courses: set[str], node: RequirementResult | None) -> str:
    if node is None or dn_status == "unknown":
        return "unknown"
    dn_met = dn_status in ("satisfied", "in_progress_or_planned")
    cp_met = node.status in MET
    if dn_met and not cp_met:
        return "dn_satisfied_cp_not"
    if cp_met and not dn_met:
        return "cp_satisfied_dn_not"
    cp_courses = _allocated(node)
    if (dn_courses or cp_courses) and dn_courses != cp_courses:
        return "allocation_differs"
    return "agrees"


def reconcile(session: Session, student: Student, imp: DegreeAuditImport, now: datetime,
              *, log: bool = False) -> dict[str, int]:
    """Label every requirement row of `imp`. Returns label counts."""
    nodes = _index(DegreeAuditEngine(session).audit(student).requirements)
    counts: dict[str, int] = {}
    for row in imp.requirement_rows:
        if row.requirement_code is None:
            row.reconciliation, row.cp_status, row.cp_courses = "unmapped", None, []
        else:
            node = nodes.get(row.requirement_code)
            dn_courses = {c["course"] for c in row.extracted_courses or []
                          if c.get("kind") in ("course", "current")
                          or c.get("icon") in ("completed", "current")}
            row.reconciliation = label(row.dn_status, dn_courses, node)
            row.cp_status = node.status.value if node is not None else "not_in_audit"
            row.cp_courses = sorted(_allocated(node)) if node is not None else []
        counts[row.reconciliation] = counts.get(row.reconciliation, 0) + 1
        if log and row.reconciliation in DISAGREEMENTS and not row.excluded:
            # The developer report: anonymous by construction.
            logger.info("dn_reconciliation_disagreement", extra={
                "mapping": imp.mapping_key, "requirement": row.requirement_code,
                "kind": row.kind, "label": row.reconciliation})
    imp.reconciled_at = now
    return counts


__all__ = ["DISAGREEMENTS", "label", "reconcile"]
