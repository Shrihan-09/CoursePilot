"""The student's plan changes: preview -> confirm -> saved, with undo (Prompt 11 A2).

Deterministic code decides everything here. A change becomes new `PlanEdits`
(pins, avoids, a lowered term range, a one-of choice); the Planning Engine is
re-run with them and with every OTHER course of the current plan HELD where it
is (`PlanningEngine(holds=...)`: a soft preference, so a prerequisite or
requirement chain can still move a course - and every such move is listed).
The result is a PREVIEW. Only `apply_preview`, while the saved plan is still at
the preview's revision, makes it the plan.

```
preview_change(change)  -> PlanPreview (stored; never the plan)
apply_preview(id)       -> the saved plan, revision + 1, previous version in history
undo(student)           -> the previous version back (revision + 1)
```

Nothing is ever silently replaced: a LOCKED or USER_SELECTED course is a pin,
and one that can no longer be placed is reported (`needs_reselection`), never
swapped for something else.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.domain.plan_edits import (
    CourseLine,
    CourseState,
    ForcedMove,
    PlanChange,
    PlanDiff,
    PlanEdits,
    PlannedCourseState,
    PlanPreview,
    TermCap,
    TermDiff,
)
from app.domain.planning import (
    CourseAvoid,
    CoursePin,
    PlanningConstraints,
    PlanResult,
    Severity,
    TermPlanningOverride,
)
from app.models import Student
from app.models.plans import SNAPSHOT_COLUMNS, PlanChangePreview, StudentPlan, StudentPlanHistory
from app.services.planning.terms import label

#: How many earlier versions "Undo last change" can walk back through.
HISTORY_DEPTH = 5
#: A preview older than this is refused (the plan's inputs may have moved on).
PREVIEW_TTL = timedelta(minutes=30)

MET_STATUSES = ("satisfied", "provisionally_satisfied")


class NoSavedPlan(LookupError):
    pass


class PlanOutOfDate(RuntimeError):
    """The saved plan's inputs changed: rebuild before changing it."""


class StalePreview(RuntimeError):
    """The preview no longer applies: the plan changed, or it expired."""


class NothingToUndo(LookupError):
    pass


# --------------------------------------------------------------------------
# edits <-> constraints
# --------------------------------------------------------------------------


def edits_of(row: StudentPlan | None) -> PlanEdits:
    return PlanEdits.model_validate((row.edits if row is not None else None) or {})


def plan_constraints(base: PlanningConstraints, edits: PlanEdits) -> PlanningConstraints:
    """The Planning Profile's constraints with the student's plan edits on top.

    An edit pin replaces a profile pin for the same course (an anchor the
    student moved keeps its retake flag); avoids, lowered term ranges and
    one-of choices are added. The same function decides what a saved plan
    "should" have been built from, so freshness compares like with like."""
    edit_pins = [*edits.locks, *edits.selections]
    pinned = {p.course for p in edit_pins}
    pins = [p for p in base.pins if p.course not in pinned] + edit_pins
    overrides = {o.term_code: o for o in base.term_overrides}
    for cap in edits.term_caps:
        old = overrides.get(cap.term_code)
        fields = old.model_dump() if old is not None else {"term_code": cap.term_code}
        fields |= {"credit_min": cap.credit_min, "credit_max": cap.credit_max}
        overrides[cap.term_code] = TermPlanningOverride.model_validate(fields)
    selections = {**base.alternative_selections, **edits.alternative_selections}
    data = base.model_dump()
    data |= {"pins": [p.model_dump() for p in pins],
             "avoid": [a.model_dump() for a in [*base.avoid, *edits.avoid]],
             "term_overrides": [o.model_dump() for o in sorted(overrides.values(),
                                                                key=lambda o: o.term_code)],
             "alternative_selections": selections}
    return PlanningConstraints.model_validate(data)


# --------------------------------------------------------------------------
# course states
# --------------------------------------------------------------------------


