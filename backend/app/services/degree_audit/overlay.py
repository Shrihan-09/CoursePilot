"""A confirmed Degree Navigator audit as the requirement STATUS planning uses (Phase 7.4).

Decision 5 of the 7.4 answers: once the student confirms a DN audit, DN's
status drives planning for every MAPPED requirement - DN reflects exceptions
and equivalencies CoursePilot cannot see. CoursePilot's own audit stays the
cross-check. This module never changes a requirement rule: it takes the Degree
Engine's result and replaces the STATUS of mapped nodes, saying so in the
node's reason.

Planning status of a DN row (decision 2 splits "In Progress or Planned"):

    satisfied              -> SATISFIED
    in_progress_or_planned -> IN PROGRESS (provisionally satisfied, counts as
                              satisfied-if-passed for next term) when EVERY
                              course behind it is in the student's confirmed
                              current schedule for its term; otherwise PLANNED,
                              which counts as not satisfied
    not_satisfied          -> not satisfied
    unknown / excluded / unmapped -> CoursePilot's own status ("estimated")

Only the upward direction changes a node: a DN "satisfied" or "in progress"
lifts CoursePilot's status. DN "not satisfied" where CoursePilot says met is a
disagreement shown as NEEDS REVIEW, but CoursePilot does not invent which
course is missing - DN's "Needs" line does not name one, so planning would
have to guess. The plan says so instead.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.audit import DegreeAuditResult, RequirementResult, RequirementStatus
from app.models import Student
from app.models.current_section import StudentCurrentSection
from app.models.degree_audit import DegreeAuditImport, DegreeAuditRequirementRow
from app.services.audit.engine import DegreeAuditEngine

MET = (RequirementStatus.SATISFIED, RequirementStatus.PROVISIONALLY_SATISFIED)
_RANK = {RequirementStatus.SATISFIED: 2, RequirementStatus.PROVISIONALLY_SATISFIED: 1}

ESTIMATED_LABEL = "Estimated - import your Degree Navigator audit for accuracy"
DISAGREE_LABEL = "Degree Navigator and CoursePilot disagree; confirm with your advisor"


@dataclass(frozen=True)
class NodeStatus:
    requirement_code: str
    program_key: str | None
    #: satisfied | in_progress | planned | not_satisfied
    planning_status: str
    dn_status: str
    rule: str
    import_id: str
    as_of: datetime | None
    reconciliation: str | None
    mapping_review_status: str | None
    name: str | None

    @property
    def disagrees(self) -> bool:
        return self.reconciliation in ("dn_satisfied_cp_not", "cp_satisfied_dn_not",
                                       "allocation_differs")


@dataclass
class DnOverlay:
    statuses: dict[str, NodeStatus] = field(default_factory=dict)
    imports: list[DegreeAuditImport] = field(default_factory=list)

    def for_keys(self, keys: set[str]) -> DnOverlay:
        """Only the nodes of these programs / Core sets (a what-if keeps Core)."""
        return DnOverlay({c: s for c, s in self.statuses.items() if s.program_key in keys},
                         self.imports)

    def __bool__(self) -> bool:
        return bool(self.statuses)


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def current_schedule(session: Session, student_id: uuid.UUID) -> set[tuple[str, str]]:
    """(term, course) of the student's CONFIRMED current schedule: sections
    confirmed from a WebReg schedule import (`student_registration`, 7.2.2)
    and sections confirmed by hand (`student_current_section`, 7.2.1)."""
    from app.models.schedule_import import StudentRegistration

    manual = session.execute(
        select(StudentCurrentSection.term_code, StudentCurrentSection.course_string)
        .where(StudentCurrentSection.student_id == student_id))
    webreg = session.execute(
        select(StudentRegistration.term_code, StudentRegistration.course_string)
        .where(StudentRegistration.student_id == student_id))
    return {(t, c) for t, c in [*manual, *webreg]}


def planning_status(row: DegreeAuditRequirementRow,
                    schedule: set[tuple[str, str]]) -> tuple[str, str]:
    """(planning status, the rule that decided it) for one DN row."""
    status = row.dn_status
    if status == "satisfied":
        return "satisfied", "Degree Navigator shows it fulfilled."
    if status == "not_satisfied":
        return "not_satisfied", "Degree Navigator shows it not fulfilled."
    if status != "in_progress_or_planned":
        return "unknown", "Degree Navigator's status could not be read."
    behind = [c for c in row.extracted_courses or []
              if c.get("kind") in ("current", "planned")
              or c.get("icon") in ("current", "planned")]
    if not behind:
        return "planned", ("Degree Navigator says in progress or planned but lists no current "
                           "course, so CoursePilot counts it as planned (not satisfied).")
    planned = [c["course"] for c in behind
               if c.get("kind") == "planned" or c.get("icon") == "planned"]
    if planned:
        return "planned", (f"In progress or planned: {', '.join(sorted(set(planned)))} "
                           "is only planned, so it counts as not satisfied.")
    missing = sorted({c["course"] for c in behind
                      if (c.get("term_code"), c["course"]) not in schedule})
    if missing:
        return "planned", (f"In progress or planned: {', '.join(missing)} is not in your "
                           "confirmed current schedule, so it counts as planned (not "
                           "satisfied). Confirm your current sections to change this.")
    courses = ", ".join(sorted({c["course"] for c in behind}))
    return "in_progress", (f"In progress: every course behind it ({courses}) is in your "
                           "confirmed current schedule - satisfied if you pass.")


def active_overlay(session: Session, student: Student) -> DnOverlay:
    """The statuses of the student's confirmed, not superseded DN imports."""
    imports = list(session.scalars(
        select(DegreeAuditImport).where(DegreeAuditImport.student_id == student.id,
                                        DegreeAuditImport.state == "confirmed",
                                        DegreeAuditImport.superseded_at.is_(None))
        .order_by(DegreeAuditImport.confirmed_at)))
    if not imports:
        return DnOverlay()
    schedule = current_schedule(session, student.id)
    out: dict[str, NodeStatus] = {}
    for imp in imports:
        # Requirement rows win over goal rows for the same node.
        rows = sorted(imp.requirement_rows, key=lambda r: (r.kind != "requirement", r.row_index))
        for row in rows:
            if row.excluded or row.requirement_code is None or row.state == "unmapped":
                continue
            planned, rule = planning_status(row, schedule)
            if planned == "unknown":
                continue
            if row.requirement_code in out and out[row.requirement_code].import_id == str(imp.id):
                continue
            out[row.requirement_code] = NodeStatus(
                requirement_code=row.requirement_code, program_key=imp.mapping_key,
                planning_status=planned, dn_status=row.dn_status, rule=rule,
                import_id=str(imp.id), as_of=_utc(imp.as_of),
                reconciliation=row.reconciliation,
                mapping_review_status=row.mapping_review_status,
                name=row.extracted_title or row.dn_code)
    return DnOverlay(out, imports)


