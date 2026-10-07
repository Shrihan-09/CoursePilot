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
    6. the course string (canonical tie-break).

A requirement candidate is ACCEPTED only if the Degree Engine, re-run with it,
shows strict progress (more requirement slots/credits met). A course eligible
for two requirements is therefore credited wherever the engine's own
allocation puts it - once - and a course that adds nothing is not planned.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.attempts import Attempt
from app.domain.audit import DegreeAuditResult, RequirementResult, RequirementStatus
from app.domain.grades import Tri, earns_credit
from app.domain.planning import (
    ConditionalDependency,
    EligibilityState,
    OfferingEvidence,
    OfferingEvidenceKind,
    PlanMetadata,
    PlannedCourse,
    PlanningConstraints,
    PlanningIssue,
    PlanResult,
    PlanStatus,
    PlanTerm,
    PrerequisiteEvidence,
    Reason,
    RequirementContribution,
    RequirementProjection,
    Severity,
)
from app.domain.prerequisites import ConcurrentReq, PrereqStatus, course_keys, from_json
from app.domain.scenario import ScenarioAssumption, ScenarioTarget
from app.models import (
    Course,
    CourseCorequisite,
    CoursePrerequisite,
    ProgramRule,
    ProgramVersion,
    Requirement,
    RequirementCourseOption,
    Student,
    StudentCourse,
)
from app.services.audit.cache import AUDIT_ENGINE_VERSION, academic_fingerprint, rules_fingerprint
from app.services.audit.engine import DegreeAuditEngine, constraint_counts
from app.services.course_eligibility import EligibilityCheck, check_proposal
from app.services.planning.dependencies import UNREADABLE, DependencyPlanner
from app.services.planning.offerings import PLACEABLE, OfferingIndex
from app.services.planning.terms import label, planning_terms
from app.services.prerequisites import attempts_by_course

LEAF_TYPES = ("course", "choose_n", "credits")
MET = (RequirementStatus.SATISFIED, RequirementStatus.PROVISIONALLY_SATISFIED)


# --------------------------------------------------------------------------
# reading the Degree Engine's result
# --------------------------------------------------------------------------