def course_states(result: PlanResult, constraints: PlanningConstraints) -> list[PlannedCourseState]:
    pins = {(p.course, p.term_code): p for p in constraints.pins}
    out = []
    for term in result.terms:
        for pc in term.courses:
            pin = pins.get((pc.course, term.term_code))
            if pin is not None and pin.source == "selection":
                state, why = CourseState.USER_SELECTED, "user_selected"
            elif pin is not None and pin.source == "lock":
                state, why = CourseState.LOCKED, "locked"
            elif pin is not None or pc.retake or (pc.role == "anchor"):
                state, why = CourseState.LOCKED, "anchor"
            else:
                state, why = CourseState.PLANNER_SELECTED, None
            out.append(PlannedCourseState(course=pc.course, term_code=term.term_code,
                                          state=state, locked_reason=why))
    return out


@dataclass
class Reselection:
    course: str
    term_code: str
    source: str                # selection | lock | must
    message: str


def needs_reselection(result: PlanResult, constraints: PlanningConstraints) -> list[Reselection]:
    """The student's own choices the plan could not keep - held, never replaced."""
    sources = {p.course: (p.source, p.term_code) for p in constraints.pins}
    out = []
    for issue in result.issues:
        if issue.code == "PIN_NOT_POSSIBLE" and issue.course in sources:
            source, term = sources[issue.course]
            out.append(Reselection(issue.course, term, source, issue.message))
    return out


# --------------------------------------------------------------------------
# a change -> new edits + holds
# --------------------------------------------------------------------------


@dataclass
class Edit:
    edits: PlanEdits
    holds: dict[str, str]
    errors: list[str]
    summary: str
    touched: set[str]


def _where(plan: PlanResult) -> dict[str, str]:
    return {pc.course: t.term_code for t in plan.terms for pc in t.courses}


def _course(plan: PlanResult, course: str, term: str):
    for t in plan.terms:
        if t.term_code == term:
            for pc in t.courses:
                if pc.course == course:
                    return t, pc
    return None, None


def _dependents(plan: PlanResult, course: str) -> set[str]:
    return {pc.course for t in plan.terms for pc in t.courses
            if any(d.course == course for d in pc.prerequisite.conditional_on)}


def _prerequisites(plan: PlanResult, course: str) -> set[str]:
    return {d.course for t in plan.terms for pc in t.courses if pc.course == course
            for d in pc.prerequisite.conditional_on if d.source == "planned"}


def _without(pins: list[CoursePin], course: str) -> list[CoursePin]:
    return [p for p in pins if p.course != course]


