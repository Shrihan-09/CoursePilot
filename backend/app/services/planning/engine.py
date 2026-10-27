"""The Planning Engine (Phase 6.5): a deterministic semester-level COURSE plan.

```
student record ─► DegreeAuditEngine.audit(projected=planned)  "what remains, and does X count?"
                     │  remaining leaf requirements + their eligible courses
                     ▼
                  candidates ─► OfferingIndex.evidence       confirmed / historical / none
                     │        ─► check_proposal (x2)    history alone / if planned pass
                     │        ─► DependencyPlanner      prerequisites to add earlier
                     ▼
                  term-by-term deterministic selection  ─►  PlanResult (+ evidence, issues)
```

Nothing academic is decided here. Requirement satisfaction, allocation,
sharing, quotas, sequences and minimum grades are the Degree Engine's -
called with the plan as PROJECTED in-progress attempts, so planned courses
count exactly as real in-progress work does: provisionally, no grade assumed.
Prerequisites and co-requisites are app.services.course_eligibility's.

## Eligibility states (a planning concept on top of Phase 6.4's tri-state)

    A = check_proposal(history only)            B = check_proposal(history + planned/in-progress
                                                    courses assumed passed, as a HYPOTHESIS)
    A SATISFIED                  -> satisfied_by_history
    B SATISFIED (A not)          -> conditional_on_plan (what must be passed, at what grade)
    B UNKNOWN                    -> needs_confirmation   - NEVER placed
    B UNSATISFIED                -> unsatisfied          - may become plannable via dependencies

## Selection objective (deterministic, greedy - not an optimization claim)

Terms are filled in order. Within a term, eligible placeable candidates are
taken by this key, smallest first, while the load limits allow:

    1. a dependency the plan needs (a prerequisite of a chosen target)  before
       anything else - it unlocks later terms;
    2. more planned courses unlocked by it (descending);
    3. scarcity: the smallest (eligible candidates - remaining need) over the
       requirements it can serve;
    4. confirmed offering in the term before historical evidence;
    5. eligibility from history before conditional eligibility;
    6. more candidate requirement groups served (allocation still rechecked);
    7. the course string (canonical tie-break).

Phase 7.6 adds two validity gates before ranking - section restrictions
(every section of the term closed to the student: excluded, with the reason)
and the term's SOC coreCodes (a Core goal the term's listing does not
certify is not a reason to plan the course) - and two sequencing keys after
the dependency keys: the longest chain of open-requirement courses a course
starts, then "offered in one regular season only". Every planned course
carries a structured `RecommendationTrace`.

A requirement candidate is ACCEPTED only if the Degree Engine, re-run with it,
shows strict progress (more requirement slots/credits met). A course eligible
for two requirements is therefore credited wherever the engine's own
allocation puts it - once - and a course that adds nothing is not planned.
"""

from __future__ import annotations

import re
import uuid
from collections import defaultdict
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.attempts import Attempt
from app.domain.audit import DegreeAuditResult, RequirementResult, RequirementStatus
from app.domain.course_ranking import CourseRankingEvidence
from app.domain.grades import Tri, earns_credit
from app.domain.planning import (
    AlternativeChoice,
    ConditionalDependency,
    CoreCodeEvidence,
    EligibilityState,
    GraduationTarget,
    OfferingEvidence,
    OfferingEvidenceKind,
    OfferingPattern,
    Pace,
    PlacementBasis,
    PlanMetadata,
    PlannedCourse,
    PlanningConstraints,
    PlanningIssue,
    PlanObservation,
    PlanResult,
    PlanStatus,
    PlanTerm,
    PrerequisiteEvidence,
    Reason,
    RecommendationTrace,
    RequirementContribution,
    RequirementProjection,
    RestrictionCheck,
    Severity,
    SourceLabel,
    Strategy,
    TermWorkload,
    TracedRequirement,
    WorkloadEvidence,
)
from app.domain.prerequisites import ConcurrentReq, PrereqStatus, course_keys, from_json
from app.domain.scenario import ScenarioAssumption, ScenarioTarget
from app.models import (
    Course,
    CourseCorequisite,
    CourseOffering,
    CoursePrerequisite,
    CourseSection,
    ProgramRule,
    ProgramVersion,
    Requirement,
    RequirementCourseOption,
    SectionCrossListing,
    Student,
    StudentCourse,
)
from app.services.audit.cache import AUDIT_ENGINE_VERSION, academic_fingerprint, rules_fingerprint
from app.services.audit.engine import DegreeAuditEngine, constraint_counts
from app.services.course_eligibility import EligibilityCheck, check_proposal
from app.services.placement import (
    MET_BY_PLACEMENT,
    PLACEMENT_POLICY_URL,
    REQUIRES_PLACEMENT,
    is_math,
)
from app.services.placement import covered as placement_covered
from app.services.planning.dependencies import UNREADABLE, DependencyPlanner
from app.services.planning.offerings import PLACEABLE, OfferingIndex
from app.services.planning.ranking import rank_key
from app.services.planning.terms import label, planning_terms
from app.services.planning.workload import LEVELS, WorkloadIndex, filler_rank
from app.services.prerequisites import attempts_by_course

LEAF_TYPES = ("course", "choose_n", "credits")


class InvalidAlternativeSelection(ValueError):
    """Student intent names a choice outside this requirement tree."""


def _needs_reselection(choice) -> PlanningIssue:
    names = ", ".join(a.name for a in choice.chosen if a.code in choice.unavailable) or \
        ", ".join(a.name for a in choice.chosen)
    return PlanningIssue(
        severity=Severity.NEEDS_CONFIRMATION, code="ALTERNATIVE_NEEDS_RESELECTION",
        requirement_code=choice.requirement_code,
        message=(f"The alternative you chose ({names}) cannot be completed in this plan. "
                 "Choose another - CoursePilot keeps your choice and does not switch it."),
        details={"selected": choice.selected, "unavailable": choice.unavailable,
                 "recommended": [a.code for a in choice.recommended]})

#: Phase 7.3: regular (Fall/Spring) semesters from entry to graduation per
#: graduation target. CoursePilot settings, not Rutgers policy.
GRADUATION_SEMESTERS = {Pace.BALANCED: 8, Pace.FINISH_EARLIER: 7, Pace.LIGHTER: 10}


SEASON = {"1": "Spring", "9": "Fall"}


def _next_regular(term: str) -> str:
    year, s = int(term[:4]), term[4]
    return f"{year}9" if s in ("0", "1", "7") else f"{year + 1}1"


def _regular(term: str) -> str:
    """The term itself when Fall/Spring, else the next Fall/Spring."""
    return term if term[4] in ("1", "9") else _next_regular(term)

#: Phase 7.3: a GENERIC topics course - one course number whose content is a
#: per-term topic (SOC section subtitle), e.g. "TOPICS IN COMP SCI",
#: "TPCS IN COMPUTER SCI", "TPC COMPUTER SCIENCE", "SPECIAL TOPICS". Read from
#: the SOC title only; 580 of the loaded courses match.
_TOPICS_TITLE = re.compile(r"\b(TOPICS?|TPCS?)\b", re.I)


def is_topics_title(title: str | None) -> bool:
    return bool(title and _TOPICS_TITLE.search(title))
MET = (RequirementStatus.SATISFIED, RequirementStatus.PROVISIONALLY_SATISFIED)


# --------------------------------------------------------------------------
# reading the Degree Engine's result
# --------------------------------------------------------------------------


def active_leaves(results: list[RequirementResult], choices=None) -> list[RequirementResult]:
    """Unmet leaf requirements, honouring alternatives.

    An ANY_OF opens only the number of children still needed. The planner
    supplies persistent choices ranked by record progress and feasibility;
    callers without a catalog use progress then deterministic code order.
    """
    out: list[RequirementResult] = []

    def walk(node: RequirementResult) -> None:
        if node.status in MET or node.status is RequirementStatus.NOT_EVALUABLE:
            return
        if node.requirement_type in LEAF_TYPES:
            out.append(node)
            return
        children = node.children
        if node.requirement_type == "any_of":
            need = max((node.needed_count or 1) - sum(c.status in MET for c in children), 0)
            open_ = sorted((c for c in children if c.status not in MET),
                           key=lambda c: (-_progress_of(c), c.requirement_code))
            selected = (choices or {}).get(node.requirement_code)
            children = ([c for c in open_ if c.requirement_code in selected][:need]
                        if selected is not None else open_[:need])
        for child in children:
            walk(child)

    for root in results:
        walk(root)
    return sorted(out, key=lambda r: r.requirement_code)


def _progress_of(node: RequirementResult) -> Decimal:
    if node.requirement_type == "credits":
        return min(node.satisfied_credits, node.needed_credits or Decimal(0))
    return Decimal(min(node.satisfied_count, node.needed_count or 0))


def deficit(node: RequirementResult) -> Decimal:
    if node.requirement_type == "credits":
        return max((node.needed_credits or Decimal(0)) - node.satisfied_credits, Decimal(0))
    missing = (node.needed_count or 1) - node.satisfied_count
    # Count met but a constraint (category, level, subject) is not: one more course.
    return Decimal(max(missing, 1))


def progress(result: DegreeAuditResult, requirements: dict | None = None, *,
             useful_only: bool = False) -> tuple:
    """Measure of degree progress, used to accept a candidate.

    (leaf requirements met, USEFUL units toward them): courses or credits
    counted, capped at the need, plus distinct categories (capped). For a
    choose-N with subject/level constraints (the Degree Engine's own
    predicates, `constraint_counts`), a course counts only if the slot it
    fills can still be part of a valid selection: with N needed and L at the
    level, at most N - L courses below the level are useful; courses outside
    the subject beyond the allowance are not. Without this, six 200-level
    courses would look like full progress on "six electives, three at the
    300 level" and leave no slot for the 300-level ones.
    """
    met, amount = 0, Decimal(0)
    leaves = (_useful_leaves(result.requirements) if useful_only else
              (n for n in _walk(result.requirements) if n.requirement_type in LEAF_TYPES))
    for node in leaves:
        met += node.status in MET
        units = _progress_of(node)
        if node.needed_distinct_categories and node.needed_count:
            # Same rule for "N courses covering K distinct categories": at
            # most N - K courses may repeat an already-covered category.
            counted = min(node.satisfied_count, node.needed_count)
            repeats = counted - min(node.distinct_categories, counted)
            units -= max(0, repeats - (node.needed_count - node.needed_distinct_categories))
            units += min(node.distinct_categories, node.needed_distinct_categories)
        req = (requirements or {}).get(node.requirement_code)
        if req is not None and node.requirement_type == "choose_n" and node.needed_count:
            allocated = [{"subject_code": ref.course_string.split(":")[1],
                          "course_number": ref.course_string.split(":")[2]}
                         for ref in node.allocated_courses]
            counts = constraint_counts(req, allocated)
            count = len(allocated)
            if counts["at_level"] is not None:
                below = count - counts["at_level"]
                units -= max(0, below - (node.needed_count - req.min_at_level_count))
            if counts["outside"] is not None:
                units -= max(0, counts["outside"] - req.max_outside_subject)
        amount += units
    return (met, amount)


def _leaf_units(node: RequirementResult) -> tuple:
    return (int(node.status in MET), _progress_of(node))


def _subtree_score(node: RequirementResult) -> tuple:
    """(leaves met, units) of the leaves `progress` would count under `node`."""
    met, units = 0, Decimal(0)
    for leaf in _useful_leaves([node]):
        m, u = _leaf_units(leaf)
        met, units = met + m, units + u
    return (met, units)


def _useful_leaves(results):
    """Leaves whose progress is USEFUL in a finished plan (Phase 7.6). Once an
    ANY_OF is MET, only the met alternatives it needs count, so a course
    credited only to another alternative (Physics 193 or Chemistry 161 beside a
    completed Physics 203-206 sequence) is pruned. An unmet ANY_OF keeps every
    alternative: the search must stay free to finish another one when the
    first is a dead end (an unplannable lab)."""
    for node in results:
        if node.requirement_type in LEAF_TYPES:
            yield node
            continue
        children = node.children
        if node.requirement_type == "any_of" and node.status in MET and children:
            need = node.needed_count or 1
            met = [c for c in children if c.status in MET]
            children = sorted(met, key=lambda c: (
                tuple(-x for x in _subtree_score(c)), c.requirement_code))[:need]
        yield from _useful_leaves(children)


def _partner_may_help(co) -> bool:
    """A co-requisite a same-term partner could satisfy: unmet, or UNKNOWN only
    because Rutgers publishes it on some sections (Phase 6.6.1). Any other
    UNKNOWN stays needs-confirmation."""
    return co.status is PrereqStatus.UNSATISFIED or (
        co.status is PrereqStatus.UNKNOWN and co.reasons == ["corequisite_on_some_sections"])


def _walk(results):
    for node in results:
        yield node
        yield from _walk(node.children)


# --------------------------------------------------------------------------
# the engine
# --------------------------------------------------------------------------