def active_leaves(results: list[RequirementResult]) -> list[RequirementResult]:
    """Unmet leaf requirements, honouring alternatives.

    An ANY_OF node that is not met plans only as many of its unmet children
    as it still needs - the least-unmet first, then by code - instead of
    every alternative.
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
            children = open_[:need]
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


def progress(result: DegreeAuditResult, requirements: dict | None = None) -> tuple:
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
    for node in _walk(result.requirements):
        if node.requirement_type not in LEAF_TYPES:
            continue
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
    def __init__(self, session: Session, constraints: PlanningConstraints | None = None) -> None:
        self.session = session
        self.constraints = constraints or PlanningConstraints()
        self.audit_engine = DegreeAuditEngine(session)
        self.audit_engine.baseline_memo = {}

    # ---- data, loaded in bulk ------------------------------------------

    def _load_static(self, version: ProgramVersion) -> None:
        s = self.session
        self.requirements = {r.code: r for r in s.scalars(
            select(Requirement).where(Requirement.program_version_id == version.id))}
        self.pools: dict[str, set[str]] = defaultdict(set)
        self.course_info: dict[str, Course] = {}
        for code, course in s.execute(
                select(Requirement.code, Course)
                .join(RequirementCourseOption,
                      RequirementCourseOption.requirement_id == Requirement.id)
                .join(Course, Course.id == RequirementCourseOption.course_id)
                .where(Requirement.program_version_id == version.id)).all():
            self.pools[code].add(course.course_string)
            self.course_info[course.course_string] = course
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

    def _load_courses(self, keys: set[str]) -> None:
        missing = sorted(k for k in keys if k not in self.course_info and k not in self._looked_up)
        self._looked_up |= set(missing)
        if missing:
            for course in self.session.scalars(select(Course).where(
                    Course.course_string.in_(missing), Course.supplement_code == "")):
                self.course_info.setdefault(course.course_string, course)
            self.offerings.extend(self.session, set(missing))
            self._load_prerequisites(set(missing))

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
        for key, row in self.session.execute(
                select(Course.course_string, CoursePrerequisite)
                .join(Course, Course.id == CoursePrerequisite.course_id)
                .where(Course.course_string.in_(sorted(keys)))).all():
            self.prereq_rows[key][row.term_code] = row

    def _expression_of(self, course: str):
        """Latest published prerequisite IR (for dependency PATHS only)."""
        self._load_courses({course})
        rows = self.prereq_rows.get(course)
        if not rows:
            return None
        row = rows[max(rows)]
        if row.expression is None:
            return UNREADABLE
        return from_json(row.expression)

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

    def _audit(self, student, version, planned) -> DegreeAuditResult:
        return self.audit_engine.audit(student, program_version=version,
                                       projected=self._projected(student, planned))

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
        if state is EligibilityState.CONDITIONAL_ON_PLAN:
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
        c = self.constraints
        self._load_static(version)
        self._history(student)
        issues: list[PlanningIssue] = []
        planned: dict[str, str] = {}                 # course -> term
        info: dict[str, dict] = {}                   # course -> planning facts
        dependency_targets: dict[str, set[str]] = defaultdict(set)
        needs_confirmation: dict[str, dict[str, list[str]]] = defaultdict(dict)
        unplaceable: dict[str, dict[str, str]] = defaultdict(dict)
        cycles: dict[str, list[str]] = {}

        before = self.audit_engine.audit(student, program_version=version)
        current = before
        terms = planning_terms(start_term, c.max_terms, include_summer=c.include_summer,
                               include_winter=c.include_winter)
        plan_terms: list[PlanTerm] = []
        empty_streak = 0
        stalled = False
        for term in terms:
            if not active_leaves(current.requirements):
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
        self._finalize_courses(plan_terms, final, info, planned)
        remaining = active_leaves(final.requirements)
        issues += self._diagnose(remaining, needs_confirmation, unplaceable, cycles,
                                 horizon=bool(remaining) and not stalled)
        issues += self._rule_issues(final)
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

        before_status = {n.requirement_code: n.status.value for n in _walk(before.requirements)
                         if n.requirement_type in LEAF_TYPES}
        projections = [RequirementProjection(
                           requirement_code=n.requirement_code,
                           requirement_name=n.requirement_name,
                           status_before=before_status.get(n.requirement_code, ""),
                           status_after=n.status.value)
                       for n in sorted(_walk(final.requirements), key=lambda n: n.requirement_code)
                       if n.requirement_type in LEAF_TYPES]
        if not active_leaves(before.requirements):
            status = PlanStatus.NOTHING_TO_PLAN
        elif not remaining:
            status = PlanStatus.COVERS_ALL_REQUIREMENTS
        else:
            status = PlanStatus.PARTIAL
        return PlanResult(
            target=target, status=status, terms=plan_terms,
            issues=sorted(issues, key=lambda i: (i.severity.value, i.code,
                                                 i.requirement_code or "", i.course or "")),
            requirements=projections, assumptions=assumptions,
            metadata=PlanMetadata(
                audit_engine_version=AUDIT_ENGINE_VERSION, start_term=start_term, constraints=c,
                academic_fingerprint=academic_fingerprint(self.session, student),
                rules_fingerprint=rules_fingerprint(self.session, version.id),
                offering_dataset=self.offerings.dataset_identity(self.session)))

    # ---- one term --------------------------------------------------------

    def _fill_term(self, student, version, term, term_plan, current, planned, info,
                   dependency_targets, needs_confirmation, unplaceable,
                   cycles) -> DegreeAuditResult:
        """Fill one term; returns the Degree Engine's result with the plan so far."""
        c = self.constraints
        before_planned = dict(planned)
        leaves = active_leaves(current.requirements)
        leaf_by_code = {leaf.requirement_code: leaf for leaf in leaves}
        serves: dict[str, list[str]] = defaultdict(list)
        for leaf in leaves:
            for key in sorted(self.pools.get(leaf.requirement_code, ())):
                if key in self.passed or key in planned or key in self.in_progress \
                        or key in self.excluded:
                    continue
                serves[key].append(leaf.requirement_code)
        dependencies = sorted(k for k in dependency_targets
                              if k not in planned and (k not in self.passed or k in self.retakes))
        self._load_closure(set(serves) | set(dependencies))
        keys = sorted(set(serves) | set(dependencies))
        evaluated = self._evaluate(student, keys, term, planned)

        states = {k: self._state(a, b) for k, (a, b, _) in evaluated.items()}
        ready = {k for k, s in states.items()
                 if s in (EligibilityState.SATISFIED_BY_HISTORY,
                          EligibilityState.CONDITIONAL_ON_PLAN)
                 and evaluated[k][2].kind in PLACEABLE
                 and self.course_info[k].credits is not None}

        # Record why candidates cannot be placed (per requirement), for diagnosis.
        for key in keys:
            for code in serves.get(key, ()):
                ev = evaluated[key][2]
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
        planner = DependencyPlanner(
            expression_of=self._expression_of,
            available=self.passed | set(self.in_progress) | set(planned),
            contributes=lambda k: k in serves,
            plannable=self._plannable)
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
                states[k] = self._state(*evaluated[k][:2])
            if (states[k] in (EligibilityState.SATISFIED_BY_HISTORY,
                              EligibilityState.CONDITIONAL_ON_PLAN)
                    and evaluated[k][2].kind in PLACEABLE
                    and self.course_info[k].credits is not None):
                ready.add(k)
                continue
            # A prerequisite the plan needs cannot be placed this term: say
            # so under every requirement whose candidate it would unlock.
            why = (f"dependency_{states[k].value}" if evaluated[k][2].kind in PLACEABLE
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
            and evaluated[k][2].kind in PLACEABLE and self.course_info[k].credits is not None}

        # Ranking (see the module docstring).
        def scarcity(key):
            codes = serves.get(key, ())
            if not codes:
                return Decimal(-1)
            return min(sum(1 for k in ready if code in serves.get(k, ()))
                       - deficit(leaf_by_code[code]) for code in codes)

        def rank(key):
            is_dep = key in dependency_targets and key not in planned
            unlocks = len(dependency_targets.get(key, ()))
            ev = evaluated[key][2]
            return (0 if is_dep else 1, -unlocks, scarcity(key),
                    0 if ev.kind is OfferingEvidenceKind.CONFIRMED_IN_TERM else 1,
                    0 if states[key] is EligibilityState.SATISFIED_BY_HISTORY else 1, key)

        order = sorted(ready | coreq_pending, key=rank)
        credits = Decimal(0)
        count = 0
        latest = current
        score = progress(current, self.requirements)
        open_codes = {leaf.requirement_code for leaf in active_leaves(current.requirements)}
        for key in order:
            if count >= c.max_courses_per_term or credits >= c.max_credits_per_term:
                break
            if key in planned:
                continue
            is_dep = key in dependency_targets
            # A requirement course whose requirements the engine already
            # reports met (by earlier picks) cannot add progress: no audit.
            if not is_dep and not set(serves.get(key, ())) & open_codes:
                continue
            group = [key]
            coreq_partner = None
            if key in coreq_pending:
                partner = self._partner(student, key, evaluated[key][1], term, planned)
                if partner is None:
                    continue
                coreq_partner = partner
                if partner not in planned:      # else it is already in THIS term
                    group.append(partner)
                proposed = self._evaluate(student, [key], term, planned, frozenset({partner}))
                evaluated[key] = proposed[key]
                states[key] = self._state(*evaluated[key][:2])
            group_credits = sum((self.course_info[g].credits for g in group), Decimal(0))
            if count + len(group) > c.max_courses_per_term or \
                    credits + group_credits > c.max_credits_per_term:
                continue
            trial = {**planned, **{g: term for g in group}}
            if serves.get(key) or not is_dep:
                result = self._audit(student, version, trial)
                after = progress(result, self.requirements)
                if after <= score and not is_dep:
                    continue
                score, latest = max(after, score), result
                open_codes = {leaf.requirement_code for leaf in active_leaves(result.requirements)}
            planned.update({g: term for g in group})
            credits += group_credits
            count += len(group)
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
                }
                term_plan.courses.append(self._planned_course(g, info[g]))
                term_plan.credits += self.course_info[g].credits
        if latest is current and planned != before_planned:
            latest = self._audit(student, version, planned)
        return latest

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

    def _planned_course(self, key, facts) -> PlannedCourse:
        course = self.course_info[key]
        ev: OfferingEvidence = facts["evidence"]
        pre: PrerequisiteEvidence = facts["prereq"]
        reasons = []
        if pre.state is EligibilityState.SATISFIED_BY_HISTORY:
            reasons.append(Reason.PREREQUISITES_SATISFIED_BY_HISTORY if pre.raw_text or
                           pre.corequisite_text else Reason.NO_PREREQUISITE_PUBLISHED)
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
        return PlannedCourse(
            course=key, title=course.title, credits=course.credits, term_code=facts["term"],
            reasons=reasons, prerequisite=pre, offering=ev, corequisite_group=facts["group"],
            unlocks=sorted(set(facts["dependency_for"]) | set(facts["corequisite_for"])),
            interchangeable_alternatives=facts["alternatives"])

    def _prune(self, student, version, plan_terms, planned, info, final) -> DegreeAuditResult:
        """Drop planned courses the final allocation credits nowhere.

        Greedy acceptance can let a later pick displace an earlier one (the
        engine re-allocates globally). A course is removed - latest term
        first, then by course string - when no requirement is credited with
        it, no other planned course depends on it or is grouped with it, and
        the Degree Engine confirms progress does not drop without it.
        """
        def credited(result):
            return {a.course.course_string for a in result.allocation}

        score = progress(final, self.requirements)
        for key in sorted(planned, key=lambda k: (planned[k], k), reverse=True):
            if key in credited(final):
                continue
            needed_by = any(key in {d.course for d in info[o]["prereq"].conditional_on}
                            or key in info[o]["group"]
                            for o in planned if o != key)
            if needed_by:
                continue
            trial = {k: t for k, t in planned.items() if k != key}
            result = self._audit(student, version, trial) if trial else \
                self.audit_engine.audit(student, program_version=version)
            if progress(result, self.requirements) < score:
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
            term.courses.sort(key=lambda pc: pc.course)

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