def build_edit(plan: PlanResult, constraints: PlanningConstraints, edits: PlanEdits,
               change: PlanChange) -> Edit:
    """What `change` does to the student's edits, and which courses stay held.

    Refusals are closed codes with a message ("code: message"); nothing is
    run when there is one."""
    where = _where(plan)
    states = {(s.course, s.term_code): s for s in course_states(plan, constraints)}
    e = edits.model_copy(deep=True)
    errors: list[str] = []
    release: set[str] = set()          # courses NOT held (the planner may re-place them)
    touched: set[str] = set()
    k, course, term = change.kind, change.course, change.term_code
    summary = ""

    term_plan, pc = (_course(plan, course, term) if course and term else (None, None))
    state = states.get((course, term)) if course and term else None
    if k in ("swap", "move", "remove", "lock", "unlock") and pc is None:
        errors.append(f"course_not_in_term: {course} is not planned in {label(term)}.")
        return Edit(e, {}, errors, "", set())

    if k == "set_term":
        if term not in {t.term_code for t in plan.terms}:
            return Edit(e, {}, ["term_not_in_plan: Build an existing semester."], "", set())
        selected = set(change.courses)
        current = {c for c, t in where.items() if t == term}
        holds = dict(where)
        for removed in sorted(current - selected):
            part = build_edit(plan, constraints, e, PlanChange(kind="remove", course=removed, term_code=term))
            errors += part.errors
            e = part.edits
            holds = {c: t for c, t in holds.items() if c in part.holds}
            touched |= part.touched
        for added in sorted(selected - current):
            cmd = (PlanChange(kind="move", course=added, term_code=where[added], to_term=term)
                   if added in where else PlanChange(kind="add", course=added, term_code=term))
            part = build_edit(plan, constraints, e, cmd)
            errors += part.errors
            e = part.edits
            holds = {c: t for c, t in holds.items() if c in part.holds}
            touched |= part.touched
        # Keeping a planner pick is now an explicit user selection. Preserve locks/anchors.
        for kept in sorted(current & selected):
            if states[(kept, term)].state is CourseState.PLANNER_SELECTED:
                e.selections = _without(e.selections, kept) + [CoursePin(course=kept, term_code=term, source="selection")]
        if len({p.course for p in [*constraints.pins, *e.selections, *e.locks]}) > 48:
            errors.append("too_many_selections: This plan supports up to 48 pinned or selected courses.")
        try:
            PlanEdits.model_validate(e.model_dump())
        except ValueError:
            errors.append("too_many_changes: Your selections or removals exceed this plan's supported limits.")
        return Edit(e, holds, errors, f"Build {label(term)}", touched | selected)
    if k == "swap":
        if state.locked_reason == "anchor":
            errors.append(f"anchor_cannot_be_swapped: {course} is a required anchor "
                          f"({_anchor_why(pc)}); it can only move to another valid term.")
        elif state.locked_reason == "locked":
            errors.append(f"locked: you locked {course} to {label(term)}. Unlock it to swap it.")
        elif change.replacement in where and where[change.replacement] <= term:
            errors.append(f"already_planned: {change.replacement} is already in your plan "
                          f"({label(where[change.replacement])}).")
        else:
            e.locks, e.selections = _without(e.locks, course), _without(e.selections, course)
            e.selections = _without(e.selections, change.replacement) + [
                CoursePin(course=change.replacement, term_code=term, source="selection")]
            e.avoid = [a for a in e.avoid if a.course != change.replacement] + [
                CourseAvoid(course=course, term_code=term)]
            release |= {course, change.replacement} | _dependents(plan, course)
            touched = {course, change.replacement}
            summary = f"Swap {course} for {change.replacement} in {label(term)}"
    elif k == "move":
        if state.locked_reason == "locked":
            errors.append(f"locked: you locked {course} to {label(term)}. Unlock it to move it.")
        elif change.to_term not in {t.term_code for t in plan.terms} | _horizon(plan):
            errors.append(f"term_not_in_plan: {label(change.to_term)} is outside this plan.")
        else:
            anchor = next((p for p in constraints.pins if p.course == course), None)
            pin = CoursePin(course=course, term_code=change.to_term,
                            retake=bool(anchor and anchor.retake) or bool(pc.retake),
                            source="must" if state.locked_reason == "anchor" else "selection")
            e.locks, e.selections = _without(e.locks, course), _without(e.selections, course)
            (e.locks if pin.source == "must" else e.selections).append(pin)
            e.avoid = [a for a in e.avoid if a.course != course]
            release |= {course} | _dependents(plan, course) | _prerequisites(plan, course)
            touched = {course}
            summary = f"Move {course} from {label(term)} to {label(change.to_term)}"
    elif k == "remove":
        if state.locked_reason == "anchor":
            errors.append(f"anchor_cannot_be_removed: {course} is a required anchor "
                          f"({_anchor_why(pc)}).")
        elif state.locked_reason == "locked":
            errors.append(f"locked: you locked {course} to {label(term)}. Unlock it first.")
        else:
            e.selections = _without(e.selections, course)
            e.avoid = [a for a in e.avoid if a.course != course] + [
                CourseAvoid(course=course, term_code=term)]
            release |= {course} | _dependents(plan, course)
            touched = {course}
            summary = f"Remove {course} from {label(term)}"
    elif k == "add":
        if course in where:
            errors.append(f"already_planned: {course} is already in your plan "
                          f"({label(where[course])}).")
        else:
            e.selections = _without(e.selections, course) + [
                CoursePin(course=course, term_code=term, source="selection")]
            e.avoid = [a for a in e.avoid if a.course != course]
            touched = {course}
            summary = f"Add {course} to {label(term)}"
    elif k == "lock":
        chosen = state.locked_reason == "user_selected"
        if state.state is not CourseState.PLANNER_SELECTED and not chosen:
            errors.append(f"already_locked: {course} is already locked ({state.locked_reason}).")
        else:
            e.selections = _without(e.selections, course)
            e.locks = _without(e.locks, course) + [
                CoursePin(course=course, term_code=term, source="lock")]
            summary = f"Lock {course} in {label(term)}"
    elif k == "unlock":
        if state.locked_reason == "anchor":
            errors.append(f"anchor_cannot_be_unlocked: {course} is a required anchor "
                          f"({_anchor_why(pc)}).")
        elif state.state is CourseState.PLANNER_SELECTED:
            errors.append(f"not_locked: {course} is not locked.")
        else:
            e.locks, e.selections = _without(e.locks, course), _without(e.selections, course)
            summary = f"Unlock {course} (CoursePilot may change it)"
    elif k == "lock_term":
        term_plan = next((t for t in plan.terms if t.term_code == term), None)
        if term_plan is None:
            errors.append(f"term_not_in_plan: {label(term)} has no planned courses.")
        else:
            for c in term_plan.courses:
                s = states[(c.course, term)]
                if s.state is CourseState.PLANNER_SELECTED:
                    e.locks.append(CoursePin(course=c.course, term_code=term, source="lock"))
            e.locked_terms = sorted({*e.locked_terms, term})
            summary = f"Lock {label(term)}"
    elif k == "unlock_term":
        if term not in e.locked_terms and not any(p.term_code == term for p in e.locks):
            errors.append(f"not_locked: {label(term)} is not locked.")
        else:
            e.locks = [p for p in e.locks if p.term_code != term or p.source != "lock"]
            e.locked_terms = [t for t in e.locked_terms if t != term]
            summary = f"Unlock {label(term)}"
    elif k == "lighten_term":
        errors += _lighten(plan, constraints, states, e, term, release)
        summary = f"Lighten {label(term)}"
        touched = set(release)
    elif k == "choose_alternative":
        choice = next((c for c in plan.metadata.alternative_choices
                       if c.requirement_code == change.requirement_code), None)
        valid = {a.code for a in [*choice.chosen, *choice.alternatives]} if choice else set()
        if choice is None or not set(change.alternatives) <= valid:
            errors.append(f"invalid_alternative: {change.requirement_code} has no such choice.")
        else:
            e.alternative_selections = {**e.alternative_selections,
                                        change.requirement_code: list(change.alternatives)}
            names = {a.code: a.name for a in [*choice.chosen, *choice.alternatives]}
            summary = "Choose " + " and ".join(names.get(a, a) for a in change.alternatives)
            # A different branch re-plans that branch's courses.
            if set(change.alternatives) != {a.code for a in choice.chosen}:
                release |= set(where)

    if errors:
        return Edit(e, {}, errors, summary, touched)
    holds = {c: t for c, t in where.items() if c not in release}
    return Edit(e, holds, [], summary, touched)