class PlanningEngine:
    def _active(self, results):
        return active_leaves(results, getattr(self, "alternative_choices", {}))

    def _competing_courses(self, results):
        blocked, chosen = set(), set()
        inactive = self._inactive_requirements(self.alternative_choices)
        for node in _walk(results):
            selection = self.alternative_choices.get(node.requirement_code)
            if selection is None:
                continue
            for child in node.children:
                for leaf in _walk([child]):
                    target = blocked if leaf.requirement_code in inactive else chosen
                    target.update(self.pools.get(leaf.requirement_code, ()))
        return blocked - chosen

    def _alternative_terms(self, node, student, version, terms, planned):
        """Estimate completion by replaying eligibility and projected audits.

        This is branch-local lookahead, not a promise about the whole plan's
        workload. Unknown rules never become feasible in the replay.
        """
        trial = dict(planned)
        leaves = [n for n in _walk([node]) if n.requirement_type in LEAF_TYPES]
        if any(n.status not in MET and not any(
                self.offerings.terms.get(k) for k in self.pools.get(n.requirement_code, ()))
               for n in leaves):
            return None
        keys = set().union(*(self.pools.get(n.requirement_code, set()) for n in leaves))
        self._load_closure(keys)
        # Every alternative of one choice starts from the same record, so one
        # dependency planner (and its memo) serves them all.
        available = frozenset(self.passed | set(self.in_progress) | set(trial))
        deps = getattr(self, "_lookahead_deps", None)
        dep = deps.get(available) if deps is not None else None
        if dep is None:
            dep = DependencyPlanner(self._expression_of, set(available),
                                    plannable=self._plannable)
            if deps is not None:
                deps[available] = dep
        dependencies = set()
        for key in sorted(keys):
            path = dep.path_for(key)
            if path.feasible:
                dependencies.update(path.courses)
        keys |= dependencies
        self._load_courses(keys)
        for index, term in enumerate(terms, 1):
            previous_size = len(trial)
            pool = sorted(k for k in keys if k not in trial and k not in self.passed
                          and k not in self.in_progress and k not in self.excluded)
            if not pool:
                break
            evaluated = self._evaluate(student, pool, term, trial)
            credits, count = Decimal(0), 0
            for key in pool:
                if key in trial or key not in self.course_info:
                    continue
                a, b, ev = evaluated[key]
                if ev.kind not in PLACEABLE or b is None:
                    continue
                group = {key}
                if b.status is not PrereqStatus.SATISFIED:
                    if not _partner_may_help(b.corequisite):
                        continue
                    partner = self._partner(student, key, b, term, trial)
                    if partner is None:
                        continue
                    group.add(partner)
                proposed = frozenset(group)
                group -= set(trial)
                if any(self.course_info[k].credits is None or
                       self._restriction(term, k).outcome == "not_satisfied" for k in group):
                    continue
                size = sum((self.course_info[k].credits for k in group), Decimal(0))
                if (credits + size > self._caps()[1]
                        or count + len(group) > self.constraints.max_courses_per_term):
                    continue
                checks = self._evaluate(student, sorted(group), term, trial, proposed)
                if any(checks[k][1] is None or checks[k][1].status is not
                       PrereqStatus.SATISFIED for k in group):
                    continue
                trial.update({k: term for k in group})
                credits += size
                count += len(group)
            if len(trial) == previous_size:
                continue
            choices = dict(self.alternative_choices)
            req = self.requirements[node.requirement_code]
            parent = self.by_id_code.get(req.parent_id)
            if parent:
                choices[parent] = [node.requirement_code]
            result = self._audit(student, version, trial, choices=choices)
            projected = next(n for n in _walk(result.requirements)
                             if n.requirement_code == node.requirement_code)
            if projected.status in MET:
                # Actual extra leaf progress respects allocation caps and sharing.
                inside = {n.requirement_code for n in _walk([node])}
                self._branch_benefit[node.requirement_code] = sum(
                    max(Decimal(0), _progress_of(n) - self._record_progress.get(
                        n.requirement_code, Decimal(0)))
                    for n in _walk(result.requirements)
                    if n.requirement_type in LEAF_TYPES and n.requirement_code not in inside)
                return index
        return None

    def _estimate(self, child, student, version, terms, planned):
        """`_alternative_terms`, cached for one `plan()` call: the key is every
        input that can change it (the child, the planned courses, the choices
        made so far), so a retry reuses only estimates that cannot differ."""
        cache = getattr(self, "_alternative_cache", None)
        if cache is None:
            return self._alternative_terms(child, student, version, terms, planned)
        key = (child.requirement_code, frozenset(planned.items()),
               tuple(sorted((k, tuple(v)) for k, v in self.alternative_choices.items())))
        if key not in cache:
            self._branch_benefit.pop(child.requirement_code, None)
            cost = self._alternative_terms(child, student, version, terms, planned)
            cache[key] = (cost, self._branch_benefit.get(child.requirement_code))
        cost, benefit = cache[key]
        if benefit is not None:
            self._branch_benefit[child.requirement_code] = benefit
        return cost

    def _choose_alternatives(self, current, student, version, terms, planned):
        """Commit each one-of requirement to the children it needs.

        A student's explicit choice is always held - never switched. If it
        cannot be completed it is marked `needs_reselection` and the student
        must choose again. Without a choice the planner recommends one feasible
        alternative (`planner_recommended`), with the reason; with no feasible
        alternative and no choice the requirement is `undecided`.
        """
        blocked = getattr(self, "_blocked_alternatives", set())
        for node in _walk(current.requirements):
            if node.requirement_type != "any_of":
                continue
            need = max((node.needed_count or 1) - sum(c.status in MET for c in node.children), 0)
            costs = {c.requirement_code: (0 if c.status in MET else self._estimate(
                c, student, version, terms, planned) if need else None) for c in node.children}
            previous = self.alternative_choices.get(node.requirement_code, [])
            requested = self.constraints.alternative_selections.get(node.requirement_code, [])
            feasible = [c for c in node.children if c.status not in MET
                        and costs.get(c.requirement_code) is not None
                        and (node.requirement_code, c.requirement_code) not in blocked]
            def rank(child, costs=costs, node=node):
                # Initial audit progress is confirmed/provisional record progress.
                started = self._record_progress.get(child.requirement_code, Decimal(0)) > 0
                benefit = self._branch_benefit.get(child.requirement_code, Decimal(0))
                order = self.requirements[child.requirement_code].sort_order
                return (not started, costs[child.requirement_code], -benefit, order,
                        next(i for i, c in enumerate(node.children) if c is child))
            retained = [c for c in feasible if c.requirement_code in previous]
            recommended = (retained + sorted([c for c in feasible if c not in retained],
                                             key=rank))[:need]
            reason = self._recommendation_reason(recommended, feasible, rank, costs)
            met = [c for c in node.children if c.status in MET][:(node.needed_count or 1)]
            if requested:
                chosen = [c for c in node.children
                          if c.requirement_code in requested and c not in met]
                impossible = [c for c in chosen if c not in feasible]
                status = "needs_reselection" if impossible else "student_selected"
            elif recommended or not need:
                chosen, impossible = recommended, []
                status = "planner_recommended"
            else:
                # Nothing can be completed and nothing was chosen. One branch
                # stays open so the plan can say why; it is never planned
                # alongside another.
                chosen = sorted(node.children, key=lambda c: (
                    -self._record_progress.get(c.requirement_code, Decimal(0)),
                    self.requirements[c.requirement_code].sort_order))[:need]
                impossible = list(chosen)
                status = "undecided"
                reason = ("No alternative can be completed within this plan's horizon; "
                          "choose one when your options change.")
            selected = met + chosen
            codes = [c.requirement_code for c in selected]
            self.alternative_choices[node.requirement_code] = codes
            def entry(c, costs=costs):
                return {"code": c.requirement_code, "name": c.requirement_name,
                        "estimated_terms": costs.get(c.requirement_code)}
            self._choice_metadata[node.requirement_code] = {
                "requirement_code": node.requirement_code,
                "status": status,
                "selected": requested,
                "chosen": [entry(c) for c in selected],
                "recommended": [entry(c) for c in recommended],
                "recommendation_reason": reason,
                "unavailable": [c.requirement_code for c in impossible],
                "alternatives": [entry(c) for c in node.children if c not in selected],
                "fallback_from": [code for code in previous if code not in codes]}

    def _recommendation_reason(self, recommended, feasible, rank, costs) -> str | None:
        """Why the planner recommends what it does, from the ranking it used."""
        if not recommended:
            return None
        best = recommended[0]
        key = rank(best)
        names = ", ".join(c.requirement_name for c in recommended)
        if not key[0]:
            return f"Recommended {names}: you have already started it."
        cost = costs[best.requirement_code]
        ties = [c for c in feasible if c not in recommended and rank(c)[:2] == key[:2]]
        terms = f"{cost} term{'s' if cost != 1 else ''}"
        if not ties:
            return f"Recommended {names}: it can be completed soonest (about {terms})."
        if key[2] < 0 and all(rank(c)[2] > key[2] for c in ties):
            return (f"Recommended {names}: as fast as the others (about {terms}) and its "
                    "courses also count toward other requirements.")
        return (f"Recommended {names}: as fast as the others (about {terms}); equally good "
                "alternatives are offered in the catalog's order.")

    def __init__(self, session: Session, constraints: PlanningConstraints | None = None,
                 status_overlay=None) -> None:
        self.session = session
        self.constraints = constraints or PlanningConstraints()
        #: Phase 7.6: the DN overlay, read again for each recommendation's trace.
        self.overlay = status_overlay or None
        if status_overlay:
            # Phase 7.4: a confirmed Degree Navigator audit decides the STATUS of
            # its mapped requirements in every audit this plan reads.
            from app.services.degree_audit.overlay import OverlaidAuditEngine
            self.audit_engine = OverlaidAuditEngine(session, status_overlay)
        else:
            self.audit_engine = DegreeAuditEngine(session)
        self.audit_engine.baseline_memo = {}

    # ---- data, loaded in bulk ------------------------------------------

    def _load_static(self, version: ProgramVersion) -> None:
        s = self.session
        from app.services.academic_context import composed_requirements
        self.requirements = {r.code: r for r in composed_requirements(s, version)}
        self.pools: dict[str, set[str]] = defaultdict(set)
        self.course_info: dict[str, Course] = {}
        #: Phase 7.6: (requirement, course) -> the certifying categories (SAS
        #: Core goal codes; '' for a major requirement).
        self.pool_categories: dict[tuple[str, str], set[str]] = defaultdict(set)
        for code, category, course in s.execute(
                select(Requirement.code, RequirementCourseOption.category, Course)
                .join(RequirementCourseOption,
                      RequirementCourseOption.requirement_id == Requirement.id)
                .join(Course, Course.id == RequirementCourseOption.course_id)
                .where(Requirement.id.in_([r.id for r in self.requirements.values()]))).all():
            self.pools[code].add(course.course_string)
            self.pool_categories[(code, course.course_string)].add(category or "")
            self.course_info[course.course_string] = course
        # Phase 7.6: the Degree Engine's own sharing rule (one course may count
        # once per allocation scope only when the program or its school Core
        # set shares across systems).
        from app.models.requirements import SharingPolicy
        from app.services.academic_context import school_requirement_sets
        shared = SharingPolicy.SHARE_ACROSS_SYSTEMS.value
        self.share = (version.sharing_policy == shared
                      or any(v.sharing_policy == shared
                             for v in school_requirement_sets(s, version)))
        self.by_id_code = {r.id: r.code for r in self.requirements.values()}
        self._restriction_cache: dict[tuple[str, str], RestrictionCheck] = {}
        self._core_cache: dict[tuple[str, str], object] = {}
        self._chain: dict[str, int] | None = None
        self._required: set[str] = set()
        self._offering_sources: dict[tuple[str, str], object] | None = None
        # Phase 7.3 workload weights. A Rutgers major's program code is its
        # subject code (198 Computer Science, 640 Mathematics, 220 Economics).
        self.major_subject = version.program.code if version.program else None
        self.workload_index = WorkloadIndex(s, major_subject=self.major_subject,
                                            natural_science=set(self.pools.get("CORE_NS", ())))
        self.excluded: set[str] = set()
        for rule in s.scalars(select(ProgramRule).where(
                ProgramRule.program_version_id == version.id,
                ProgramRule.rule_type == "course_exclusion")):
            self.excluded |= set(rule.excluded_courses)
        self.offerings = OfferingIndex(s, set(self.course_info))
        self._looked_up: set[str] = set(self.course_info)
        self._closed: set[str] = set()       # prerequisite closure already loaded
        self.prereq_rows: dict[str, dict[str, CoursePrerequisite]] = defaultdict(dict)
        self._load_prerequisites(set(self.course_info))
        self._breadth: dict[str, int] | None = None
        self.placement_covers: frozenset[str] = frozenset()     # set per student (_history)
        #: course -> courses it is cross-listed with (the SAME class under
        #: another number, e.g. 01:198:417 / 16:198:545 in Fall 2026).
        self.twins: dict[str, set[str]] = defaultdict(set)
        self._load_twins(set(self.course_info))

    def _load_courses(self, keys: set[str]) -> None:
        missing = sorted(k for k in keys if k not in self.course_info and k not in self._looked_up)
        self._looked_up |= set(missing)
        if missing:
            for course in self.session.scalars(select(Course).where(
                    Course.course_string.in_(missing), Course.supplement_code == "")):
                self.course_info.setdefault(course.course_string, course)
            self.offerings.extend(self.session, set(missing))
            self._load_prerequisites(set(missing))
            self._load_twins(set(missing))

    def _load_twins(self, keys: set[str]) -> None:
        """Cross-listings of `keys` in any loaded term, both directions, one query."""
        partner = func.concat(SectionCrossListing.offering_unit_code, ":",
                              SectionCrossListing.subject_code, ":",
                              SectionCrossListing.course_number)
        for key, other in self.session.execute(
                select(Course.course_string, partner)
                .join(CourseOffering, CourseOffering.course_id == Course.id)
                .join(CourseSection, CourseSection.offering_id == CourseOffering.id)
                .join(SectionCrossListing, SectionCrossListing.section_id == CourseSection.id)
                .where(Course.course_string.in_(sorted(keys)) | partner.in_(sorted(keys)))
                .distinct()):
            if other and other != key:
                self.twins[key].add(other)
                self.twins[other].add(key)

    def _twin_taken(self, key: str, planned: dict[str, str]) -> bool:
        """Its cross-listed twin is already planned, passed or in progress."""
        return any(t in planned or t in self.passed or t in self.in_progress
                   for t in self.twins.get(key, ()))

    def _is_topics(self, key: str) -> bool:
        course = self.course_info.get(key)
        return course is not None and is_topics_title(course.title)

    def _topics(self, key: str, term: str) -> list[str]:
        """The topic labels (section subtitles) Rutgers published for `term`."""
        return sorted({(t or "").strip() for t in self.session.scalars(
            select(CourseSection.subtitle)
            .join(CourseOffering, CourseOffering.id == CourseSection.offering_id)
            .join(Course, Course.id == CourseOffering.course_id)
            .where(Course.course_string == key, CourseSection.term_code == term))} - {""})

    def _load_closure(self, keys: set[str]) -> None:
        """Courses `keys` may need, transitively - one batch per prerequisite
        LEVEL, not one query per course (dependency search would otherwise
        load each referenced course as it meets it)."""
        frontier = set(keys) - self._closed
        while frontier:
            self._closed |= frontier
            self._load_courses(frontier)
            referenced: set[str] = set()
            for key in sorted(frontier):
                expr = self._expression_of(key)
                if expr is not None and expr is not UNREADABLE:
                    referenced |= set(course_keys(expr))
            frontier = referenced - self._closed

    def _load_prerequisites(self, keys: set[str]) -> None:
        """Each course's MOST RECENT published prerequisite row only - older
        terms are offering evidence, never a source of rules (Phase 7.3)."""
        latest = (select(CoursePrerequisite.course_id,
                         func.max(CoursePrerequisite.term_code).label("term_code"))
                  .join(Course, Course.id == CoursePrerequisite.course_id)
                  .where(Course.course_string.in_(sorted(keys)))
                  .group_by(CoursePrerequisite.course_id).subquery())
        for key, row in self.session.execute(
                select(Course.course_string, CoursePrerequisite)
                .join(Course, Course.id == CoursePrerequisite.course_id)
                .join(latest, (latest.c.course_id == CoursePrerequisite.course_id)
                      & (latest.c.term_code == CoursePrerequisite.term_code))).all():
            self.prereq_rows[key][row.term_code] = row

    def _system(self, code: str) -> str:
        req = self.requirements.get(code)
        return (req.allocation_scope or req.requirement_system) if req is not None else "major"

    def _is_core(self, code: str) -> bool:
        """A school Core node. Its ALLOCATION scope is finer ("core:writing",
        "core:natural-sciences"), so `_system` is not the test (Phase 7.6)."""
        req = self.requirements.get(code)
        return req is not None and req.requirement_system == "core"

    def _unlock_breadth(self) -> dict[str, int]:
        """How many requirement-pool courses cite each course in their latest
        published prerequisites (7.2.4 UNLOCK_FUTURE_OPTIONS evidence). Counts
        published rules only - never a guess about future offerings."""
        if self._breadth is None:
            counts: dict[str, int] = defaultdict(int)
            for course in sorted(set().union(*self.pools.values()) if self.pools else ()):
                rows = self.prereq_rows.get(course)
                if not rows:
                    continue
                row = rows[max(rows)]
                if row.expression is None:
                    continue
                for ref in set(course_keys(from_json(row.expression))) - {course}:
                    counts[ref] += 1
            self._breadth = dict(counts)
        return self._breadth

    def _expression_of(self, course: str):
        """Latest published prerequisite IR (for dependency PATHS only)."""
        self._load_courses({course})
        rows = self.prereq_rows.get(course)
        if not rows:
            return None
        row = rows[max(rows)]
        if course in self.placement_covers and is_math(course):
            return None         # prerequisites met by placement: nothing below it is needed
        if row.expression is None:
            return UNREADABLE
        expr = from_json(row.expression)
        lists = (row.interpreted_conditions or {}).get("prerequisite_lists", ())
        if lists:
            from app.domain.prerequisites import AllOf, AnyOf, CourseReq
            expr = AllOf((expr, *(AnyOf(tuple(CourseReq(k) for k in codes)) for codes in lists)))
        return expr

    def _plannable(self, course: str) -> str | None:
        self._load_courses({course})
        if course not in self.course_info:
            return "not_in_catalog"
        if not self.offerings.terms.get(course):
            return "no_offering_evidence"
        if course in self.excluded:
            return "excluded_by_program"
        return None

    # ---- history ---------------------------------------------------------

    def _history(self, student: Student) -> None:
        self.history = attempts_by_course(self.session, student)
        self.placement_covers = placement_covered(self.session, student.math_placement)
        self.passed: set[str] = set()
        self.in_progress: dict[str, str] = {}
        for key, attempts in self.history.items():
            for a in attempts:
                if a.status == "completed" and earns_credit(a.outcome) is Tri.YES:
                    self.passed.add(key)
                elif a.status == "in_progress":
                    self.in_progress[key] = a.term_code
        #: Passed courses the plan must repeat: a target needs a higher grade.
        self.retakes: set[str] = set()
        self.last_term = max((a.term_code for atts in self.history.values() for a in atts),
                             default=None)

    # ---- evaluation ------------------------------------------------------

    def _projected(self, student: Student, planned: dict[str, str]) -> tuple:
        rows = []
        for key, term in sorted(planned.items()):
            course = self.course_info[key]
            rows.append((StudentCourse(id=uuid.uuid4(), student_id=student.id, course_id=course.id,
                                       term_code=term, status="in_progress", grade=None,
                                       credits_earned=None, credit_origin="rutgers"), course))
        return tuple(rows)

    def _inactive_requirements(self, selections):
        excluded = set()
        for code, requirement in self.requirements.items():
            child = requirement
            while child.parent_id in self.by_id_code:
                parent_code = self.by_id_code[child.parent_id]
                if parent_code in selections and child.code not in selections[parent_code]:
                    excluded.add(code)
                    break
                child = self.requirements[parent_code]
        return frozenset(excluded)

    def _audit(self, student, version, planned, *, choices=None) -> DegreeAuditResult:
        selections = self.alternative_choices if choices is None else choices
        return self.audit_engine.audit(student, program_version=version,
                                       projected=self._projected(student, planned),
                                       excluded_requirements=self._inactive_requirements(selections))

    def _hypothesis(self, planned: dict[str, str], term: str) -> list[Attempt]:
        """Planned-earlier and in-progress courses ASSUMED passed - for the
        conditional evaluation only; the result is never labelled satisfied."""
        out = [Attempt(k, t, "completed", "A") for k, t in sorted(planned.items()) if t < term]
        out += [Attempt(k, t, "completed", "A") for k, t in sorted(self.in_progress.items())]
        return out

    def _evaluate(self, student, keys: list[str], term: str, planned: dict[str, str],
                  proposed: frozenset[str] = frozenset()) -> dict[str, tuple]:
        """(A, B, evidence) per course: history-only and hypothetical checks,
        batched per rules term - a fixed number of queries per group."""
        by_rules: dict[str, list[str]] = defaultdict(list)
        evidence: dict[str, OfferingEvidence] = {}
        for key in keys:
            ev = self.offerings.evidence(key, term)
            evidence[key] = ev
            if ev.kind in PLACEABLE:
                by_rules[ev.rules_term].append(key)
        hyp = self._hypothesis(planned, term)
        out: dict[str, tuple] = {}
        for rules_term in sorted(by_rules):
            group = sorted(by_rules[rules_term])
            a = check_proposal(self.session, student, group, rules_term, proposed,
                               as_of_term=term, independent=True)
            b = check_proposal(self.session, student, group, rules_term, proposed,
                               projected=hyp, as_of_term=term, independent=True)
            for key in group:
                out[key] = (a[key], b[key], evidence[key])
        for key in keys:
            out.setdefault(key, (None, None, evidence[key]))
        return out

    @staticmethod
    def _state(a: EligibilityCheck | None, b: EligibilityCheck | None) -> EligibilityState:
        if a is None or b is None:
            return EligibilityState.UNSATISFIED
        if a.status is PrereqStatus.SATISFIED:
            return EligibilityState.SATISFIED_BY_HISTORY
        if b.status is PrereqStatus.SATISFIED:
            return EligibilityState.CONDITIONAL_ON_PLAN
        if b.status is PrereqStatus.UNKNOWN or a.status is PrereqStatus.UNKNOWN:
            return EligibilityState.NEEDS_CONFIRMATION
        return EligibilityState.UNSATISFIED

    @staticmethod
    def _awaits_placement(b: EligibilityCheck | None) -> bool:
        """Unknown ONLY because no math placement is stated (app.services.placement):
        placed and marked needs-confirmation, never a blocked chain."""
        return (b is not None and b.status is PrereqStatus.UNKNOWN
                and b.prerequisite.reasons == [REQUIRES_PLACEMENT]
                and (not b.corequisite.has_corequisite
                     or b.corequisite.status is PrereqStatus.SATISFIED))

    def _settle(self, key: str, state: EligibilityState, b: EligibilityCheck | None,
                planner: DependencyPlanner) -> EligibilityState:
        """A course awaiting a placement nobody stated: when its published
        prerequisite path can be planned, plan THAT (it is unmet for now);
        only when it cannot is the course placed, flagged needs-confirmation."""
        if (state is EligibilityState.NEEDS_CONFIRMATION and self._awaits_placement(b)
                and planner.path_for(key).feasible):
            return EligibilityState.UNSATISFIED
        return state

    def _placeable_state(self, state: EligibilityState, b: EligibilityCheck | None) -> bool:
        return state in (EligibilityState.SATISFIED_BY_HISTORY,
                         EligibilityState.CONDITIONAL_ON_PLAN) or (
            state is EligibilityState.NEEDS_CONFIRMATION and self._awaits_placement(b))

    def _prereq_evidence(self, a, b, state, planned, term) -> PrerequisiteEvidence:
        ev = PrerequisiteEvidence(state=state)
        if b is None:
            return ev
        pre = b.prerequisite
        ev.rules_term = b.term_code
        ev.raw_text = pre.raw_text
        ev.canonical_text = pre.canonical_text
        ev.combination = b.combination
        ev.corequisite_text = b.corequisite.raw_text if b.corequisite.has_corequisite else None
        unknown = state is EligibilityState.NEEDS_CONFIRMATION
        ev.unknown_reasons = sorted(set(pre.reasons)
                                    | set(b.corequisite.reasons if unknown else ()))
        if state is EligibilityState.CONDITIONAL_ON_PLAN or self._awaits_placement(b):
            minimum = (pre.interpreted_conditions or {}).get("minimum_grade") or {}
            named = set(minimum.get("courses", ()))
            used = set(pre.evidence.satisfied_courses) if pre.evidence else set()
            if b.corequisite.evidence:
                used |= set(b.corequisite.evidence.satisfied_courses)
            for key in sorted(used):
                # A planned course first: a RETAKE of a passed course is
                # planned precisely because the recorded grade is too low.
                if key in planned and planned[key] < term:
                    src, t = "planned", planned[key]
                elif key in self.in_progress:
                    src, t = "in_progress", self.in_progress[key]
                else:
                    continue
                grade = None
                if minimum and (minimum.get("scope") != "named" or key in named):
                    grade = minimum.get("grade")
                ev.conditional_on.append(ConditionalDependency(course=key, term_code=t, source=src,
                                                               required_grade=grade))
        return ev

    # ---- the plan --------------------------------------------------------

    def plan(self, student: Student, version: ProgramVersion, target: ScenarioTarget,
             start_term: str, assumptions: list[ScenarioAssumption]) -> PlanResult:
        """Retry a stranded choice from the original record, without its courses.

        Branch-local estimates do not include competition for whole-plan load.
        If the constructed plan strands a branch (for example, its required
        same-term partner was taken earlier), try an untried feasible branch.
        Each retry closes a child, so the process is finite.
        """
        self._blocked_alternatives = set()
        self._alternative_cache = {}
        self._lookahead_deps = {}
        fallbacks = defaultdict(list)
        while True:
            result = self._plan_once(student, version, target, start_term, assumptions)
            nodes = {n.requirement_code: n for n in _walk(self._final_requirements)}
            inactive = self._inactive_requirements(self.alternative_choices)
            retry = False
            for choice in result.metadata.alternative_choices:
                node = nodes[choice.requirement_code]
                if node.status in MET or node.requirement_code in inactive:
                    continue
                if choice.status == "student_selected":
                    # The student's choice is held even when this plan strands
                    # it - they choose again; CoursePilot does not switch it.
                    choice.status = "needs_reselection"
                    choice.unavailable = [a.code for a in choice.chosen
                                          if nodes[a.code].status not in MET]
                    result.issues.append(_needs_reselection(choice))
                    continue
                if choice.status != "planner_recommended":
                    continue
                original = self._initial_choices[choice.requirement_code]
                times = {a["code"]: a["estimated_terms"]
                         for a in original["chosen"] + original["alternatives"]}
                available = [a for a in choice.alternatives if times.get(a.code) is not None
                             and (choice.requirement_code, a.code)
                             not in self._blocked_alternatives]
                failed = [a.code for a in choice.chosen if nodes[a.code].status not in MET
                          and (choice.requirement_code, a.code) not in self._blocked_alternatives]
                if available and failed:
                    for code in failed:
                        self._blocked_alternatives.add((choice.requirement_code, code))
                        fallbacks[choice.requirement_code].append(code)
                    retry = True
                    break
            if retry:
                continue
            for choice in result.metadata.alternative_choices:
                if fallbacks[choice.requirement_code]:
                    choice.fallback_from = fallbacks[choice.requirement_code]
                    choice.recommendation_reason = (
                        f"{choice.recommendation_reason or 'Recommended.'} The first "
                        "recommendation could not finish under this plan's load and "
                        "eligibility constraints, so another alternative was recommended.")
                    result.issues.append(PlanningIssue(
                        severity=Severity.WARNING, code="ALTERNATIVE_REPLANNED",
                        requirement_code=choice.requirement_code,
                        message=("A sequence could not finish under this plan's load and "
                                 "eligibility constraints. The plan was rebuilt with "
                                 "another alternative."),
                        details={"from": choice.fallback_from,
                                 "to": [a.code for a in choice.chosen]}))
            return result

    def _plan_once(self, student: Student, version: ProgramVersion, target: ScenarioTarget,
                   start_term: str, assumptions: list[ScenarioAssumption]) -> PlanResult:
        c = self.constraints
        self._load_static(version)
        self._history(student)
        from app.services.scheduling.candidates import student_attributes
        self.attributes = student_attributes(self.session, student)
        self.undeclared = student.declaration_status == "undeclared"
        issues: list[PlanningIssue] = []
        self._pin_issues: list[PlanningIssue] = []
        #: Phase 7.6: course -> (term, RestrictionCheck) of its first exclusion.
        self._restricted: dict[str, tuple[str, RestrictionCheck]] = {}
        self._trace_issues: list[PlanningIssue] = []
        planned: dict[str, str] = {}                 # course -> term
        info: dict[str, dict] = {}                   # course -> planning facts
        dependency_targets: dict[str, set[str]] = defaultdict(set)
        needs_confirmation: dict[str, dict[str, list[str]]] = defaultdict(dict)
        unplaceable: dict[str, dict[str, str]] = defaultdict(dict)
        cycles: dict[str, list[str]] = {}

        before = self.audit_engine.audit(student, program_version=version)
        nodes = {n.requirement_code: n for n in _walk(before.requirements)}
        for code, selection in c.alternative_selections.items():
            node = nodes.get(code)
            if (node is None or node.requirement_type != "any_of" or
                    len(selection) != len(set(selection)) or not selection or
                    len(selection) != (node.needed_count or 1) or
                    not set(selection) <= {ch.requirement_code for ch in node.children}):
                raise InvalidAlternativeSelection(f"Invalid alternative selection for {code}.")
        current = before
        self.alternative_choices = {}
        self._choice_metadata = {}
        self._branch_benefit = {}
        self._record_progress = {n.requirement_code: _progress_of(n)
                                 for n in _walk(before.requirements)}
        self._open_at_start = {leaf.requirement_code for leaf in self._active(before.requirements)}
        graduation = self._graduation_target(start_term)
        if graduation is None:
            terms = planning_terms(start_term, c.max_terms, include_summer=c.include_summer,
                                   include_winter=c.include_winter)
        else:
            # Phase 7.3: the horizon ends at the graduation target - counted
            # from the student's entry term, or their stated expected term.
            terms = [t for t in planning_terms(start_term, 16, include_summer=c.include_summer,
                                               include_winter=c.include_winter)
                     if t <= graduation.term_code]
            if not terms:
                issues.append(PlanningIssue(
                    severity=Severity.WARNING, code="GRADUATION_TARGET_PASSED",
                    message=(f"Your graduation target ({label(graduation.term_code)}) is before "
                             f"{label(start_term)}, the first semester planned. Plan from an "
                             "earlier semester or choose a later target."),
                    details={"graduation_term": graduation.term_code}))
        self._choose_alternatives(current, student, version, terms, planned)
        self._initial_choices = dict(self._choice_metadata)
        self._open_at_start = {leaf.requirement_code for leaf in self._active(before.requirements)}
        for choice in self._choice_metadata.values():
            if choice["status"] == "needs_reselection":
                issues.append(_needs_reselection(AlternativeChoice.model_validate(choice)))
        plan_terms: list[PlanTerm] = []
        empty_streak = 0
        stalled = False
        for term in terms:
            if not self._active(current.requirements):
                break
            term_plan = PlanTerm(term_code=term, label=label(term))
            current = self._fill_term(student, version, term, term_plan, current, planned, info,
                                      dependency_targets, needs_confirmation, unplaceable, cycles)
            if term_plan.courses:
                empty_streak = 0
                plan_terms.append(term_plan)
            else:
                # One empty term per season in a row: nothing will change.
                empty_streak += 1
                if empty_streak >= len({t[4] for t in terms}):
                    stalled = True
                    break

        final = self._prune(student, version, plan_terms, planned, info, current)
        if c.pace is not None and plan_terms and c.workload_level is None:
            # With a workload level each term is already shaped by its budget;
            # levelling would move anchors and break the term's explanation.
            final = self._level(student, version, terms, plan_terms, planned, info, final)
        self._finalize_courses(plan_terms, final, info, planned)
        if c.workload_level is not None:
            # Pruning may have removed courses after a term was summarised.
            for term_plan in plan_terms:
                term_plan.workload = self._term_workload(
                    term_plan.term_code, term_plan,
                    {pc.course: pc.role for pc in term_plan.courses})
        remaining = self._active(final.requirements)
        issues += self._diagnose(remaining, needs_confirmation, unplaceable, cycles,
                                 horizon=bool(remaining) and not stalled)
        issues += self._rule_issues(final)
        issues += self._pin_issues
        issues += self._restriction_issues()
        issues += self._trace_issues
        if graduation is not None and terms and remaining and not stalled:
            # Never silently push graduation later: say so and suggest fixes.
            level = (f" at a {c.workload_level.value.replace('_', '-')} workload"
                     if c.workload_level is not None else "")
            issues.append(PlanningIssue(
                severity=Severity.WARNING, code="GRADUATION_TARGET_NOT_MET",
                message=(f"The remaining requirements do not fit by {label(graduation.term_code)}"
                         f"{level} ({graduation.basis}). Your plan has not been changed: you "
                         "could add a summer term, take more in some semesters, or choose a "
                         "later graduation target."),
                details={"graduation_term": graduation.term_code, "terms": len(terms),
                         "workload_level": c.workload_level.value if c.workload_level else None,
                         "remaining": sorted(n.requirement_code for n in remaining)}))
        if self.in_progress:
            issues.append(PlanningIssue(
                severity=Severity.WARNING, code="IN_PROGRESS_OUTCOME_ASSUMED_PENDING",
                message=("Courses in progress count only provisionally; planned courses that "
                         "depend on them are conditional on passing them."),
                details={"courses": sorted(self.in_progress)}))
        if target.support_status == "pending_review":
            issues.append(PlanningIssue(
                severity=Severity.WARNING, code="PROGRAM_PENDING_REVIEW",
                message=("This program's requirements have not been verified by a person; "
                         "the plan inherits that uncertainty.")))

        # The plan inherits every unresolved school/attempt authority boundary.
        for finding in final.findings:
            if finding.code in {"requirement_eligibility_unknown",
                                "school_requirements_pending_review",
                                "program_legacy_curated"}:
                issues.append(PlanningIssue(severity=Severity.WARNING,
                    code=finding.code.upper(), message=finding.message,
                    requirement_code=finding.requirement_code))

        before_status = {n.requirement_code: n.status.value for n in _walk(before.requirements)
                         if n.requirement_type in LEAF_TYPES}
        projections = [RequirementProjection(
                           requirement_code=n.requirement_code,
                           requirement_name=n.requirement_name,
                           status_before=before_status.get(n.requirement_code, ""),
                           status_after=n.status.value)
                       for n in sorted(_walk(final.requirements), key=lambda n: n.requirement_code)
                       if n.requirement_type in LEAF_TYPES]
        if not self._active(before.requirements):
            status = PlanStatus.NOTHING_TO_PLAN
        elif not remaining:
            status = PlanStatus.COVERS_ALL_REQUIREMENTS
        else:
            status = PlanStatus.PARTIAL
        self._final_requirements = final.requirements
        return PlanResult(
            target=target, status=status, terms=plan_terms,
            placement=self._placement_basis(student),
            graduation_target=graduation,
            issues=sorted(issues, key=lambda i: (i.severity.value, i.code,
                                                 i.requirement_code or "", i.course or "")),
            requirements=projections, assumptions=assumptions,
            observations=self._observations(
                student, version, terms, plan_terms, planned, info, final,
                complete=status is PlanStatus.COVERS_ALL_REQUIREMENTS) if c.pace is not None else [],
            metadata=PlanMetadata(
                audit_engine_version=AUDIT_ENGINE_VERSION, start_term=start_term, constraints=c,
                academic_fingerprint=academic_fingerprint(self.session, student),
                rules_fingerprint=rules_fingerprint(self.session, version.id),
                offering_dataset=self.offerings.dataset_identity(self.session),
                alternative_choices=list(self._choice_metadata.values())))

    # ---- one term --------------------------------------------------------

    def _fill_term(self, student, version, term, term_plan, current, planned, info,
                   dependency_targets, needs_confirmation, unplaceable,
                   cycles) -> DegreeAuditResult:
        """Fill one term; returns the Degree Engine's result with the plan so far."""
        c = self.constraints
        before_planned = dict(planned)
        leaves = self._active(current.requirements)
        competing = self._competing_courses(current.requirements)
        leaf_by_code = {leaf.requirement_code: leaf for leaf in leaves}
        serves: dict[str, list[str]] = defaultdict(list)
        for leaf in leaves:
            for key in sorted(self.pools.get(leaf.requirement_code, ())):
                if key in self.passed or key in planned or key in self.in_progress \
                        or key in self.excluded or key in competing:
                    continue
                serves[key].append(leaf.requirement_code)
        dependencies = sorted(k for k in dependency_targets
                              if k not in planned and (k not in self.passed or k in self.retakes))
        # Phase 7.3 pins: a pinned course goes in ITS term or nowhere.
        pins_here = sorted({p.course for p in c.pins if p.term_code == term} - set(planned))
        elsewhere = {p.course for p in c.pins if p.term_code != term}
        for k in elsewhere:
            serves.pop(k, None)
        dependencies = [k for k in dependencies if k not in elsewhere]
        self._load_courses(set(pins_here))
        self._load_closure(set(serves) | set(dependencies) | set(pins_here))
        keys = sorted(set(serves) | set(dependencies)
                      | {k for k in pins_here if k in self.course_info})
        evaluated = self._evaluate(student, keys, term, planned)

        # Phase 7.6 gate: the term's SOC coreCodes. A Core goal the listing
        # read for this term does not certify is no reason to plan the course.
        self._prefetch_core(term, {k: evaluated[k][2] for k in serves
                                   if k in evaluated and evaluated[k][2].kind in PLACEABLE
                                   and any(self._is_core(c) for c in serves[k])})
        self._prefetch_restrictions(term, [k for k in keys if evaluated[k][2].kind in PLACEABLE])
        for key in sorted(serves):
            for code in list(serves[key]):
                if not self._is_core(code) or key not in evaluated \
                        or evaluated[key][2].kind not in PLACEABLE:
                    continue
                verdict = self._core_supported(key, code, term, evaluated[key][2])
                if verdict is False:
                    serves[key].remove(code)
                    ev = self._core_evidence(key, term, evaluated[key][2])
                    unplaceable[code][key] = f"core_code_not_listed:{ev.term_code}"
            if not serves[key]:
                del serves[key]
        # Phase 7.6 gate: section restrictions. Every section of the term
        # closed to the student -> the course is excluded, with the reason.
        restricted = set()
        for key in keys:
            if key in pins_here or evaluated[key][2].kind not in PLACEABLE:
                continue                 # a pin has its own restriction check
            check = self._restriction(term, key)
            if check.outcome == "not_satisfied":
                restricted.add(key)
                # Reported as an issue for a major-side course or a needed
                # prerequisite; a restricted Core alternative among hundreds
                # is only excluded (its reason stays in the diagnosis).
                if key in dependency_targets or any(
                        not self._is_core(c) for c in serves.get(key, ())):
                    self._restricted.setdefault(key, (term, check))
                for code in serves.get(key, ()):
                    unplaceable[code][key] = "restricted_sections"
                for target in sorted(dependency_targets.get(key, ())):
                    for code in serves.get(target, ()):
                        unplaceable[code][target] = f"dependency_restricted_sections:{key}"

        planner = DependencyPlanner(
            expression_of=self._expression_of,
            available=self.passed | set(self.in_progress) | set(planned),
            contributes=lambda k: k in serves,
            plannable=self._plannable)
        states = {k: self._settle(k, self._state(a, b), b, planner)
                  for k, (a, b, _) in evaluated.items()}
        ready = {k for k, s in states.items()
                 if self._placeable_state(s, evaluated[k][1])
                 and evaluated[k][2].kind in PLACEABLE
                 and self.course_info[k].credits is not None
                 and k not in restricted}
        pins_ok = []
        for key in pins_here:
            why = self._pin_check(student, term, key, evaluated, states, planned, serves)
            if why is None:
                pins_ok.append(key)
                ready.add(key)
            else:
                self._pin_issues.append(PlanningIssue(
                    severity=Severity.BLOCKER, code="PIN_NOT_POSSIBLE", course=key,
                    message=f"{key} cannot be placed in {label(term)} as pinned: {why}.",
                    details={"term": term, "reason": why}))

        # Record why candidates cannot be placed (per requirement), for diagnosis.
        for key in keys:
            for code in serves.get(key, ()):
                ev = evaluated[key][2]
                if key in ready:
                    continue
                if states[key] is EligibilityState.NEEDS_CONFIRMATION:
                    b = evaluated[key][1]
                    needs_confirmation[code][key] = sorted(
                        set(b.prerequisite.reasons) | set(b.corequisite.reasons)) if b else []
                elif ev.kind not in PLACEABLE:
                    unplaceable[code].setdefault(key, ev.kind.value)
                elif self.course_info[key].credits is None:
                    unplaceable[code][key] = "variable_credit"

        # Dependency expansion: requirements whose ready candidates cannot
        # cover their need get prerequisite paths for the cheapest targets.
        for code in sorted(leaf_by_code):
            leaf = leaf_by_code[code]
            pool = [k for k in sorted(serves) if code in serves[k]]
            need = deficit(leaf)
            have = sum((self.course_info[k].credits or Decimal(0)) if leaf.requirement_type ==
                       "credits" else Decimal(1) for k in pool if k in ready)
            if have >= need:
                continue
            options = []
            for key in pool:
                if key in ready or states.get(key) is EligibilityState.NEEDS_CONFIRMATION:
                    continue
                if not self.offerings.terms.get(key):
                    continue
                b = evaluated[key][1]
                retake = frozenset(
                    g["course"] for g in (b.prerequisite.evidence.grade_checks
                                          if b and b.prerequisite.evidence else [])
                    if g.get("result") == "unsatisfied" and g.get("earned_grade"))
                path = planner.path_for(key, retake)
                if not path.feasible:
                    if path.cycle:
                        cycles[key] = list(path.cycle)
                    unplaceable[code][key] = f"no_prerequisite_path:{path.reason}"
                    continue
                self.retakes |= retake & set(path.courses)
                options.append((len(path.courses), key, path))
            for _, key, path in sorted(options, key=lambda o: (o[0], o[1])):
                if have >= need:
                    break
                for dep in path.courses:
                    dependency_targets[dep].add(key)
                have += Decimal(1)

        # Re-evaluate newly added dependency courses once.
        new = sorted(k for k in dependency_targets if k not in evaluated
                     and k not in planned and (k not in self.passed or k in self.retakes))
        if new:
            self._load_courses(set(new))
            evaluated.update(self._evaluate(student, new, term, planned))
        for k in sorted(k for k in dependency_targets if k in evaluated and k not in planned):
            if k in new:
                states[k] = self._settle(k, self._state(*evaluated[k][:2]), evaluated[k][1],
                                         planner)
            check = self._restriction(term, k)
            if check.outcome == "not_satisfied":
                self._restricted.setdefault(k, (term, check))
            elif (self._placeable_state(states[k], evaluated[k][1])
                    and evaluated[k][2].kind in PLACEABLE
                    and self.course_info[k].credits is not None):
                ready.add(k)
                continue
            # A prerequisite the plan needs cannot be placed this term: say
            # so under every requirement whose candidate it would unlock.
            why = ("dependency_restricted_sections" if k in self._restricted
                   and self._restricted[k][0] == term
                   else f"dependency_{states[k].value}" if evaluated[k][2].kind in PLACEABLE
                   else f"dependency_{evaluated[k][2].kind.value}")
            for target in sorted(dependency_targets[k]):
                for code in serves.get(target, ()):
                    unplaceable[code][target] = f"{why}:{k}"

        # Co-requisite candidates: unmet ALONE only because a same-term
        # partner is missing. They are tried with a partner below.
        coreq_pending = {
            k for k, s in states.items()
            if s in (EligibilityState.UNSATISFIED, EligibilityState.NEEDS_CONFIRMATION)
            and evaluated[k][1] is not None
            and evaluated[k][1].corequisite.has_corequisite
            and _partner_may_help(evaluated[k][1].corequisite)
            and (evaluated[k][1].prerequisite.status is not PrereqStatus.UNSATISFIED
                 or " OR " in evaluated[k][1].combination)
            and evaluated[k][2].kind in PLACEABLE and self.course_info[k].credits is not None
            and k not in restricted}

        # Ranking (see the module docstring).
        def scarcity(key):
            codes = serves.get(key, ())
            if not codes:
                return Decimal(-1)
            return min(sum(1 for k in ready if code in serves.get(k, ()))
                       - deficit(leaf_by_code[code]) for code in codes)

        def live_targets(key):
            """Phase 7.6: dependency targets that still serve an open
            requirement this term - a path opened for an alternative the plan
            has since moved away from no longer counts."""
            return [t for t in dependency_targets.get(key, ()) if serves.get(t)]

        def ranking_evidence(key):
            # The requirement SYSTEM ("major", "core"), not the allocation
            # scope: SAS Core scopes are "core:<area>" (Phase 7.6 fix - before
            # it, serves_school was never true for a scoped Core node).
            systems = {self.requirements[code].requirement_system
                       if code in self.requirements else "major"
                       for code in serves.get(key, ())}
            return CourseRankingEvidence(
                required_dependency=(key in dependency_targets and key not in planned
                                     and bool(live_targets(key))),
                downstream_targets=len(live_targets(key)),
                scarcity=scarcity(key),
                confirmed_offering=(evaluated[key][2].kind is
                                    OfferingEvidenceKind.CONFIRMED_IN_TERM),
                prerequisites_met_by_history=(states[key] is EligibilityState.SATISFIED_BY_HISTORY),
                candidate_requirement_coverage=self._usable(serves.get(key, ())),
                serves_major="major" in systems,
                serves_school=bool(systems & {"core", "school"}),
                # Computed only when the student's strategy uses it (performance).
                unlock_breadth=(self._unlock_breadth().get(key, 0)
                                if c.strategy is Strategy.UNLOCK_FUTURE_OPTIONS else 0),
                unlock_chain_depth=self._chain_depths().get(key, 0),
                # only a course the student MUST take: a one-season Core option
                # among hundreds is no reason to come first
                season_only=(key in self._required_courses()
                             and self._pattern(key, term).only_season is not None))

        def rank(key):
            return rank_key(key, ranking_evidence(key), c.strategy)

        workload_mode = c.workload_level is not None
        stop_at, hard_cap = self._caps()
        budget = LEVELS[c.workload_level][2] if workload_mode else None
        roles: dict[str, tuple[str, str]] = {}
        if workload_mode:
            ranked = self._workload_order(term, ready | coreq_pending, pins_ok, serves,
                                          dependency_targets, rank, budget)
            order = [k for k, _, _ in ranked]
            roles = {k: (role, why) for k, role, why in ranked}
        else:
            # Fixed-content courses before generic topics courses (Phase 7.3).
            order = sorted(ready | coreq_pending, key=lambda k: (self._is_topics(k), rank(k)))
        credits = Decimal(0)
        count = 0
        topics_here = 0
        weight_total = Decimal(0)
        latest = current
        score = progress(current, self.requirements)
        open_codes = {leaf.requirement_code for leaf in self._active(current.requirements)}
        def place(key, companion_of: str | None = None) -> str:
            """Try one candidate (and its co-requisite partner): "placed", "skip"
            (this one does not fit) or "stop" (the term is full). A companion -
            a lab joining its lecture placed this term - takes that course's
            role for the floor and budget checks."""
            nonlocal credits, count, topics_here, weight_total, latest, score, open_codes
            role = roles.get(companion_of or key, (None, None))[0]
            if count >= c.max_courses_per_term or credits >= hard_cap:
                return "stop"
            if not workload_mode and credits >= stop_at:
                return "stop"
            if workload_mode and role == "filler" and credits >= stop_at:
                return "skip"            # fillers only fill up to the level's credit floor
            if key in planned or self._twin_taken(key, planned):
                return "skip"            # never the same class twice, under any number
            is_dep = key in dependency_targets
            pinned = key in pins_ok
            # A requirement course whose requirements the engine already
            # reports met (by earlier picks) cannot add progress: no audit.
            if not is_dep and not pinned and not set(serves.get(key, ())) & open_codes:
                return "skip"
            if is_dep and not pinned and not set(serves.get(key, ())) & open_codes \
                    and not any(set(serves.get(t, ())) & open_codes
                                for t in dependency_targets.get(key, ())):
                # Phase 7.3: a prerequisite path opened for an alternative the
                # plan has since moved away from (Physics 193 -> 194 once the
                # 203/204 sequence is under way) leads nowhere open: skip it.
                return "skip"
            group = [key]
            coreq_partner = None
            if key in coreq_pending:
                partner = self._partner(student, key, evaluated[key][1], term, planned)
                if partner is None:
                    return "skip"
                coreq_partner = partner
                if partner not in planned:      # else it is already in THIS term
                    group.append(partner)
                proposed = self._evaluate(student, [key], term, planned, frozenset({partner}))
                evaluated[key] = proposed[key]
                states[key] = self._state(*evaluated[key][:2])
            group_credits = sum((self.course_info[g].credits for g in group), Decimal(0))
            if count + len(group) > c.max_courses_per_term or \
                    credits + group_credits > hard_cap:
                return "skip"
            group_topics = sum(1 for g in group if self._is_topics(g))
            if group_topics and topics_here + group_topics > c.max_topics_courses_per_term:
                return "skip"
            if any(self._twin_taken(g, planned) or (set(group) - {g}) & self.twins.get(g, set())
                   for g in group):
                return "skip"
            group_weight = (sum((self.workload_index.weight(self.course_info[g], term).weight
                                 for g in group), Decimal(0)) if workload_mode else Decimal(0))
            if workload_mode and role != "anchor" and weight_total + group_weight > budget:
                return "skip"            # only must-take courses may go over the budget
            if not workload_mode and c.pace is Pace.BALANCED \
                    and c.target_credits_per_term is not None and count:
                # "Aim for the target": go past it only when that lands CLOSER
                # to it (14 + 4 = 18 against 15 is refused; 12 + 4 = 16 is not).
                aim, after_add = c.target_credits_per_term, credits + group_credits
                if after_add > aim and after_add - aim >= aim - credits:
                    return "skip"
            trial = {**planned, **{g: term for g in group}}
            if serves.get(key) or not is_dep:
                result = self._audit(student, version, trial)
                after = progress(result, self.requirements)
                if after <= score and not is_dep and not pinned:
                    return "skip"
                score, latest = max(after, score), result
                open_codes = {leaf.requirement_code for leaf in self._active(result.requirements)}
            ranked_evidence = {g: ranking_evidence(g) for g in group if g in states}
            planned.update({g: term for g in group})
            credits += group_credits
            count += len(group)
            topics_here += group_topics
            weight_total += group_weight
            members = sorted(set(group) | ({coreq_partner} if coreq_partner else set()))
            if coreq_partner and coreq_partner not in group:
                # The partner was already placed this term on its own merits.
                info[coreq_partner]["group"] = members
                info[coreq_partner]["corequisite_for"] = [key]
                term_plan.courses = [self._planned_course(coreq_partner, info[coreq_partner])
                                     if pc.course == coreq_partner else pc
                                     for pc in term_plan.courses]
            for g in group:
                if g not in evaluated:
                    evaluated.update(self._evaluate(student, [g], term, planned,
                                                    frozenset(set(group) - {g})))
                ga, gb, gev = evaluated[g]
                info[g] = {
                    "term": term, "evidence": gev,
                    "prereq": self._prereq_evidence(ga, gb, self._state(ga, gb), planned, term),
                    "group": members if len(members) > 1 else [],
                    "dependency_for": sorted(dependency_targets.get(g, ())),
                    "corequisite_for": [key] if g != key else [],
                    "alternatives": self._alternatives(g, serves, ready, rank, planned),
                    "ranking_evidence": ranked_evidence.get(g),
                }
                if workload_mode:
                    g_role, g_why = roles.get(g, roles.get(key, (None, None)))
                    if g != key:
                        g_why = f"taken together with {key} ({g_why})"
                    elif coreq_partner and coreq_partner not in group \
                            and info.get(coreq_partner, {}).get("role"):
                        # A lab/co-requisite joining a course placed this term
                        # shares that course's reason ("Gen Physics with lab").
                        g_role = info[coreq_partner]["role"]
                        g_why = (f"taken together with {coreq_partner} "
                                 f"({info[coreq_partner]['role_reason']})")
                    info[g] |= {"workload": self._workload(g, term), "role": g_role,
                                "role_reason": g_why}
                term_plan.courses.append(self._planned_course(g, info[g]))
                term_plan.credits += self.course_info[g].credits
            return "placed"

        if workload_mode:
            # Anchors, multi-requirement and the major course in order; then
            # fillers one at a time against the term AS IT NOW STANDS: the
            # preferred delivery mode first, then one that reaches the credit
            # floor within the budget, then the lowest weight (Phase 7.3).
            pool = [k for k in order if roles[k][0] == "filler"]
            for key in [k for k in order if roles[k][0] != "filler"]:
                outcome = place(key)
                if outcome == "stop":
                    break
                if outcome == "placed":
                    for k in [k for k in pool if k in coreq_pending]:
                        if self._partner(student, k, evaluated[k][1], term, planned) == key:
                            pool.remove(k)
                            place(k, companion_of=key)
            while pool and credits < stop_at:
                def choice(k):
                    w = self.workload_index.weight(self.course_info[k], term)
                    reaches = (credits + self.course_info[k].credits >= stop_at
                               and weight_total + w.weight <= budget)
                    return (filler_rank(w.delivery, c.filler_preference.value), not reaches,
                            w.weight, self._is_topics(k), rank(k))
                pick = min(pool, key=choice)
                pool.remove(pick)
                if place(pick) == "stop":
                    break
        else:
            for key in order:
                if place(key) == "stop":
                    break
        for key in pins_ok:
            if key not in planned:
                self._pin_issues.append(PlanningIssue(
                    severity=Severity.BLOCKER, code="PIN_NOT_POSSIBLE", course=key,
                    message=(f"{key} could not be placed in {label(term)} as pinned: it does "
                             "not fit this term's course or credit limits.")))
        if workload_mode:
            term_plan.workload = self._term_workload(
                term, term_plan, {g: info[g].get("role") for g in planned if planned[g] == term})
        if latest is current and planned != before_planned:
            latest = self._audit(student, version, planned)
        return latest

    # ---- Phase 7.3: pins and workload levels ------------------------------

    def _pin_check(self, student, term: str, key: str, evaluated, states,
                   planned: dict[str, str], serves) -> str | None:
        """Why `key` CANNOT be placed in `term` as pinned (None: it can).
        Warnings that do not prevent it (won't count, may be restricted) are
        recorded as issues here."""
        if key not in self.course_info or key not in evaluated:
            return "CoursePilot has no record of this course"
        a, b, ev = evaluated[key]
        if key in self.passed and key not in self.retakes:
            return "you have already completed it"
        if self._twin_taken(key, planned):
            return "the same class (cross-listed under another number) is already in your plan or record"
        if ev.kind not in PLACEABLE:
            return {
                OfferingEvidenceKind.NOT_OFFERED_IN_TERM: f"it is not listed for {label(term)}",
                OfferingEvidenceKind.TERM_DATA_PENDING:
                    f"Rutgers has published {label(term)} but CoursePilot has not loaded it yet",
                OfferingEvidenceKind.OTHER_SEASONS_ONLY: "it has only been offered in other seasons",
                OfferingEvidenceKind.NO_EVIDENCE: "there is no record of Rutgers offering it",
            }.get(ev.kind, f"offering evidence: {ev.kind.value}")
        if states[key] is EligibilityState.NEEDS_CONFIRMATION and not self._placeable_state(
                states[key], b):
            # Decided 2026-10-23: an explicit pin may place a course whose
            # prerequisite CoursePilot cannot VERIFY - flagged, never hidden.
            reasons = sorted(set(b.prerequisite.reasons)) if b is not None else []
            self._pin_issues.append(PlanningIssue(
                severity=Severity.NEEDS_CONFIRMATION, code="PIN_PREREQUISITES_UNVERIFIED",
                course=key, details={"reasons": reasons},
                message=(f"{key} is placed in {label(term)} because you pinned it, but "
                         "CoursePilot cannot verify its prerequisites - confirm them before "
                         "registering.")))
        elif not self._placeable_state(states[key], b):
            text = b.prerequisite.canonical_text if b is not None else None
            return "its prerequisites are not met by then" + (f" ({text})" if text else "")
        restricted = self._pin_restriction(student, term, key)
        if restricted == "not_satisfied":
            return f"every {label(term)} section is restricted to other majors or schools"
        if restricted == "unknown":
            self._pin_issues.append(PlanningIssue(
                severity=Severity.NEEDS_CONFIRMATION, code="PIN_MAY_BE_RESTRICTED", course=key,
                message=(f"{key} in {label(term)}: its sections list enrollment restrictions "
                         "CoursePilot cannot check against your record.")))
        if key in self.excluded:
            self._pin_issues.append(PlanningIssue(
                severity=Severity.WARNING, code="PIN_WILL_NOT_COUNT", course=key,
                message=(f"{key} is placed as you pinned it, but your program gives no credit "
                         "for it (a published course exclusion), so it will not count.")))
        elif not serves.get(key):
            self._pin_issues.append(PlanningIssue(
                severity=Severity.WARNING, code="PIN_WILL_NOT_COUNT", course=key,
                message=(f"{key} is placed as you pinned it, but it completes no remaining "
                         "requirement.")))
        return None

    def _pin_restriction(self, student, term: str, key: str) -> str | None:
        """'not_satisfied' when EVERY section of the term excludes the student,
        'unknown' when restrictions exist that cannot be checked, else None."""
        from app.domain.schedule import RestrictionEntry, RestrictionOutcome
        from app.models import SectionRestriction
        from app.services.scheduling.candidates import evaluate_restriction, student_attributes

        by_section: dict = defaultdict(list)
        sections = set()
        for section_id, kind, code, unit in self.session.execute(
                select(CourseSection.id, SectionRestriction.kind, SectionRestriction.code,
                       SectionRestriction.unit_code)
                .join(CourseOffering, CourseOffering.id == CourseSection.offering_id)
                .join(Course, Course.id == CourseOffering.course_id)
                .outerjoin(SectionRestriction, SectionRestriction.section_id == CourseSection.id)
                .where(Course.course_string == key, CourseSection.term_code == term)):
            sections.add(section_id)
            if kind:
                by_section[section_id].append(RestrictionEntry(kind=kind, code=code,
                                                               unit_code=unit))
        if not sections:
            return None
        attrs = student_attributes(self.session, student)
        outcomes = {evaluate_restriction(by_section.get(s, []), attrs).outcome for s in sections}
        if outcomes == {RestrictionOutcome.NOT_SATISFIED}:
            return "not_satisfied"
        if not outcomes & {RestrictionOutcome.UNRESTRICTED, RestrictionOutcome.SATISFIED}:
            return "unknown"
        return None

    def _workload(self, key: str, term: str) -> WorkloadEvidence:
        w = self.workload_index.weight(self.course_info[key], term)
        return WorkloadEvidence(weight=w.weight, credits=w.credits, contact_hours=w.contact_hours,
                                has_lab=w.has_lab, level=w.level, technical=w.technical,
                                components=w.components, delivery=w.delivery,
                                basis_term=w.basis_term)

    def _workload_order(self, term, ready_or_pending, pins, serves, dependency_targets,
                        rank, budget_left) -> list[tuple[str, str, str]]:
        """(course, role, why) in the Phase 7.3 fill order: anchors (pins,
        then needed retakes), multi-requirement, ONE major course, prerequisite
        paths, then fillers by the student's filler preference - lowest weight
        first when the budget is nearly used. Every pick is still re-validated
        by the caller (audit, caps, budget)."""
        c = self.constraints
        names = {code: self.requirements[code].name for code in self.requirements}
        out: list[tuple[str, str, str]] = []
        used: set[str] = set()

        def take(keys, role, why):
            for k in keys:
                if k not in used:
                    used.add(k)
                    out.append((k, role, why(k)))

        take(pins, "anchor", lambda k: "you pinned it to this term")
        retakes = sorted((k for k in ready_or_pending if serves.get(k) and (
            k in self.retakes or (k in self.history and k not in self.passed))), key=rank)
        take(retakes, "anchor", lambda k: "a retake you need (your recorded attempt does not "
                                         "count yet)")
        # Phase 7.6: "multi-requirement" only where the sharing policy lets one
        # course count in two open requirements (one per allocation scope).
        multi = sorted((k for k in ready_or_pending if self._usable(serves.get(k, ())) >= 2),
                       key=rank)
        take(multi, "multi_requirement", lambda k: "counts toward " + " and ".join(
            sorted({names.get(code, code) for code in serves[k]})))
        major = sorted((k for k in ready_or_pending if k.split(":")[1] == self.major_subject
                        and any(self._system(code) == "major" for code in serves.get(k, ()))),
                       key=lambda k: (self._is_topics(k), rank(k)))       # fixed content first
        take(major[:1], "major", lambda k: f"a {self.major_subject} course that counts toward "
                                           "your major")
        nearly_used = budget_left <= Decimal(8)

        def filler_key(k):
            w = self.workload_index.weight(self.course_info[k], term)
            return (filler_rank(w.delivery, c.filler_preference.value),
                    w.weight if nearly_used else Decimal(0), self._is_topics(k), rank(k))

        # Fillers: everything else that completes a remaining requirement or
        # opens one (a prerequisite path), chosen by the filler preference.
        fillers = sorted((k for k in ready_or_pending
                          if serves.get(k) or k in dependency_targets), key=filler_key)

        def filler_why(k):
            w = self.workload_index.weight(self.course_info[k], term)
            mode = ("an asynchronous online section" if "async" in w.delivery
                    else "an online section" if "online" in w.delivery else "in-person sections")
            what = (f"completes {', '.join(sorted({names.get(x, x) for x in serves[k]}))}"
                    if serves.get(k) else
                    f"a prerequisite for {', '.join(sorted(dependency_targets[k]))}")
            return f"{what}; has {mode}"

        take(fillers, "filler", filler_why)
        return out

    def _term_workload(self, term: str, term_plan: PlanTerm, roles: dict[str, str]) -> TermWorkload:
        c = self.constraints
        floor, cap, budget = LEVELS[c.workload_level]
        total = sum((pc.workload.weight for pc in term_plan.courses if pc.workload), Decimal(0))
        hours = sum((pc.workload.contact_hours or Decimal(0) for pc in term_plan.courses
                     if pc.workload), Decimal(0))
        notes = []
        anchors = [pc for pc in term_plan.courses if roles.get(pc.course) == "anchor"]
        anchor_weight = sum((pc.workload.weight for pc in anchors if pc.workload), Decimal(0))
        if anchors and anchor_weight > budget:
            notes.append(f"{' + '.join(pc.title for pc in anchors)} make this a heavier term: "
                         f"the courses you must take weigh {anchor_weight} against a "
                         f"{budget} budget.")
        elif total > budget:
            notes.append(f"Over the {budget} workload budget ({total}) because of the courses "
                         "this term must hold.")
        if term_plan.courses and term_plan.credits < floor:
            notes.append(f"Below the {floor}-credit floor ({term_plan.credits}): no further "
                         "eligible course fit the workload budget and the per-term limits.")
        return TermWorkload(level=c.workload_level, budget=budget, total=total,
                            credit_floor=floor, credit_max=cap, contact_hours=hours,
                            notes=notes)

    def _partner(self, student, key, b, term, planned) -> str | None:
        """Deterministic same-term partner satisfying `key`'s co-requisite:
        the co-requisite courses named in the published rule, already-planned
        ones first, then by course string; the first that check_proposal
        accepts - proposed together - and that is itself eligible, wins."""
        leaves = []

        def walk(e):
            if isinstance(e, ConcurrentReq):
                leaves.append(e.course_key)
            for ch in getattr(e, "children", ()):
                walk(ch)

        for row in self._coreq_rows(key, b.term_code):
            if row.expression:
                walk(from_json(row.expression))
        for partner in sorted(set(leaves), key=lambda p: (p not in planned, p)):
            if partner in self.passed or partner in self.excluded:
                continue
            if partner in planned and planned[partner] != term:
                continue        # an earlier term: check_proposal already saw it
            if partner in planned:
                check = check_proposal(self.session, student, [key], b.term_code, {partner},
                                       projected=self._hypothesis(planned, term),
                                       as_of_term=term, independent=True)
                if check[key].status is PrereqStatus.SATISFIED:
                    return partner
                continue
            self._load_courses({partner})
            if self.course_info.get(partner) is None or self.course_info[partner].credits is None:
                continue
            if self._restriction(term, partner).outcome == "not_satisfied":
                continue
            if self.offerings.evidence(partner, term).kind not in PLACEABLE:
                continue
            check = check_proposal(self.session, student, [key], b.term_code, {partner},
                                   projected=self._hypothesis(planned, term),
                                   as_of_term=term, independent=True)
            if check[key].status is PrereqStatus.SATISFIED:
                pa, pb, _ = self._evaluate(student, [partner], term, planned,
                                           frozenset({key}))[partner]
                if self._state(pa, pb) in (EligibilityState.SATISFIED_BY_HISTORY,
                                           EligibilityState.CONDITIONAL_ON_PLAN):
                    return partner
        return None

    def _coreq_rows(self, key, rules_term):
        return list(self.session.scalars(
            select(CourseCorequisite).join(Course, Course.id == CourseCorequisite.course_id)
            .where(Course.course_string == key, CourseCorequisite.term_code == rules_term)
            .order_by(CourseCorequisite.id)))

    def _alternatives(self, key, serves, ready, rank, planned) -> int:
        """Equally ranked ready candidates serving the same requirements.

        A co-requisite partner added only to satisfy another course (Phase
        6.6.1 finding: 01:750:227 for 01:750:229) serves no requirement and is
        not a ranked candidate - it has no alternatives, and is not ranked."""
        codes = set(serves.get(key, ()))
        if not codes:
            return 0
        mine = rank(key)[:-1]
        return sum(1 for k in ready if k != key and k not in planned
                   and set(serves.get(k, ())) == codes and rank(k)[:-1] == mine)

    def _graduation_target(self, start_term: str) -> GraduationTarget | None:
        """The last term a personalized plan may use (Phase 7.3).

        An expected graduation term the student states wins. Otherwise the
        graduation target pace counts REGULAR semesters (Fall/Spring) from
        the student's entry term - their first Rutgers semester on record,
        else the first planned one: On time 8, Early 7, No rush 10. A
        stateless request (no pace) keeps the plain horizon."""
        c = self.constraints
        if c.expected_graduation_term:
            return GraduationTarget(term_code=c.expected_graduation_term,
                                    basis="your expected graduation term")
        if c.pace is None:
            return None
        seen = sorted(a.term_code for atts in self.history.values() for a in atts
                      if a.credit_origin == "rutgers" and a.term_code
                      and a.term_code[4:] in ("1", "9"))
        entry = seen[0] if seen else _regular(start_term)
        semesters = GRADUATION_SEMESTERS[c.pace]
        end = entry
        for _ in range(semesters - 1):
            end = _next_regular(end)
        source = (f"your first semester, {label(entry)}" if seen
                  else "the first planned semester")
        return GraduationTarget(term_code=end, entry_term=entry, semesters=semesters,
                                basis=f"{semesters} semesters counted from {source}")

    def _placement_basis(self, student: Student) -> PlacementBasis | None:
        """The stated math placement the plan relied on - shown, so a wrong
        entry is easy to spot."""
        if not student.math_placement:
            return None
        title = self.session.scalar(select(Course.title).where(
            Course.course_string == student.math_placement, Course.supplement_code == ""))
        return PlacementBasis(course=student.math_placement, title=title,
                              source_url=PLACEMENT_POLICY_URL)

    def _planned_course(self, key, facts) -> PlannedCourse:
        course = self.course_info[key]
        ev: OfferingEvidence = facts["evidence"]
        pre: PrerequisiteEvidence = facts["prereq"]
        reasons = []
        if (pre.state is EligibilityState.SATISFIED_BY_HISTORY
                and MET_BY_PLACEMENT in pre.unknown_reasons):
            reasons.append(Reason.PREREQUISITES_MET_BY_PLACEMENT)
        elif pre.state is EligibilityState.SATISFIED_BY_HISTORY:
            reasons.append(Reason.PREREQUISITES_SATISFIED_BY_HISTORY if pre.raw_text or
                           pre.corequisite_text else Reason.NO_PREREQUISITE_PUBLISHED)
        elif pre.state is EligibilityState.NEEDS_CONFIRMATION:
            reasons.append(Reason.REQUIRES_MATH_PLACEMENT if REQUIRES_PLACEMENT
                           in pre.unknown_reasons else Reason.PREREQUISITES_NEED_CONFIRMATION)
        else:
            reasons.append(Reason.PREREQUISITES_CONDITIONAL_ON_PLAN)
        if facts["dependency_for"]:
            reasons.append(Reason.REQUIRED_PREREQUISITE_FOR)
        if facts["corequisite_for"]:
            reasons.append(Reason.REQUIRED_COREQUISITE_FOR)
        reasons.append(Reason.CONFIRMED_OFFERING_IN_TERM
                       if ev.kind is OfferingEvidenceKind.CONFIRMED_IN_TERM
                       else Reason.HISTORICALLY_OFFERED_IN_SEASON)
        if ev.rules_term and ev.rules_term != ev.target_term:
            reasons.append(Reason.RULES_FROM_EARLIER_TERM)
        if facts["alternatives"]:
            reasons.append(Reason.SELECTED_BY_CANONICAL_TIE_BREAK)
        evidence = facts.get("ranking_evidence")
        if evidence and evidence.candidate_requirement_coverage > 1:
            reasons.append(Reason.RANKED_BY_REQUIREMENT_COVERAGE)
        return PlannedCourse(
            ranking_evidence=evidence, course=key, title=course.title, credits=course.credits, term_code=facts["term"],
            reasons=reasons, prerequisite=pre, offering=ev, corequisite_group=facts["group"],
            unlocks=sorted(set(facts["dependency_for"]) | set(facts["corequisite_for"])),
            interchangeable_alternatives=facts["alternatives"],
            is_topics_course=self._is_topics(key),
            # Only the term's OWN published sections name its topic; a term
            # planned from history has none yet ("topic not yet announced").
            topics=(self._topics(key, facts["term"]) if self._is_topics(key)
                    and ev.kind is OfferingEvidenceKind.CONFIRMED_IN_TERM else []),
            workload=facts.get("workload"), role=facts.get("role"),
            role_reason=facts.get("role_reason"))

    # ---- Phase 7.2.1: pace, target load, levelling -----------------------

    def _caps(self) -> tuple[Decimal, Decimal]:
        """(where a term STOPS taking courses, the HARD credit cap).

        No pace (unpersonalized request): the Phase 6.5 behaviour - fill to the
        maximum. BALANCED and LIGHTER stop at the target; only LIGHTER refuses to
        go past it. FINISH_EARLIER fills toward the maximum. The maximum is never
        exceeded by any pace."""
        c = self.constraints
        if c.workload_level is not None:
            floor, cap, _budget = LEVELS[c.workload_level]
            return floor, cap
        target = c.target_credits_per_term
        if c.pace in (Pace.BALANCED, Pace.LIGHTER) and target is not None:
            hard = target if c.pace is Pace.LIGHTER else c.max_credits_per_term
            return target, hard
        return c.max_credits_per_term, c.max_credits_per_term

    def _move_check(self, student, key: str, term: str, planned: dict[str, str], info,
                    cap: Decimal) -> tuple | None:
        """Would `key` be validly placed in `term` instead? None if not.

        Validity is the engine's own: offering evidence for THAT term must be
        placeable, the prerequisite state must still be met or conditional when
        re-evaluated FOR THAT TERM against the plan with the move applied, every
        planned prerequisite stays strictly earlier and every planned dependent
        strictly later, co-requisite groups never move, and the hard caps hold.
        UNKNOWN can never pass this check, so levelling cannot place one."""
        facts = info[key]
        credits = self.course_info[key].credits
        if facts["group"] or credits is None:
            return None
        before = {d.course for d in facts["prereq"].conditional_on
                  if d.source == "planned" and d.course in planned}
        if any(planned[p] >= term for p in before):
            return None
        for other, other_term in planned.items():
            if other != key and other_term <= term and key in {
                    d.course for d in info[other]["prereq"].conditional_on if d.source == "planned"}:
                return None
        load = sum((self.course_info[k].credits for k, t in planned.items() if t == term and k != key),
                   Decimal(0))
        count = sum(1 for k, t in planned.items() if t == term and k != key)
        if load + credits > cap or count + 1 > self.constraints.max_courses_per_term:
            return None
        if self.offerings.evidence(key, term).kind not in PLACEABLE:
            return None
        if self._restriction(term, key).outcome == "not_satisfied":
            return None
        trial = {**planned, key: term}
        # Eligibility in `term` depends only on what is planned BEFORE it
        # (the hypothesis) - so that is the cache key.
        memo_key = (key, term, frozenset((k, t) for k, t in planned.items()
                                         if t < term and k != key))
        memo = self.__dict__.setdefault("_move_memo", {})
        if memo_key not in memo:
            memo[memo_key] = self._evaluate(student, [key], term, trial)[key]
        checked = memo[memo_key]
        if self._state(checked[0], checked[1]) not in (EligibilityState.SATISFIED_BY_HISTORY,
                                                       EligibilityState.CONDITIONAL_ON_PLAN):
            return None
        return checked

    @staticmethod
    def _loads(planned: dict[str, str], span: list[str], course_info) -> dict[str, Decimal]:
        out = {t: Decimal(0) for t in span}
        for k, t in planned.items():
            out[t] = out.get(t, Decimal(0)) + course_info[k].credits
        return out

    def _compress(self, student, span, planned, info, cap, repeat: bool) -> list[str]:
        """Move every course of the LAST term earlier, or none of them; returns
        the span actually used. One term at a time; `repeat` keeps going."""
        span = list(span)
        while len(span) > 1:
            last = max(t for t in planned.values())
            moving = sorted((k for k, t in planned.items() if t == last),
                            key=lambda k: (-self.course_info[k].credits, k))
            trial = dict(planned)
            for key in moving:
                loads = self._loads(trial, span, self.course_info)
                targets = sorted((t for t in span if t < last), key=lambda t: (loads[t], t))
                placed = next((t for t in targets
                               if self._move_check(student, key, t, trial, info, cap)), None)
                if placed is None:
                    return span
                trial[key] = placed
            planned.clear()
            planned.update(trial)
            span = [t for t in span if t < last]
            if not repeat:
                break
        return span

    def _level(self, student, version, terms, plan_terms, planned, info,
               final: DegreeAuditResult) -> DegreeAuditResult:
        """Deterministic, bounded improvement of an already-valid plan.

        1. Compression (BALANCED when the last term is stranded - at most half
           the target; FINISH_EARLIER always): move the last term's courses
           into earlier terms within the hard maximum, all or nothing.
        2. Levelling: repeatedly make the single valid move that most reduces
           the spread of term loads (a move from term D to R helps only when
           load(D) - load(R) > the course's credits). Never extends the plan.
        3. Verification: every moved course and every dependent is re-evaluated
           for its new term, then ONE projected audit must show the same
           progress and the same requirement allocation as before. Otherwise
           every move is reverted and the greedy plan stands.
        """
        c = self.constraints
        _, hard = self._caps()
        original = dict(planned)
        snapshot = {k: dict(v) for k, v in info.items()}
        first, last = plan_terms[0].term_code, plan_terms[-1].term_code
        span = [t for t in terms if first <= t <= last]
        loads = self._loads(planned, span, self.course_info)
        target = c.target_credits_per_term
        if c.pace is Pace.FINISH_EARLIER or (
                c.pace is Pace.BALANCED and target is not None and loads[last] * 2 <= target):
            span = self._compress(student, span, planned, info, c.max_credits_per_term,
                                  repeat=c.pace is Pace.FINISH_EARLIER)
        for _ in range(40):
            loads = self._loads(planned, span, self.course_info)
            moves = sorted(
                (loads[planned[k]] - loads[r] - self.course_info[k].credits, planned[k], k, r)
                for k in planned for r in span
                if r != planned[k] and loads[planned[k]] - loads[r] > self.course_info[k].credits)
            moved = False
            # Best improvement first; at most 25 candidates checked per step.
            for _gain, _donor, key, receiver in list(reversed(moves))[:25]:
                if self._move_check(student, key, receiver, planned, info, hard):
                    planned[key] = receiver
                    moved = True
                    break
            if not moved:
                break
        if planned == original:
            return final

        def revert() -> DegreeAuditResult:
            planned.clear()
            planned.update(original)
            info.clear()
            info.update(snapshot)
            return final

        changed = {k for k in planned if planned[k] != original[k]}
        affected = changed | {o for o in planned if any(
            d.course in changed for d in info[o]["prereq"].conditional_on if d.source == "planned")}
        for key in sorted(affected):
            term = planned[key]
            ga, gb, gev = self._evaluate(student, [key], term, planned,
                                         frozenset(set(info[key]["group"]) - {key}))[key]
            state = self._state(ga, gb)
            if state not in (EligibilityState.SATISFIED_BY_HISTORY,
                             EligibilityState.CONDITIONAL_ON_PLAN) or gev.kind not in PLACEABLE:
                return revert()
            info[key] = {**info[key], "term": term, "evidence": gev,
                         "prereq": self._prereq_evidence(ga, gb, state, planned, term)}

        def allocation(result):
            out: dict[str, list[str]] = defaultdict(list)
            for a in result.allocation:
                out[a.course.course_string].append(a.requirement_code)
            return {k: sorted(v) for k, v in out.items()}

        result = self._audit(student, version, planned)
        if progress(result, self.requirements) != progress(final, self.requirements) or \
                allocation(result) != allocation(final):
            return revert()
        by_term: dict[str, list[str]] = defaultdict(list)
        for key, term in planned.items():
            by_term[term].append(key)
        plan_terms[:] = []
        for term in sorted(by_term):
            pt = PlanTerm(term_code=term, label=label(term))
            for key in sorted(by_term[term]):
                pt.courses.append(self._planned_course(key, info[key]))
                pt.credits += self.course_info[key].credits
            plan_terms.append(pt)
        return result

    def _observations(self, student, version, terms, plan_terms, planned, info,
                      final: DegreeAuditResult, *, complete: bool) -> list[PlanObservation]:
        c = self.constraints
        out: list[PlanObservation] = []
        target = c.target_credits_per_term
        if target is not None:
            for i, term in enumerate(plan_terms):
                if term.credits * 10 >= target * 6:
                    continue
                n = len(term.courses)
                if i == len(plan_terms) - 1:
                    reason = "final_requirements"
                    msg = (f"{term.label} is the last semester of this plan: only {n} "
                           f"course{'s' if n != 1 else ''} remain{'s' if n == 1 else ''} for the "
                           "currently modeled requirements.")
                else:
                    prev = plan_terms[i - 1].term_code if i else None
                    chained = prev is not None and all(any(
                        d.source == "planned" and d.term_code == prev
                        for d in pc.prerequisite.conditional_on) for pc in term.courses)
                    reason = "prerequisite_chain" if chained else "constrained"
                    msg = (f"{term.label} is lighter than your target because each course here "
                           f"needs a course from {label(prev)} first." if chained else
                           f"{term.label} is lighter than your target: CoursePilot couldn't move "
                           "more courses here without breaking a prerequisite order or Rutgers' "
                           "known offering seasons.")
                out.append(PlanObservation(code="light_term", term_code=term.term_code,
                                           message=msg, details={"reason": reason,
                                                                 "credits": str(term.credits)}))
        # Capability, never intent: say that fewer terms are possible, and leave
        # the objective alone unless the student chooses FINISH_EARLIER.
        if complete and c.pace in (Pace.BALANCED, Pace.LIGHTER) and len(plan_terms) > 1:
            first, last = plan_terms[0].term_code, plan_terms[-1].term_code
            span = [t for t in terms if first <= t <= last]
            trial = dict(planned)
            shorter = self._compress(student, span, trial, info, c.max_credits_per_term,
                                     repeat=True)
            if trial != planned:
                end = max(trial.values())
                result = self._audit(student, version, trial)
                if progress(result, self.requirements) == progress(final, self.requirements) \
                        and end < last:
                    out.append(PlanObservation(
                        code="earlier_completion_possible", term_code=end,
                        message=(f"Taking up to {c.max_credits_per_term} credits in some "
                                 f"semesters could complete the currently modeled requirements "
                                 f"by {label(end)} instead of {label(last)}. This is not a "
                                 "graduation date, and your plan has not been changed: choose "
                                 "\"Finish earlier\" if that is what you want."),
                        details={"planned_last_term": last, "possible_last_term": end,
                                 "terms_planned": len(plan_terms),
                                 "terms_possible": len({t for t in trial.values()}),
                                 "max_credits_per_term": str(c.max_credits_per_term)}))
        return out

    def _prune(self, student, version, plan_terms, planned, info, final) -> DegreeAuditResult:
        """Drop planned courses the final allocation credits nowhere.

        Greedy acceptance can let a later pick displace an earlier one (the
        engine re-allocates globally). A course is removed - latest term
        first, then by course string - when no requirement is credited with
        it, no other planned course depends on it or is grouped with it, and
        the Degree Engine confirms progress does not drop without it.
        """
        def credited(result):
            # Phase 7.6: credited where it is USEFUL - a course the allocation
            # puts only on an ANY_OF alternative the plan no longer needs
            # (Physics 193 once the 203/204 sequence fills the B.S. science)
            # is a removal candidate like an uncredited one.
            useful = {n.requirement_code for n in _useful_leaves(result.requirements)}
            return {a.course.course_string for a in result.allocation
                    if a.requirement_code in useful or a.requirement_code not in self.requirements
                    or self.requirements[a.requirement_code].requirement_type not in LEAF_TYPES}

        score = progress(final, self.requirements, useful_only=True)
        pinned = {(p.course, p.term_code) for p in self.constraints.pins}
        for key in sorted(planned, key=lambda k: (planned[k], k), reverse=True):
            if key in credited(final) or (key, planned[key]) in pinned:
                continue            # a pin is the student's hard constraint
            needed_by = any(key in {d.course for d in info[o]["prereq"].conditional_on}
                            or key in info[o]["group"]
                            for o in planned if o != key)
            if needed_by:
                continue
            trial = {k: t for k, t in planned.items() if k != key}
            result = self._audit(student, version, trial) if trial else \
                self.audit_engine.audit(student, program_version=version)
            if progress(result, self.requirements, useful_only=True) < score:
                continue
            del planned[key]
            info.pop(key, None)
            final = result
            for term in plan_terms:
                for pc in [pc for pc in term.courses if pc.course == key]:
                    term.courses.remove(pc)
                    term.credits -= pc.credits
        plan_terms[:] = [t for t in plan_terms if t.courses]
        return final

    def _finalize_courses(self, plan_terms, final: DegreeAuditResult, info, planned) -> None:
        """Requirement contributions come from the final projected audit:
        the Degree Engine's own allocation decides where each course counts."""
        allocated: dict[tuple[str, str], list] = defaultdict(list)
        for a in final.allocation:
            allocated[(a.course.course_string, a.term_code or "")].append(a)
        unlocked_by: dict[str, set[str]] = defaultdict(set)
        for term in plan_terms:
            for pc in term.courses:
                for dep in pc.prerequisite.conditional_on:
                    if dep.source == "planned":
                        unlocked_by[dep.course].add(pc.course)
        for term in plan_terms:
            for pc in term.courses:
                pc.requirements = [RequirementContribution(
                    requirement_code=a.requirement_code, requirement_name=a.requirement_name,
                    requirement_system=a.requirement_system)
                    for a in sorted(allocated[(pc.course, pc.term_code)],
                                    key=lambda a: a.requirement_code)]
                if pc.requirements:
                    pc.reasons.insert(0, Reason.SATISFIES_REQUIREMENT)
                if unlocked_by[pc.course]:
                    pc.unlocks = sorted(set(pc.unlocks) | unlocked_by[pc.course])
                    if Reason.UNLOCKS_DOWNSTREAM_COURSE not in pc.reasons:
                        pc.reasons.append(Reason.UNLOCKS_DOWNSTREAM_COURSE)
                self._trace(pc)
            term.courses.sort(key=lambda pc: pc.course)

    # ---- Phase 7.6: requirement-aware recommendations ----------------------

    def _usable(self, codes) -> int:
        """How many of these open requirements ONE course can count toward at
        once: one per allocation scope when the program shares across systems,
        else one."""
        codes = set(codes)
        if not codes:
            return 0
        return len({self._system(c) for c in codes}) if self.share else 1

    def _prefetch_core(self, term: str, evidence: dict) -> None:
        """One coreCodes read per listing term for many candidates."""
        from app.services.sources.sections import core_codes

        wanted: dict[str, set[str]] = defaultdict(set)
        for key, ev in evidence.items():
            basis = (term if ev.kind is OfferingEvidenceKind.CONFIRMED_IN_TERM
                     else ev.most_recent_term)
            if basis is not None and (key, basis) not in self._core_cache:
                wanted[basis].add(key)
        for basis, keys in sorted(wanted.items()):
            found = core_codes(self.session, basis, keys)
            for key in keys:
                self._core_cache[(key, basis)] = found.get(key)

    def _prefetch_restrictions(self, term: str, keys) -> None:
        """Restriction verdicts for many courses of one term, in one query."""
        from app.models import SectionRestriction

        todo = sorted({k for k in keys if (term, k) not in self._restriction_cache})
        if not todo:
            return
        rows: dict[str, dict] = defaultdict(lambda: defaultdict(list))
        for key, section_id, row in self.session.execute(
                select(Course.course_string, CourseSection.id, SectionRestriction)
                .join(CourseOffering, CourseOffering.course_id == Course.id)
                .join(CourseSection, CourseSection.offering_id == CourseOffering.id)
                .outerjoin(SectionRestriction, SectionRestriction.section_id == CourseSection.id)
                .where(Course.course_string.in_(todo), CourseSection.term_code == term,
                       Course.supplement_code == "")):
            bucket = rows[key][section_id]
            if row is not None:
                bucket.append(row)
        for key in todo:
            self._restriction_cache[(term, key)] = self._judge(term, rows.get(key, {}))

    def _restriction(self, term: str, key: str) -> RestrictionCheck:
        """The term's sections against the student's record. Cached per plan."""
        if (term, key) not in self._restriction_cache:
            self._prefetch_restrictions(term, [key])
        return self._restriction_cache[(term, key)]

    def _judge(self, term: str, sections: dict) -> RestrictionCheck:
        """The verdict from {section id: [SectionRestriction rows]}."""
        from app.domain.schedule import RestrictionEntry, RestrictionOutcome
        from app.services.scheduling.candidates import evaluate_restriction
        from app.services.sources.sections import restriction_names

        if not sections:
            return RestrictionCheck(
                outcome="sections_not_published",
                message=(f"{label(term)} sections are not published yet, so their "
                         "enrollment restrictions are unknown."))
        rows_by_section = {sid: rows for sid, rows in sections.items() if rows}
        verdicts = {sid: evaluate_restriction(
                        [RestrictionEntry(kind=r.kind, code=r.code, unit_code=r.unit_code)
                         for r in rows], self.attributes)
                    for sid, rows in sections.items()}
        outcomes = {v.outcome for v in verdicts.values()}
        open_ = sum(1 for v in verdicts.values() if v.outcome in (
            RestrictionOutcome.UNRESTRICTED, RestrictionOutcome.SATISFIED))
        names: list[str] = []
        if open_ < len(sections):
            for sid in sorted(rows_by_section, key=str):
                for n in restriction_names(self.session, rows_by_section[sid]):
                    text = (f"{n.kind.replace('_', '/')} "
                            f"{n.unit_code + '/' if n.unit_code else ''}{n.code}"
                            + (f" ({n.name})" if n.name else ""))
                    if text not in names:
                        names.append(text)
        reasons = sorted({r for v in verdicts.values() for r in v.reasons})
        if outcomes == {RestrictionOutcome.UNRESTRICTED}:
            outcome, message = "unrestricted", None
        elif open_:
            outcome = "satisfied"
            message = (None if open_ == len(sections) else
                       f"{open_} of {len(sections)} {label(term)} sections are open to you; "
                       "the others are restricted.")
        elif outcomes == {RestrictionOutcome.NOT_SATISFIED}:
            outcome = "not_satisfied"
            mine = self.undeclared and any(
                r.kind in ("major", "unit_major") and r.code == self.major_subject
                for rows in rows_by_section.values() for r in rows)
            message = (f"Every {label(term)} section is open only to: {', '.join(names)}"
                       + (" - you are planning toward this major but have not declared it yet."
                          if mine else "."))
        else:
            outcome = "unknown"
            mine = self.undeclared and any(
                r.kind in ("major", "unit_major") and r.code == self.major_subject
                for rows in rows_by_section.values() for r in rows)
            message = (f"Every {label(term)} section lists restrictions CoursePilot cannot "
                       f"check against your record (open to: {', '.join(names) or 'see SOC'})"
                       + ("; you have not declared this major yet, so confirm you can enroll."
                          if mine else "; confirm before registering."))
        return RestrictionCheck(outcome=outcome, sections=len(sections), open_sections=open_,
                                open_to=names, reasons=reasons, message=message)

    def _restriction_issues(self) -> list[PlanningIssue]:
        out = []
        for key, (term, check) in sorted(self._restricted.items()):
            out.append(PlanningIssue(
                severity=Severity.NEEDS_CONFIRMATION, code="SECTION_RESTRICTED", course=key,
                message=f"{key} was not planned for {label(term)}: {check.message}",
                details={"term": term, "open_to": check.open_to, "reasons": check.reasons}))
        return out

    def _core_evidence(self, key: str, term: str, ev: OfferingEvidence):
        """The coreCodes listing a Core contribution in `term` rests on: the
        term's own when Rutgers published it (confirmed offering), else the
        course's latest loaded offering - labelled. None: nothing to read."""
        from app.services.sources.sections import core_codes

        basis_term = (term if ev.kind is OfferingEvidenceKind.CONFIRMED_IN_TERM
                      else ev.most_recent_term)
        if basis_term is None:
            return None
        if (key, basis_term) not in self._core_cache:
            self._core_cache[(key, basis_term)] = core_codes(
                self.session, basis_term, [key]).get(key)
        listed = self._core_cache[(key, basis_term)]
        if listed is None:
            return None
        return CoreCodeEvidence(
            term_code=basis_term,
            basis="planned_term" if basis_term == term else "latest_offering",
            codes=list(listed.codes))

    def _core_supported(self, key: str, code: str, term: str, ev: OfferingEvidence):
        """True/False: does the term's listing certify a goal this Core
        requirement accepts the course for? None: cannot be checked."""
        goals = self.pool_categories.get((code, key), set()) - {""}
        evidence = self._core_evidence(key, term, ev)
        if not goals or evidence is None:
            return None
        return bool(goals & set(evidence.codes))

    def _pattern(self, key: str, term: str) -> OfferingPattern:
        """Regular seasons the loaded schedules show the course in. "Only
        Fall" needs two or more Fall offerings and none in Spring while two or
        more Spring terms were loaded since its first offering."""
        observed = [t for t in self.offerings.terms.get(key, []) if t[4] in "19"]
        if not observed:
            return OfferingPattern()
        seasons = sorted({SEASON[t[4]] for t in observed})
        out = OfferingPattern(seasons=seasons)
        if len(seasons) != 1:
            return out
        season = observed[0][4]
        other = "9" if season == "1" else "1"
        loaded_other = [t for t in self.offerings.coverage if t[4] == other and t >= observed[0]]
        if len(observed) < 2 or len(loaded_other) < 2:
            return out
        out.only_season = SEASON[season]
        if term[4] == season:
            out.next_chance = f"{int(term[:4]) + 1}{season}"
            out.message = (f"Offered only in {SEASON[season]} in the schedules CoursePilot has "
                           f"loaded ({len(observed)} {SEASON[season]} terms, none in "
                           f"{SEASON[other]}); after {label(term)} the next chance is "
                           f"{label(out.next_chance)}.")
        else:
            out.message = (f"Offered only in {SEASON[season]} in the schedules CoursePilot has "
                           f"loaded ({len(observed)} {SEASON[season]} terms).")
        return out

    def _required_courses(self) -> set[str]:
        """Courses every remaining path must include (see `_chain_depths`)."""
        self._chain_depths()
        return self._required

    def _chain_depths(self) -> dict[str, int]:
        """course -> the longest chain of REQUIRED open courses (not yet
        passed) whose latest published prerequisites lead back to it. A
        course nothing required cites has depth 0. Published rules only.

        "Required": a single-course requirement, or a choose-N whose remaining
        pool is no larger than N - every course in it must be taken. A pool
        with alternatives (Core Natural Sciences, CS electives) is left out:
        a chain inside it (Chemistry 161 -> 162) says nothing about what the
        student must take, and counting it made such courses outrank the
        major's own sequence (measured with plan_quality.py)."""
        if self._chain is not None:
            return self._chain
        open_codes = getattr(self, "_open_at_start", set(self.pools))
        required: set[str] = set()
        by_id = {r.id: r for r in self.requirements.values()}

        def under_alternative(req) -> bool:
            # A leaf inside an ANY_OF is one alternative (Chemistry 161 in the
            # B.S. science choice), not something every path must include;
            # ranking it by its chain made the plan abandon a sequence it had
            # started (measured: B.S. full-coverage plans 3 -> 0).
            parent = by_id.get(req.parent_id)
            while parent is not None:
                if parent.requirement_type == "any_of":
                    return True
                parent = by_id.get(parent.parent_id)
            return False

        for code in open_codes:
            req = self.requirements.get(code)
            courses = set(self.pools.get(code, ())) - self.passed
            if req is None or not courses or under_alternative(req):
                continue
            if req.requirement_type == "course" or (
                    req.requirement_type == "choose_n" and req.min_count
                    and len(courses) <= req.min_count):
                required |= courses
        self._required = required
        pool = sorted(required)
        cited_by: dict[str, set[str]] = defaultdict(set)
        for course in pool:
            rows = self.prereq_rows.get(course)
            if not rows:
                continue
            row = rows[max(rows)]
            if row.expression is None:
                continue
            for ref in set(course_keys(from_json(row.expression))) - {course}:
                cited_by[ref].add(course)
        memo: dict[str, int] = {}

        def depth(course: str, stack: frozenset) -> int:
            if course in memo:
                return memo[course]
            best = 0
            for nxt in sorted(cited_by.get(course, ())):
                if nxt not in stack:
                    best = max(best, 1 + depth(nxt, stack | {nxt}))
            memo[course] = best
            return best

        self._chain = {k: d for k in sorted(cited_by) if (d := depth(k, frozenset({k})))}
        return self._chain

    def _source_of(self, key: str, term: str | None):
        """The SOC `data_source` row the course's offering in `term` came from."""
        if term is None:
            return None
        if self._offering_sources is None:
            self._offering_sources = {}
        if (key, term) not in self._offering_sources:
            from app.models import DataSource
            row = self.session.execute(
                select(DataSource.id, DataSource.retrieved_at)
                .join(CourseOffering, CourseOffering.source_id == DataSource.id)
                .join(Course, Course.id == CourseOffering.course_id)
                .where(Course.course_string == key, CourseOffering.term_code == term,
                       Course.supplement_code == "")).first()
            self._offering_sources[(key, term)] = row
        return self._offering_sources[(key, term)]

    def _status_source(self, code: str) -> tuple[str, str | None]:
        """degree_navigator (with DN's rule) when the node or a group containing
        it takes its status from the student's DN audit; else estimated."""
        seen = set()
        current: str | None = code
        while current is not None and current not in seen:
            seen.add(current)
            status = self.overlay.statuses.get(current) if self.overlay else None
            if status is not None:
                return "degree_navigator", status.rule
            req = self.requirements.get(current)
            current = self.by_id_code.get(req.parent_id) if req is not None else None
        return "estimated", None

    def _trace(self, pc: PlannedCourse) -> None:
        """Attach the structured reason (RecommendationTrace) to a planned course."""
        ev, pre = pc.offering, pc.prerequisite
        offering_term = (pc.term_code if ev.kind is OfferingEvidenceKind.CONFIRMED_IN_TERM
                         else (ev.same_season_terms[-1] if ev.same_season_terms
                               else ev.most_recent_term or pc.term_code))
        traced, sources = [], []
        dn_dates = set()
        for contribution in pc.requirements:
            source, rule = self._status_source(contribution.requirement_code)
            traced.append(TracedRequirement(
                requirement_code=contribution.requirement_code,
                requirement_name=contribution.requirement_name,
                requirement_system=contribution.requirement_system,
                status_source=source, status_rule=rule))
            if source == "degree_navigator":
                dn_dates |= {s.as_of.date().isoformat() for s in self.overlay.statuses.values()
                             if s.as_of}
        row = self._source_of(pc.course, offering_term)
        sources.append(SourceLabel(
            fact="offering", source="soc_courses_json", term_code=offering_term,
            data_source_id=str(row[0]) if row else None, fetched_at=row[1] if row else None,
            detail=("listed in this term's published schedule"
                    if ev.kind is OfferingEvidenceKind.CONFIRMED_IN_TERM else
                    "historical evidence: offered in the same season before")))
        if pre.rules_term:
            rules = self._source_of(pc.course, pre.rules_term)
            sources.append(SourceLabel(
                fact="prerequisite_rules", source="soc_courses_json", term_code=pre.rules_term,
                data_source_id=str(rules[0]) if rules else None,
                fetched_at=rules[1] if rules else None,
                detail=pre.canonical_text or pre.raw_text or "no prerequisite published"))
        core = None
        core_codes_here = [c for c in pc.requirements if c.requirement_system == "core"]
        if core_codes_here:
            core = self._core_evidence(pc.course, pc.term_code, ev)
            if core is not None:
                for c in core_codes_here:
                    goals = self.pool_categories.get((c.requirement_code, pc.course), set()) - {""}
                    for g in sorted(goals):
                        core.goals[g] = g in core.codes
                    if goals and not goals & set(core.codes):
                        note = (f"{c.requirement_name}: the {label(core.term_code)} SOC listing "
                                f"does not list {', '.join(sorted(goals))} for this course - "
                                "confirm it will count before relying on it.")
                        pc.notes.append(note)
                        self._trace_issues.append(PlanningIssue(
                            severity=Severity.NEEDS_CONFIRMATION,
                            code="CORE_CODE_NOT_LISTED_FOR_TERM", course=pc.course,
                            requirement_code=c.requirement_code, message=note,
                            details={"term": core.term_code, "codes": core.codes}))
                listed = self._core_cache.get((pc.course, core.term_code))
                sources.append(SourceLabel(
                    fact="core_codes", source="soc_courses_json", term_code=core.term_code,
                    data_source_id=listed.provenance.data_source_id if listed else None,
                    fetched_at=listed.provenance.fetched_at if listed else None,
                    detail=("coreCodes " + (", ".join(core.codes) or "none")
                            + ("" if core.basis == "planned_term"
                               else " (latest loaded offering; the planned term is not "
                                    "published yet)"))))
        restriction = self._restriction(pc.term_code, pc.course)
        if restriction.outcome != "sections_not_published":
            sources.append(SourceLabel(
                fact="restrictions", source="soc_courses_json", term_code=pc.term_code,
                data_source_id=str(row[0]) if row and offering_term == pc.term_code else None,
                fetched_at=row[1] if row and offering_term == pc.term_code else None,
                detail=restriction.outcome))
        if restriction.outcome == "unknown" and restriction.message:
            pc.notes.append(restriction.message)
            self._trace_issues.append(PlanningIssue(
                severity=Severity.NEEDS_CONFIRMATION, code="SECTION_RESTRICTION_UNCONFIRMED",
                course=pc.course, message=f"{pc.course}: {restriction.message}",
                details={"term": pc.term_code, "open_to": restriction.open_to}))
        if traced:
            sources.append(SourceLabel(
                fact="requirement_status",
                source=("degree_navigator_audit" if any(t.status_source == "degree_navigator"
                                                        for t in traced) else "coursepilot_audit"),
                detail=(f"Degree Navigator audit as of {', '.join(sorted(dn_dates))}"
                        if dn_dates else "estimated by CoursePilot's own audit")))
        pattern = self._pattern(pc.course, pc.term_code)
        if pattern.message and pattern.only_season and pattern.next_chance:
            pc.notes.append(pattern.message)
        if pc.role == "multi_requirement" and len(pc.requirements) < 2:
            names = ", ".join(c.requirement_name for c in pc.requirements) or "no requirement"
            pc.role_reason = (f"{pc.role_reason}; the final allocation credits it only to "
                              f"{names} (the sharing policy lets a course count once per area)")
        pc.trace = RecommendationTrace(
            requirements=traced, unlocks_for=list(pc.unlocks), offering_kind=ev.kind,
            offering_term=offering_term, prerequisite_state=pre.state,
            prerequisite_rules_term=pre.rules_term, core_codes=core,
            restriction=restriction, pattern=pattern,
            unlock_chain_depth=self._chain_depths().get(pc.course, 0), sources=sources)

    # ---- issues ----------------------------------------------------------

    def _diagnose(self, remaining, needs_confirmation, unplaceable, cycles, horizon) -> list:
        out = []
        for leaf in remaining:
            code = leaf.requirement_code
            pool = sorted(k for k in self.pools.get(code, ()) if k not in self.excluded)
            details = {"needed": str(deficit(leaf))}
            if not pool:
                out.append(PlanningIssue(
                    severity=Severity.BLOCKER, code="NO_ELIGIBLE_COURSE", requirement_code=code,
                    message=f"No course in CoursePilot's data can satisfy {leaf.requirement_name}.",
                    details=details))
                continue
            cycle = next((cycles[k] for k in pool if k in cycles), None)
            if cycle:
                out.append(PlanningIssue(
                    severity=Severity.BLOCKER, code="DEPENDENCY_CYCLE", requirement_code=code,
                    message="A prerequisite chain for this requirement is circular.",
                    details={**details, "cycle": cycle}))
            if needs_confirmation.get(code):
                out.append(PlanningIssue(
                    severity=Severity.NEEDS_CONFIRMATION, code="ELIGIBILITY_UNKNOWN",
                    requirement_code=code,
                    message=(f"Some courses for {leaf.requirement_name} have prerequisites or "
                             "co-requisites CoursePilot cannot evaluate; they were not planned."),
                    details={**details, "courses": {k: v for k, v in
                                                    sorted(needs_confirmation[code].items())[:10]},
                             "count": len(needs_confirmation[code])}))
            kinds = defaultdict(list)
            for k, why in sorted(unplaceable.get(code, {}).items()):
                kinds[why].append(k)
            if kinds:
                out.append(PlanningIssue(
                    severity=Severity.NEEDS_CONFIRMATION, code="NO_PLACEABLE_OFFERING",
                    requirement_code=code,
                    message=(f"Courses for {leaf.requirement_name} lack offering evidence for "
                             "the planned terms or have variable credits."),
                    details={**details,
                             "by_reason": {w: ks[:10] for w, ks in sorted(kinds.items())},
                             "counts": {w: len(ks) for w, ks in sorted(kinds.items())}}))
            out.append(PlanningIssue(
                severity=Severity.BLOCKER,
                code="PLAN_HORIZON_REACHED" if horizon else "REQUIREMENT_NOT_PLANNED",
                requirement_code=code,
                message=(f"{leaf.requirement_name} could not be completed within the plan horizon."
                         if horizon else f"{leaf.requirement_name} could not be planned with "
                         "the evidence CoursePilot has (see related issues)."),
                details={**details, "status": leaf.status.value}))
        return out

    @staticmethod
    def _rule_issues(final: DegreeAuditResult) -> list:
        out = []
        for rule in final.rules:
            if rule.status is RequirementStatus.NOT_EVALUABLE:
                out.append(PlanningIssue(
                    severity=Severity.WARNING, code="UNRESOLVED_PROGRAM_RULE",
                    requirement_code=rule.rule_code,
                    message=f"{rule.rule_name}: {rule.reason}",
                    details={"rule_type": rule.rule_type}))
            elif rule.status is RequirementStatus.UNSATISFIED:
                out.append(PlanningIssue(
                    severity=Severity.BLOCKER, code="PROGRAM_RULE_VIOLATED",
                    requirement_code=rule.rule_code, message=f"{rule.rule_name}: {rule.reason}"))
        return out


__all__ = ["PlanningEngine", "active_leaves", "deficit", "progress"]