def _walk(nodes: list[RequirementResult]):
    for node in nodes:
        yield node
        yield from _walk(node.children)


def apply_overlay(result: DegreeAuditResult, overlay: DnOverlay) -> DegreeAuditResult:
    """Lift mapped nodes to DN's status; re-derive their group ancestors. In place."""
    if not overlay:
        return result
    changed = False

    def visit(node: RequirementResult) -> bool:
        nonlocal changed
        child_changed = False
        for child in node.children:
            child_changed |= visit(child)
        if child_changed and node.requirement_type == "all_of":
            DegreeAuditEngine._eval_all_of(node, node.children)
        elif child_changed and node.requirement_type == "any_of":
            need = node.needed_count or 1
            done = sum(c.status is RequirementStatus.SATISFIED for c in node.children)
            prov = sum(c.status is RequirementStatus.PROVISIONALLY_SATISFIED
                       for c in node.children)
            if done >= need:
                node.status = RequirementStatus.SATISFIED
            elif done + prov >= need:
                node.status = RequirementStatus.PROVISIONALLY_SATISFIED
        status = overlay.statuses.get(node.requirement_code)
        if status is None:
            return child_changed
        target = {"satisfied": RequirementStatus.SATISFIED,
                  "in_progress": RequirementStatus.PROVISIONALLY_SATISFIED}.get(
            status.planning_status)
        as_of = f" (as of {status.as_of:%Y-%m-%d})" if status.as_of else ""
        if target is not None and _RANK.get(node.status, 0) < _RANK[target]:
            own = node.status.value.replace("_", " ")
            node.status = target
            node.reason = (f"From your Degree Navigator audit{as_of}: {status.rule} "
                           f"CoursePilot's own check says {own}. {DISAGREE_LABEL}.")
            changed = True
            return True
        if status.planning_status in ("planned", "not_satisfied") and node.status in MET:
            node.reason = (f"Degree Navigator{as_of} does not show this satisfied "
                           f"({status.rule}) while CoursePilot does. {DISAGREE_LABEL}. "
                           f"{node.reason}")
        return child_changed

    for root in result.requirements:
        visit(root)
    return result


class OverlaidAuditEngine(DegreeAuditEngine):
    """The Degree Engine with a confirmed DN overlay applied to every result -
    the base audit and every projected audit the Planning Engine runs."""

    def __init__(self, session: Session, overlay: DnOverlay, **kwargs) -> None:
        super().__init__(session, **kwargs)
        self.overlay = overlay

    def audit(self, *args, **kwargs) -> DegreeAuditResult:
        return apply_overlay(super().audit(*args, **kwargs), self.overlay)


# --------------------------------------------------------------------------
# freshness (decision 6)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Freshness:
    as_of: datetime | None
    as_of_source: str | None
    age_days: int | None
    stale: bool
    reasons: list[str]
    message: str


def freshness(imp: DegreeAuditImport, now: datetime) -> Freshness:
    from app.core.config import get_settings
    from app.core.grade_calendar import latest_final_grades

    as_of = _utc(imp.as_of)
    if as_of is None:
        return Freshness(None, None, None, True, ["no_date"],
                         "The audit's date is unknown; re-import it.")
    age = (now - as_of).days
    reasons, notes = [], []
    limit = get_settings().dn_audit_stale_days
    if age > limit:
        reasons.append("older_than_limit")
        notes.append(f"it is {age} days old (more than {limit})")
    latest = latest_final_grades(now)
    if latest is not None and as_of < latest[1]:
        reasons.append("predates_final_grades")
        notes.append(f"it predates the final grades for term {latest[0]}")
    source = "printed" if imp.as_of_source == "print_header" else "uploaded"
    if reasons:
        message = (f"Your Degree Navigator audit ({source} {as_of:%b %d, %Y}) may be out of "
                   f"date: {'; '.join(notes)}. Re-import it from Degree Navigator.")
    else:
        message = f"From your Degree Navigator audit, {source} {as_of:%b %d, %Y} ({age} days ago)."
    return Freshness(as_of, imp.as_of_source, age, bool(reasons), reasons, message)


__all__ = ["DISAGREE_LABEL", "ESTIMATED_LABEL", "DnOverlay", "Freshness", "NodeStatus",
           "OverlaidAuditEngine", "active_overlay", "apply_overlay", "current_schedule",
           "freshness", "planning_status"]