def _horizon(plan: PlanResult) -> set[str]:
    """Terms the plan could use (its own terms and the graduation target)."""
    terms = {t.term_code for t in plan.terms}
    if plan.graduation_target is not None:
        from app.services.planning.terms import planning_terms
        terms |= {t for t in planning_terms(plan.metadata.start_term, 16, include_summer=False, include_winter=False)
                  if t <= plan.graduation_target.term_code}
    return terms


def _anchor_why(pc) -> str:
    if pc.retake:
        return "required retake"
    return pc.role_reason or "you set it in your Musts"


def _lighten(plan, constraints, states, e: PlanEdits, term, release) -> list[str]:
    """Move ONE course out of `term` - the planner-selected course with the
    highest workload weight, a filler before a major course - and lower the
    term's credit range so the planner does not refill it."""
    t = next((x for x in plan.terms if x.term_code == term), None)
    if t is None:
        return [f"term_not_in_plan: {label(term)} has no planned courses."]
    movable = [pc for pc in t.courses
               if states[(pc.course, term)].state is CourseState.PLANNER_SELECTED
               and not pc.corequisite_group]
    if not movable:
        return [f"nothing_to_move: every course in {label(term)} is locked, chosen by you, "
                "or taken with a co-requisite."]
    pick = max(movable, key=lambda pc: (pc.role == "filler",
                                        pc.workload.weight if pc.workload else Decimal(0),
                                        pc.credits, pc.course))
    left = int(t.credits - pick.credits)
    if left < 1:
        return [f"only_course: {pick.course} is the only course {label(term)} could lose."]
    floor = int(t.workload.credit_floor) if t.workload else left
    e.avoid = [a for a in e.avoid if not (a.course == pick.course and a.term_code == term)] + [
        CourseAvoid(course=pick.course, term_code=term)]
    if constraints.workload_level is not None:
        e.term_caps = [c for c in e.term_caps if c.term_code != term] + [
            TermCap(term_code=term, credit_min=min(floor, left), credit_max=left)]
    release |= {pick.course} | _dependents(plan, pick.course)
    return []


