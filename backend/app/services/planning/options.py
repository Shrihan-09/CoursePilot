"""Options for a planned course, "more like what worked", and "Your choices" (Prompt 11).

```
course_options(course, term)   A1   other courses for the same requirement(s), and -
                                    for a filler - other open requirements this term
next_term_choices()            A3b  one question per flexible slot of the plan's first term
liked_courses()                A3   the student's own well-going courses (ordering only)
```

Every option passes EVERY gate the planner applies (`PlanningEngine.check_candidates`):
offering evidence, prerequisites with the plan's earlier courses, not taken or
planned, not excluded or avoided, honors and permission gates, section
restrictions, the term's Core codes - plus the term's credit cap after the
swap. A course that fails a gate is never an option. The preview (the planner
re-run with the change) remains the authority for the whole plan.

Ordering, in this order: the student's delivery preference (async first for
"flexible"), fewer workload points when the term is over its budget, more
open requirements filled at once, then similarity to courses that went well
for the student - never "easy" or "hard".

Privacy: grades are read only to order THIS student's own options, only when
their "Use my grades to personalize suggestions" setting is on; a similarity
note names the course and the shared features, never a grade, and nothing
here is logged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.grades import Tri, meets_minimum, outcome
from app.domain.planning import OfferingEvidenceKind, PlanningConstraints, PlanResult
from app.models import Course, ProgramVersion, Student, StudentCourse
from app.services.planning.edits import MET_STATUSES, course_states
from app.services.planning.engine import CandidateVerdict, PlanningEngine
from app.services.planning.terms import label
from app.services.planning.workload import filler_rank

#: "Did well": B or better, by the grade domain (app.domain.grades) - Rutgers
#: letters A, B+, B. No grade rule lives here.
WELL_MINIMUM = "B"
#: Candidates checked per requirement (pre-sorted by offering confidence).
POOL_LIMIT = 60
_WORD = re.compile(r"[a-z]{4,}")
_STOP = {"introduction", "intro", "topics", "principles", "seminar", "special", "studies",
         "advanced", "fundamentals", "elements", "survey"}


@dataclass
class Option:
    course: str
    title: str
    credits: Decimal
    requirement_codes: list[str]
    requirements: list[str]                 # names
    offering: str                           # confirmed | historically offered
    offering_term: str
    workload_weight: Decimal | None
    contact_hours: Decimal | None
    delivery: list[str]
    fills_multiple: bool
    impact: str
    similar_to: str | None = None
    group: str = "same_requirement"         # same_requirement | different_requirement


@dataclass
class OptionGroup:
    kind: str                               # same_requirement | different_requirement
    requirement_code: str
    requirement_name: str
    options: list[Option] = field(default_factory=list)


@dataclass
class CourseOptions:
    course: str
    term_code: str
    title: str
    state: str
    role: str | None
    locked: bool
    locked_reason: str | None
    requirements: list[str]
    groups: list[OptionGroup] = field(default_factory=list)
    move_terms: list[str] = field(default_factory=list)
    can_remove: bool = False
    not_available: list[dict] = field(default_factory=list)


# --------------------------------------------------------------------------
# A3: "more like what worked for me"
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Liked:
    course: str
    title: str
    why: str                    # did_well | doing_well  (never the grade itself)


def uses_grades(session: Session, student: Student) -> bool:
    from app.services.planning.profile import get_profile

    profile = get_profile(session, student)
    return True if profile is None else bool(profile.use_grades_for_suggestions)


def liked_courses(session: Session, student: Student) -> list[Liked]:
    """The student's OWN signals: completed with B or better, or in progress and
    marked "doing well". Empty when the student turned the setting off."""
    if not uses_grades(session, student):
        return []
    rows = session.execute(
        select(Course.course_string, Course.title, StudentCourse.status, StudentCourse.grade,
               StudentCourse.doing_well)
        .join(Course, Course.id == StudentCourse.course_id)
        .where(StudentCourse.student_id == student.id)).all()
    out = []
    for key, title, status, grade, well in rows:
        if status == "completed" and meets_minimum(outcome(status, grade), WELL_MINIMUM) is Tri.YES:
            out.append(Liked(key, title or "", "did_well"))
        elif status == "in_progress" and well:
            out.append(Liked(key, title or "", "doing_well"))
    return sorted(set(out), key=lambda x: x.course)


def _words(title: str) -> set[str]:
    return {w for w in _WORD.findall((title or "").lower()) if w not in _STOP}


@dataclass
class _Features:
    subject: str
    level: int
    delivery: frozenset
    weight: Decimal | None
    goals: frozenset
    words: frozenset


class Similarity:
    """Feature overlap with the student's liked courses: subject, course level,
    delivery, Core goal, workload weight, title words. Catalog descriptions are
    scarce in this corpus (DATA_MODEL section 22) and no embeddings exist, so
    the title stands in for description similarity - stated, not hidden."""

    def __init__(self, engine: PlanningEngine, term: str, liked: list[Liked]) -> None:
        self.engine, self.term = engine, term
        self.liked = [(item, self._features(item.course, item.title)) for item in liked]

    def _features(self, key: str, title: str) -> _Features:
        e = self.engine
        info = e.course_info.get(key)
        weight, delivery = None, frozenset()
        if info is not None and info.credits is not None:
            try:
                w = e.workload_index.weight(info, self.term)
                weight, delivery = w.weight, frozenset(w.delivery)
            except Exception:  # noqa: BLE001 - a missing input is no similarity
                pass
        goals = frozenset(g for (code, k), cats in e.pool_categories.items() if k == key
                          for g in cats if g)
        number = key.split(":")[2] if key.count(":") == 2 else "000"
        return _Features(subject=key.split(":")[1] if ":" in key else "",
                         level=int(number[0]) if number[:1].isdigit() else 0,
                         delivery=delivery, weight=weight, goals=goals,
                         words=frozenset(_words(title or (info.title if info else ""))))

    def best(self, key: str, title: str) -> tuple[int, str | None]:
        """(score, note). 0 / None when nothing is shared."""
        if not self.liked:
            return 0, None
        f = self._features(key, title)
        best_score, best_note = 0, None
        for item, g in self.liked:
            if item.course == key:
                continue
            shared, score = [], 0
            if f.subject and f.subject == g.subject:
                score += 2
                shared.append("same department")
            if f.goals & g.goals:
                score += 2
                shared.append("same Core area")
            if {"async", "online"} & f.delivery & g.delivery:
                score += 1
                shared.append("async" if "async" in f.delivery & g.delivery else "online")
            if f.level and f.level == g.level:
                score += 1
                shared.append(f"{f.level}00-level")
            if f.weight is not None and g.weight is not None and abs(f.weight - g.weight) <= 1:
                score += 1
                shared.append("similar workload")
            common = f.words & g.words
            if common:
                score += 2
                shared.append("similar topic")
            if score > best_score and score >= 3:
                verb = "you did well" if item.why == "did_well" else "you're doing well"
                best_score = score
                best_note = f"Similar to {item.course}, where {verb}: " + ", ".join(shared[:3])
        return best_score, best_note


# --------------------------------------------------------------------------
# A1: options
# --------------------------------------------------------------------------


class OptionsContext:
    """One prepared engine for every options question about one saved plan."""

    def __init__(self, session: Session, student: Student, plan: PlanResult,
                 constraints: PlanningConstraints) -> None:
        self.session, self.student, self.plan, self.c = session, student, plan, constraints
        self.version = session.get(ProgramVersion, student.program_version_id)
        self.engine = PlanningEngine(session, constraints)
        self.engine.prepare(student, self.version)
        self.where = {pc.course: t.term_code for t in plan.terms for pc in t.courses}
        self.states = {(s.course, s.term_code): s for s in course_states(plan, constraints)}
        self._liked: list[Liked] | None = None
        chosen = {ch.requirement_code: [a.code for a in ch.chosen]
                  for ch in plan.metadata.alternative_choices}
        self.inactive = self.engine._inactive_requirements(chosen)
        self.names = {r.requirement_code: r.requirement_name for r in plan.requirements}
        self.open = [r.requirement_code for r in plan.requirements
                     if r.status_before not in MET_STATUSES
                     and r.requirement_code not in self.inactive
                     and self.engine.pools.get(r.requirement_code)]

    @property
    def liked(self) -> list[Liked]:
        if self._liked is None:
            self._liked = liked_courses(self.session, self.student)
        return self._liked

    def term(self, code: str):
        return next((t for t in self.plan.terms if t.term_code == code), None)

    def fills(self, key: str) -> list[str]:
        """Open requirements whose official pool lists the course."""
        return [code for code in self.open if key in self.engine.pools.get(code, ())]

    def check(self, term: str, candidates: dict[str, list[str]], *, replacing: str | None):
        # A candidate the plan holds for a LATER term would move here: it is
        # checked as if not yet planned (its later place is what moves).
        planned = {k: t for k, t in self.where.items()
                   if k != replacing and not (k in candidates and t > term)}
        return self.engine.check_candidates(self.student, self.version, term, planned, candidates)

    def option(self, verdict: CandidateVerdict, term: str, *, group: str, replacing,
               impact: str, similarity: Similarity) -> Option:
        info = self.engine.course_info[verdict.course]
        w = verdict.workload
        ev = verdict.offering
        _, note = similarity.best(verdict.course, info.title)
        codes = list(verdict.requirements)
        return Option(
            course=verdict.course, title=info.title, credits=info.credits,
            requirement_codes=codes, requirements=[self.names.get(c, c) for c in codes],
            offering=("confirmed" if ev.kind is OfferingEvidenceKind.CONFIRMED_IN_TERM
                      else "historically offered"),
            offering_term=term, workload_weight=w.weight if w else None,
            contact_hours=w.contact_hours if w else None,
            delivery=list(w.delivery) if w else [],
            fills_multiple=self.engine._usable(codes) >= 2, impact=impact,
            similar_to=note, group=group)


def _order(options: list[Option], ctx: OptionsContext, term_code: str, replacing,
           similarity: Similarity) -> list[Option]:
    t = ctx.term(term_code)
    pref = ctx.c.filler_preference.value
    budget = t.workload.budget if t is not None and t.workload else None
    base = (t.workload.total if t is not None and t.workload else Decimal(0)) - (
        replacing.workload.weight if replacing is not None and replacing.workload else 0)

    def key(o: Option):
        weight = o.workload_weight or Decimal(0)
        over = budget is not None and base + weight > budget
        score, _ = similarity.best(o.course, o.title)
        return (filler_rank(o.delivery, pref), weight if over else Decimal(0),
                -len(o.requirement_codes), -score, weight, o.course)
    return sorted(options, key=key)


def _caps_ok(ctx: OptionsContext, term_code: str, replacing, credits: Decimal) -> bool:
    t = ctx.term(term_code)
    if t is None:
        return True
    _, cap = ctx.engine._caps(term_code)
    now = t.credits - (replacing.credits if replacing is not None else Decimal(0))
    count = len(t.courses) - (1 if replacing is not None else 0)
    return now + credits <= cap and count + 1 <= ctx.c.max_courses_per_term


def _candidates(ctx: OptionsContext, codes: list[str], term: str, exclude: set[str]) -> list[str]:
    pool = sorted({k for code in codes for k in ctx.engine.pools.get(code, ())} - exclude)
    index = ctx.engine.offerings
    ranked = sorted(pool, key=lambda k: (index.evidence(k, term).kind
                                         is not OfferingEvidenceKind.CONFIRMED_IN_TERM, k))
    return ranked[:POOL_LIMIT * max(1, len(codes))]


def course_options(ctx: OptionsContext, course: str, term_code: str, *,
                   per_group: int = 8, other_requirements: int = 4) -> CourseOptions:
    from app.services.planning.change_intent import CourseNotInPlan

    t = ctx.term(term_code)
    pc = next((x for x in t.courses if x.course == course), None) if t else None
    if pc is None:
        raise CourseNotInPlan(f"{course} is not planned in {term_code}")
    state = ctx.states[(course, term_code)]
    own = sorted({r.requirement_code for r in pc.requirements})
    out = CourseOptions(course=course, term_code=term_code, title=pc.title,
                        state=state.state.value, role=pc.role,
                        locked=state.locked_reason in ("anchor", "locked"),
                        locked_reason=state.locked_reason,
                        requirements=[ctx.names.get(c, c) for c in own])
    out.move_terms = _move_terms(ctx, pc, term_code)
    out.can_remove = not out.locked
    if out.locked:
        # An anchor (or a course the student locked) has no swap; an anchor may
        # move to another valid term.
        if state.locked_reason == "locked":
            out.move_terms = []
        return out
    similarity = Similarity(ctx.engine, term_code, ctx.liked)
    grad = ctx.plan.graduation_target.term_code if ctx.plan.graduation_target else None
    # Courses planned in this term or earlier are not options; one planned
    # LATER is ("take it now instead") and says so.
    exclude = {k for k, t in ctx.where.items() if t <= term_code} | {course}

    # (1) the same requirement(s)
    if own:
        cands = _candidates(ctx, own, term_code, exclude)
        verdicts = ctx.check(term_code, {k: ctx.fills(k) for k in cands}, replacing=course)
        same = []
        for v in verdicts.values():
            if not v.ok or not set(v.requirements) & set(own):
                if not v.ok:
                    out.not_available.append({"course": v.course, "reason": v.reason})
                continue
            credits = ctx.engine.course_info[v.course].credits
            if not _caps_ok(ctx, term_code, pc, credits):
                out.not_available.append({"course": v.course, "reason": "over_credit_cap"})
                continue
            delta = credits - pc.credits
            missing = [ctx.names.get(c, c) for c in own
                       if c in ctx.open and c not in v.requirements]
            impact = ("Same requirement and credits"
                      + (f": graduation stays {label(grad)}" if grad else "")
                      + " - the preview confirms the whole plan." if delta == 0 else
                      f"{'+' if delta > 0 else ''}{delta} credits in {label(term_code)} - the "
                      "preview shows any other change.")
            if missing:
                impact = (f"Doesn't fill {', '.join(missing)} - the planner finds another "
                          "course for it (the preview shows where). " + impact)
            if v.course in ctx.where:
                impact = (f"Already in your plan for {label(ctx.where[v.course])} - it moves "
                          f"here and {course} takes a later semester. " + impact)
            same.append(ctx.option(v, term_code, group="same_requirement", replacing=pc,
                                   impact=impact, similarity=similarity))
        for code in [c for c in own if c in ctx.open]:
            group = OptionGroup("same_requirement", code, ctx.names.get(code, code))
            group.options = _order([o for o in same if code in o.requirement_codes], ctx,
                                   term_code, pc, similarity)[:per_group]
            out.groups.append(group)

    # (2) a different open requirement instead (fillers and electives only)
    # (Fillers and Core/elective picks only: a major course's requirement is
    # the major's, and swapping it for another requirement is not an option.)
    if not any(ctx.engine._is_major_node(c) for c in own):
        later = {r.requirement_code: (o.course, tt.term_code) for tt in ctx.plan.terms
                 if tt.term_code > term_code for o in tt.courses for r in o.requirements}
        others = [c for c in ctx.open if c not in own and not ctx.engine._is_major_node(c)
                  and (c in later or c not in {r.requirement_code for tt in ctx.plan.terms
                                               for o in tt.courses for r in o.requirements})]
        if others:
            cands = _candidates(ctx, others, term_code, exclude)
            verdicts = ctx.check(term_code, {k: ctx.fills(k) for k in cands}, replacing=course)
            for code in others:
                opts = []
                for v in verdicts.values():
                    if not v.ok or code not in v.requirements:
                        continue
                    if not _caps_ok(ctx, term_code, pc,
                                    ctx.engine.course_info[v.course].credits):
                        continue
                    own_name = ", ".join(ctx.names.get(c, c) for c in own) or pc.course
                    freed = later.get(code)
                    impact = (f"Fills {ctx.names.get(code, code)} now; you'd still need "
                              f"{own_name} later - the planner moves it to a later semester "
                              "(the preview shows where)")
                    if freed:
                        impact += (f", and {freed[0]} is no longer needed in "
                                   f"{label(freed[1])}")
                    opts.append(ctx.option(v, term_code, group="different_requirement",
                                           replacing=pc, impact=impact + ".",
                                           similarity=similarity))
                if opts:
                    group = OptionGroup("different_requirement", code, ctx.names.get(code, code))
                    group.options = _order(opts, ctx, term_code, pc, similarity)[:3]
                    out.groups.append(group)
                if len([g for g in out.groups if g.kind == "different_requirement"]) \
                        >= other_requirements:
                    break
    out.not_available = sorted(out.not_available, key=lambda x: x["course"])[:20]
    return out


def _move_terms(ctx: OptionsContext, pc, term_code: str) -> list[str]:
    from app.domain.plan_change import ChangeIntent
    from app.services.planning.change_intent import change_options

    analysis = change_options(ctx.session, ctx.student, ctx.plan, pc.course, term_code)
    move = next(o for o in analysis.options if o.intent is ChangeIntent.MOVE_TO_ANOTHER_TERM)
    return list(move.candidates)


# --------------------------------------------------------------------------
# A3b: "Your choices for <next term>"
# --------------------------------------------------------------------------


@dataclass
class ChoiceQuestion:
    kind: str                     # slot | alternative | already_set | your_choice
    term_code: str
    prompt: str
    course: str | None = None
    title: str | None = None
    groups: list[OptionGroup] = field(default_factory=list)
    requirement_code: str | None = None
    alternatives: list[dict] = field(default_factory=list)
    recommended: list[str] = field(default_factory=list)
    reason: str | None = None


#: Requirement types where the student picks from a list (a Core goal, an
#: elective pool) - as opposed to one named course.
PICK_TYPES = ("choose_n", "credits")


def _flexible(ctx: OptionsContext, pc) -> bool:
    """A flexible slot: a filler, or a planner-chosen course whose requirement
    is a pick-from-a-list one (Core goals, electives) - never a named course."""
    if pc.role == "filler":
        return True
    codes = [r.requirement_code for r in pc.requirements]
    reqs = [ctx.engine.requirements.get(c) for c in codes]
    return bool(codes) and all(r is not None and r.requirement_type in PICK_TYPES
                               and len(ctx.engine.pools.get(r.code, ())) > 1 for r in reqs)


def next_term_choices(ctx: OptionsContext) -> tuple[str | None, list[ChoiceQuestion]]:
    if not ctx.plan.terms:
        return None, []
    first = ctx.plan.terms[0]
    term = first.term_code
    out: list[ChoiceQuestion] = []
    for pc in first.courses:
        s = ctx.states[(pc.course, term)]
        if s.locked_reason == "anchor":
            why = "required retake" if pc.retake else "from your Musts"
            out.append(ChoiceQuestion("already_set", term, f"Already set: {pc.title} ({why})",
                                      course=pc.course, title=pc.title))
        elif s.state.value == "user_selected":
            out.append(ChoiceQuestion("your_choice", term, f"Your choice: {pc.title}",
                                      course=pc.course, title=pc.title))
        elif _flexible(ctx, pc):
            opts = course_options(ctx, pc.course, term, per_group=3, other_requirements=3)
            groups = [g for g in opts.groups if g.options]
            if groups:
                names = [g.requirement_name for g in groups]
                out.append(ChoiceQuestion(
                    "slot", term, ("You have room for one "
                                   + ("Core course" if any(c.startswith("CORE") for c in
                                                           [g.requirement_code for g in groups])
                                      else "course")
                                   + ". Which do you want to knock out first? CoursePilot "
                                     f"picked {pc.course} ("
                                     f"{', '.join(opts.requirements) or 'a requirement'})."),
                    course=pc.course, title=pc.title, groups=groups,
                    reason=", ".join(names)))
    for ch in ctx.plan.metadata.alternative_choices:
        if ch.status in ("planner_recommended", "undecided", "needs_reselection") and \
                len(ch.chosen) + len(ch.alternatives) > 1:
            alts = [{"code": a.code, "name": a.name, "estimated_terms": a.estimated_terms}
                    for a in [*ch.chosen, *ch.alternatives]]
            node = ctx.engine.requirements.get(ch.requirement_code)
            name = node.name if node is not None else ch.requirement_code
            out.append(ChoiceQuestion(
                "alternative", term, f"{name}: which one?", requirement_code=ch.requirement_code,
                alternatives=alts, recommended=[a.code for a in ch.recommended],
                reason=ch.recommendation_reason))
    return term, out


__all__ = ["ChoiceQuestion", "CourseOptions", "Liked", "Option", "OptionGroup",
           "OptionsContext", "Similarity", "course_options", "liked_courses",
           "next_term_choices", "uses_grades"]