# --------------------------------------------------------------------------
# the diff
# --------------------------------------------------------------------------


def _lines(term, states) -> list[CourseLine]:
    return [CourseLine(course=pc.course, title=pc.title, credits=pc.credits,
                       state=states.get((pc.course, term.term_code), CourseState.PLANNER_SELECTED))
            for pc in term.courses]


def _met(result: PlanResult) -> set[str]:
    return {r.requirement_code for r in result.requirements if r.status_after in MET_STATUSES}


def _issue_key(i) -> tuple:
    return (i.code, i.course or "", i.requirement_code or "")


def plan_diff(before: PlanResult, after: PlanResult, before_c: PlanningConstraints,
              after_c: PlanningConstraints, touched: set[str],
              proposed: set[str] = frozenset()) -> PlanDiff:
    sb = {(s.course, s.term_code): s.state for s in course_states(before, before_c)}
    sa = {(s.course, s.term_code): (CourseState.PROPOSED_CHANGE if s.course in proposed
                                    else s.state) for s in course_states(after, after_c)}
    tb = {t.term_code: t for t in before.terms}
    ta = {t.term_code: t for t in after.terms}
    wb, wa = _where(before), _where(after)
    names = {pc.course: pc.title for t in [*before.terms, *after.terms] for pc in t.courses}
    req_names = {r.requirement_code: r.requirement_name for r in after.requirements}
    terms = []
    for code in sorted(set(tb) | set(ta)):
        b, a = tb.get(code), ta.get(code)
        bl = _lines(b, sb) if b else []
        al = _lines(a, sa) if a else []
        bc, ac = {x.course for x in bl}, {x.course for x in al}
        terms.append(TermDiff(
            term_code=code, label=label(code), before=bl, after=al,
            added=sorted(ac - bc), removed=sorted(bc - ac),
            credits_before=(b.credits + b.free_elective_credits) if b else Decimal(0),
            credits_after=(a.credits + a.free_elective_credits) if a else Decimal(0),
            workload_before=b.workload.total if b and b.workload else None,
            workload_after=a.workload.total if a and a.workload else None,
            budget=(a or b).workload.budget if (a or b).workload else None))
    moved = []
    after_reqs = {pc.course: sorted({r.requirement_name for r in pc.requirements})
                  for t in after.terms for pc in t.courses}
    for course in sorted(set(wb) | set(wa)):
        if course in touched or wb.get(course) == wa.get(course):
            continue
        fro, to = wb.get(course), wa.get(course)
        if fro is None:
            why = "now planned" + (f" to fill {', '.join(after_reqs[course])}"
                                   if after_reqs.get(course) else "")
        elif to is None:
            why = "no longer needed in the plan"
        else:
            prereq_moved = [d.course for t in after.terms for pc in t.courses if pc.course == course
                            for d in pc.prerequisite.conditional_on
                            if d.source == "planned" and wb.get(d.course) != wa.get(d.course)]
            why = (f"moved because {', '.join(prereq_moved)} moved" if prereq_moved
                   else "moved to keep its requirements, prerequisites and credit limits")
        moved.append(ForcedMove(course=course, title=names.get(course, ""), from_term=fro,
                                to_term=to, reason=why))
    met_b, met_a = _met(before), _met(after)
    old = {_issue_key(i) for i in before.issues}
    new_issues = [i.message for i in after.issues
                  if i.severity in (Severity.BLOCKER, Severity.NEEDS_CONFIRMATION)
                  and _issue_key(i) not in old]
    last_b = max(tb, default=None)
    last_a = max(ta, default=None)
    lost = sorted(req_names.get(c, c) for c in met_b - met_a)
    gained = sorted(req_names.get(c, c) for c in met_a - met_b)
    over = any(a.workload is not None
               and (a.credits + a.free_elective_credits) > a.workload.credit_max
               for a in after.terms)
    diff = PlanDiff(
        terms=terms, forced_moves=moved,
        graduation_before=before.graduation_target.term_code if before.graduation_target else None,
        graduation_after=after.graduation_target.term_code if after.graduation_target else None,
        last_term_before=last_b, last_term_after=last_a,
        requirements_lost=lost, requirements_gained=gained, new_issues=new_issues)
    diff.big = bool(moved) or bool(lost) or over or bool(new_issues) or (
        last_a is not None and last_b is not None and last_a > last_b)
    diff.summary = _summary(diff, before, after)
    return diff


def _summary(diff: PlanDiff, before: PlanResult, after: PlanResult) -> str:
    parts = []
    for t in diff.terms:
        for c in t.removed:
            if c not in {m.course for m in diff.forced_moves}:
                parts.append(f"{c} out of {t.label}")
        for c in t.added:
            if c not in {m.course for m in diff.forced_moves}:
                parts.append(f"{c} into {t.label}")
    if diff.forced_moves:
        parts.append(f"{len(diff.forced_moves)} other course"
                     f"{'s' if len(diff.forced_moves) != 1 else ''} move")
    if diff.requirements_lost:
        parts.append("no longer completes " + ", ".join(diff.requirements_lost))
    if diff.last_term_after != diff.last_term_before and diff.last_term_after:
        was = label(diff.last_term_before) if diff.last_term_before else "-"
        parts.append(f"last semester {was} -> {label(diff.last_term_after)}")
    else:
        parts.append("graduation unchanged")
    return "; ".join(parts)


# --------------------------------------------------------------------------
# preview / apply / undo
# --------------------------------------------------------------------------


def _row(session: Session, student: Student, *, lock: bool = False) -> StudentPlan:
    q = select(StudentPlan).where(StudentPlan.student_id == student.id)
    if lock and session.get_bind().dialect.name == "postgresql":
        q = q.with_for_update()
    row = session.scalar(q)
    if row is None:
        raise NoSavedPlan("Build your plan first.")
    return row


def profile_constraints(session: Session, student: Student) -> PlanningConstraints:
    from app.domain.planning_profile import to_constraints
    from app.services.planning.profile import get_profile, profile_input

    profile = get_profile(session, student)
    return to_constraints(profile_input(profile)) if profile is not None else PlanningConstraints()


def preview_change(session: Session, student: Student, change: PlanChange, *,
                   now: datetime | None = None) -> PlanPreview:
    """Run the planner with the change; store the result as a PROPOSED change."""
    from app.services.planning.saved import _decode, stale_reasons
    from app.services.planning.service import generate_plan

    now = now or datetime.now(UTC)
    row = _row(session, student)
    if change.kind == "set_term" and change.expected_revision != row.revision:
        raise PlanOutOfDate("Your plan changed while the builder was open. Reopen it before saving.")
    if stale_reasons(session, student, row):
        raise PlanOutOfDate("Your plan is out of date. Rebuild it, then change it.")
    plan = _decode(row.result_blob)
    edits = edits_of(row)
    base = profile_constraints(session, student)
    before_c = plan_constraints(base, edits)
    edit = build_edit(plan, before_c, edits, change)
    expires = now + PREVIEW_TTL
    if edit.errors:
        return PlanPreview(preview_id="", base_revision=row.revision, change=change, valid=False,
                           errors=edit.errors, expires_at=expires)
    if change.kind == "set_term":
        from app.services.planning.options import OptionsContext
        current = {pc.course for t in plan.terms if t.term_code == change.term_code for pc in t.courses}
        added = set(change.courses) - current
        check_constraints = before_c.model_copy(update={"avoid": [a for a in before_c.avoid if a.course not in added]})
        ctx = OptionsContext(session, student, plan, check_constraints)
        ctx.where = {k: t for k, t in ctx.where.items() if t != change.term_code or k in change.courses}
        verdicts = ctx.check(change.term_code, {k: [] for k in added}, replacing=None)
        overridable = {"excluded_by_program", "prerequisites_need_confirmation", "special_permission_required"}
        failures = [f"{v.reason}: {k} cannot be added without a valid gate or explicit supported pin explanation."
                    for k, v in verdicts.items() if not v.ok and not (
                        v.reason in overridable and change.pin_explanations.get(k, "").strip())
                    and not (v.reason == "needs_a_corequisite_in_the_same_term" and len(added) > 1)]
        if failures:
            return PlanPreview(preview_id="", base_revision=row.revision, change=change, valid=False,
                               errors=failures, expires_at=expires)
    after_c = plan_constraints(base, edit.edits)
    result = generate_plan(session, student, start_term=row.start_term, constraints=after_c,
                           holds=edit.holds, prior_choices=plan.metadata.alternative_choices)
    proposed = {c for c in edit.touched if c in _where(result)}
    diff = plan_diff(plan, result, before_c, after_c, edit.touched, proposed)
    errors = _refusals(result, change, before=plan)
    preview = PlanChangePreview(
        id=uuid.uuid4(), student_id=student.id, base_revision=row.revision,
        change=change.model_dump(mode="json"), summary=edit.summary,
        edits=edit.edits.model_dump(mode="json"), program_version_id=row.program_version_id,
        catalog_year=row.catalog_year, constraints=after_c.model_dump(mode="json"),
        planning_engine_version=result.metadata.planning_engine_version,
        audit_engine_version=result.metadata.audit_engine_version,
        academic_fingerprint=result.metadata.academic_fingerprint,
        rules_fingerprint=result.metadata.rules_fingerprint,
        offering_dataset=list(result.metadata.offering_dataset),
        plan_status=result.status.value, result_blob=_encode(result), expires_at=expires)
    if not errors:
        # Old previews of this student are useless once a new one exists.
        session.execute(delete(PlanChangePreview).where(
            PlanChangePreview.student_id == student.id, PlanChangePreview.expires_at < now))
        session.add(preview)
        session.flush()
    return PlanPreview(preview_id=str(preview.id) if not errors else "",
                       base_revision=row.revision, change=change, valid=not errors,
                       errors=errors, diff=diff, expires_at=expires)


def _encode(result: PlanResult) -> bytes:
    from app.services.planning.saved import _encode as encode
    return encode(result)


def _refusals(result: PlanResult, change: PlanChange, *, before: PlanResult) -> list[str]:
    """A change the planner could not honour is refused, never half-applied:
    the course the student asked for is not where they asked."""
    where = _where(result)
    out = []
    if change.kind == "set_term":
        for course in change.courses:
            if where.get(course) != change.term_code:
                why = next((i.message for i in result.issues if i.code == "PIN_NOT_POSSIBLE" and i.course == course), course)
                out.append(f"not_possible: {why} could not be kept in {label(change.term_code)}.")
        removed = {c.course for t in before.terms if t.term_code == change.term_code for c in t.courses} - set(change.courses)
        if any(where.get(c) == change.term_code for c in removed):
            out.append("not_possible: A course you removed stayed in the semester.")
    target = {"swap": (change.replacement, change.term_code),
              "add": (change.course, change.term_code),
              "move": (change.course, change.to_term)}.get(change.kind)
    if target is not None and where.get(target[0]) != target[1]:
        why = next((i.message for i in result.issues
                    if i.code == "PIN_NOT_POSSIBLE" and i.course == target[0]), None)
        out.append("not_possible: " + (why or f"{target[0]} could not be planned in "
                                              f"{label(target[1])}."))
    if change.kind in ("remove", "lighten_term", "swap") and change.course and \
            where.get(change.course) == change.term_code:
        out.append(f"not_possible: {change.course} stays in {label(change.term_code)}.")
    return out


def apply_preview(session: Session, student: Student, preview_id: str, *,
                  now: datetime | None = None):
    """Make the preview the plan - only if the plan is still the one it was
    computed from (same revision, same program) and it has not expired."""
    from app.services.planning.saved import _view

    now = now or datetime.now(UTC)
    row = _row(session, student, lock=True)
    try:
        pid = uuid.UUID(preview_id)
    except ValueError:
        raise StalePreview("That preview does not exist.") from None
    preview = session.get(PlanChangePreview, pid)
    if preview is None or preview.student_id != student.id:
        raise StalePreview("That preview does not exist.")
    expires = preview.expires_at if preview.expires_at.tzinfo else \
        preview.expires_at.replace(tzinfo=UTC)
    if expires < now:
        raise StalePreview("That preview expired. Preview the change again.")
    if preview.base_revision != row.revision or \
            preview.program_version_id != row.program_version_id:
        raise StalePreview("Your plan changed since this preview. Preview the change again.")
    _push_history(session, row, preview.summary)
    for column in SNAPSHOT_COLUMNS:
        if column in ("start_term",):
            continue
        setattr(row, column, getattr(preview, column))
    row.revision += 1
    session.execute(delete(PlanChangePreview).where(PlanChangePreview.student_id == student.id))
    session.flush()
    session.refresh(row)
    return _view(session, student, row)


def _push_history(session: Session, row: StudentPlan, summary: str) -> None:
    snap = StudentPlanHistory(student_id=row.student_id, revision=row.revision,
                              change_summary=summary,
                              **{c: getattr(row, c) for c in SNAPSHOT_COLUMNS})
    session.add(snap)
    session.flush()
    keep = session.scalars(select(StudentPlanHistory.id).where(
        StudentPlanHistory.student_id == row.student_id)
        .order_by(StudentPlanHistory.revision.desc(), StudentPlanHistory.created_at.desc())
        .limit(HISTORY_DEPTH)).all()
    session.execute(delete(StudentPlanHistory).where(
        StudentPlanHistory.student_id == row.student_id, StudentPlanHistory.id.not_in(keep)))


def last_change(session: Session, student: Student) -> str | None:
    """What "Undo last change" would undo (None: nothing)."""
    return session.scalar(select(StudentPlanHistory.change_summary).where(
        StudentPlanHistory.student_id == student.id)
        .order_by(StudentPlanHistory.revision.desc(),
                  StudentPlanHistory.created_at.desc()).limit(1))


def undo(session: Session, student: Student):
    from app.services.planning.saved import _view

    row = _row(session, student, lock=True)
    snap = session.scalar(select(StudentPlanHistory).where(
        StudentPlanHistory.student_id == student.id)
        .order_by(StudentPlanHistory.revision.desc(),
                  StudentPlanHistory.created_at.desc()).limit(1))
    if snap is None:
        raise NothingToUndo("There is no earlier version of your plan.")
    for column in SNAPSHOT_COLUMNS:
        setattr(row, column, getattr(snap, column))
    row.revision += 1
    session.delete(snap)
    session.execute(delete(PlanChangePreview).where(PlanChangePreview.student_id == student.id))
    session.flush()
    session.refresh(row)
    return _view(session, student, row)


def push_history_for_rebuild(session: Session, row: StudentPlan) -> None:
    """A rebuild replaces the plan: the version it replaces stays undoable."""
    _push_history(session, row, "Rebuilt the plan")


__all__ = ["HISTORY_DEPTH", "NoSavedPlan", "NothingToUndo", "PREVIEW_TTL", "PlanOutOfDate",
           "Reselection", "StalePreview", "apply_preview", "build_edit", "course_states",
           "edits_of", "last_change", "needs_reselection", "plan_constraints", "plan_diff",
           "preview_change", "profile_constraints", "undo"]
