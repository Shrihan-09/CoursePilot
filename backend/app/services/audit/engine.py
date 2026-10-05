"""The deterministic degree audit engine.

Pure evaluation over data. No LLM, no network, no randomness.

Order of operations, and why:

  1. Load the requirement tree for the student's program VERSION (not the
     program), so the rules are the ones that bind this student.
  2. Build course eligibility from `requirement_course_option`.
  3. Allocate courses to requirement slots (see `allocation.py`).
  4. Evaluate every node bottom-up against its allocation.
  5. Derive the overall status from the results.

Allocation happens BEFORE evaluation because a requirement's status depends on
which courses it actually got, and that is a global decision - deciding it
locally, node by node, is exactly the greedy bug the allocator exists to
avoid.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.observability import redact_id
from app.domain.attempts import Attempt, degree_credit_attempt, degree_minimum_grade
from app.domain.audit import (
    Allocation,
    AuditFinding,
    AuditStatus,
    CourseRef,
    DegreeAuditResult,
    GradeEvaluation,
    RequirementResult,
    RequirementStatus,
    RuleResult,
    Severity,
)
from app.models import (
    Course,
    EnrollmentStatus,
    ProgramRule,
    ProgramVersion,
    SharingPolicy,
    Requirement,
    RequirementCourseOption,
    RequirementType,
    Student,
    StudentCourse,
)
from app.services.audit.allocation import (
    AllocationPlan,
    Candidate,
    Slot,
    allocate,
    allocate_credits,
    max_distinct_categories,
)
from app.services.audit.optimizer import (
    DEFAULT_OBJECTIVE,
    AllocationProblem,
    GlobalAllocationObjective,
    ObjectiveContext,
    RequirementSpec,
)
from app.services.audit.categories import (
    DEFAULT_STRATEGY,
    CategoryAllocationStrategy,
    CategoryCandidate,
    CategoryRequest,
)
from app.domain.grades import LETTERS, POLICY_SOURCES, Tri, at_most, earns_credit, outcome
from app.services.audit.rules import (
    RuleContext,
    StudentCourseView,
    evaluate_rule,
    excluded_course_strings,
)

logger = logging.getLogger(__name__)

DISCLAIMER = (
    "CoursePilot is a planning aid, not an official degree audit. Rutgers "
    "states that verification of college and degree requirements can only be "
    "certified by an academic advisor."
)

# Statuses that can count toward a requirement at all. PLANNED never does:
# a planned course is an intention, not evidence.
COUNTABLE = {EnrollmentStatus.COMPLETED.value, EnrollmentStatus.IN_PROGRESS.value}

# Which grades earn credit is no longer a list kept here: it is
# app.domain.grades.earns_credit, shared with the prerequisite evaluator
# (Phase 6.4). The pre-6.4 list {F, D-, NC, W} contained "D-", which is not a
# Rutgers grade, and missed XF, S and U.


def _attempts_by_course(records) -> dict:
    out: dict = defaultdict(list)
    for sc, course in records:
        out[course.id].append(Attempt(course.course_string, sc.term_code, sc.status, sc.grade,
                                      sc.credit_origin or "rutgers", handle=sc))
    return out


def strictest(grades) -> str | None:
    """The most demanding of several minimum grades ("B" over "C")."""
    present = [g for g in grades if g]
    return min(present, key=LETTERS.index) if present else None


def constraint_counts(req, allocated: list[dict]) -> dict[str, int | None]:
    """How many allocated courses fall OUTSIDE the constrained subject and how
    many are AT the required level (None when the requirement has no such
    constraint). `allocated` items need "subject_code" and "course_number".

    The single definition of both predicates: the audit's violations below
    and Phase 6.5's planner (measuring progress toward a constraint the count
    alone does not show) both read it.
    """
    outside = at_level = None
    if req.max_outside_subject is not None and req.constraint_subject_code:
        outside = sum(1 for e in allocated if e["subject_code"] != req.constraint_subject_code)
    if req.min_at_level is not None and req.min_at_level_count is not None:
        at_level = sum(
            1
            for e in allocated
            if e["subject_code"] == (req.constraint_subject_code or e["subject_code"])
            and e["course_number"].isdigit()
            and int(e["course_number"]) >= req.min_at_level
        )
    return {"outside": outside, "at_level": at_level}


def _constraint_checks(req, allocated: list[dict]) -> list[str]:
    counts = constraint_counts(req, allocated)
    problems: list[str] = []
    if counts["outside"] is not None and counts["outside"] > req.max_outside_subject:
        problems.append(
            f"at most {req.max_outside_subject} may be outside subject "
            f"{req.constraint_subject_code} (found {counts['outside']})"
        )
    if counts["at_level"] is not None and counts["at_level"] < req.min_at_level_count:
        problems.append(
            f"at least {req.min_at_level_count} must be at the {req.min_at_level} "
            f"level or above (found {counts['at_level']})"
        )
    return problems


class DegreeAuditEngine:
    """Evaluates one student against one program version."""

    def __init__(
        self,
        session: Session,
        category_strategy: CategoryAllocationStrategy | None = None,
        objective: "GlobalAllocationObjective | None" = None,
    ) -> None:
        self.session = session
        # Internal seam, not a user-facing setting: it exists so the two
        # category strategies can be compared on identical real input.
        # Production always uses DEFAULT_STRATEGY.
        self.category_strategy = category_strategy or DEFAULT_STRATEGY
        # The adopted global objective (Phase 4.5). Overridable for tests and
        # for measuring alternatives; production uses DEFAULT_OBJECTIVE.
        self.objective = objective or DEFAULT_OBJECTIVE
        # Guards the baseline audit against recomputing its own baseline.
        self._computing_baseline = False
        # Phase 6.5: the earned baseline depends only on stored completed
        # courses, which projected (planned) attempts never are, so a caller
        # auditing many projections of one unchanged record may opt in to
        # reusing it. None = recompute every time (the default).
        self.baseline_memo: dict | None = None

    # ------------------------------------------------------------------ #
    # loading
    # ------------------------------------------------------------------ #

    def _load_requirements(self, version_id) -> list[Requirement]:
        return list(
            self.session.scalars(
                select(Requirement)
                .where(Requirement.program_version_id == version_id)
                .order_by(Requirement.sort_order, Requirement.code)
            ).all()
        )

    def _load_rules(self, version_id) -> list[ProgramRule]:
        return list(
            self.session.scalars(
                select(ProgramRule)
                .where(ProgramRule.program_version_id == version_id)
                .order_by(ProgramRule.code)
            ).all()
        )

    def _load_eligibility(
        self, requirement_ids: list
    ) -> tuple[dict[str, set[str]], dict[tuple[str, str], set[str]]]:
        """Returns (course_id -> requirement codes, (course_id, req) -> categories).

        The second map is what makes "meet at least two of these goals"
        answerable: it records WHY a course is eligible, not merely that it is.
        """
        if not requirement_ids:
            return {}, {}
        rows = self.session.execute(
            select(
                RequirementCourseOption.course_id,
                Requirement.code,
                RequirementCourseOption.category,
            )
            .join(Requirement, Requirement.id == RequirementCourseOption.requirement_id)
            .where(RequirementCourseOption.requirement_id.in_(requirement_ids))
        ).all()
        out: dict[str, set[str]] = defaultdict(set)
        categories: dict[tuple[str, str], set[str]] = defaultdict(set)
        for course_id, code, category in rows:
            out[str(course_id)].add(code)
            if category:
                categories[(str(course_id), code)].add(category)
        return out, categories

    def _load_student_courses(
        self, student: Student, statuses: frozenset[str] | None = None
    ) -> list[tuple[StudentCourse, Course]]:
        rows = self.session.execute(
            select(StudentCourse, Course)
            .join(Course, Course.id == StudentCourse.course_id)
            .where(StudentCourse.student_id == student.id)
            .order_by(Course.course_string, Course.supplement_code, StudentCourse.term_code)
        ).all()
        # `statuses` narrows the record WITHOUT changing how anything is
        # evaluated. Phase 4.5 uses it to derive a completed-courses-only
        # baseline through the real evaluator rather than a second copy of
        # the satisfaction rules. Default: every row, exactly as before.
        if statuses is not None:
            return [(sc, c) for sc, c in rows if sc.status in statuses]
        return [(sc, c) for sc, c in rows]

    @staticmethod
    def _representative_attempts(
        records: list[tuple[StudentCourse, Course]],
    ) -> dict:
        """The one attempt per course that allocation and credit may use.

        Phase 6.2 made one course one allocation identity. Phase 6.4 chooses
        WHICH attempt by the SAS repeated-course policy
        (app.domain.attempts.degree_credit_attempt): attempts after a final
        C-or-better are E credit and never represent the course; among the
        rest, the latest credit-earning attempt, else the latest provisional
        one. Still not "highest grade wins" and not "latest wins".
        """
        return {course_id: choice.attempt.handle
                for course_id, choice in (
                    (cid, degree_credit_attempt(atts))
                    for cid, atts in _attempts_by_course(records).items())
                if choice.attempt is not None}

    # ------------------------------------------------------------------ #
    # slots
    # ------------------------------------------------------------------ #

    @staticmethod
    def _slots_needed(req: Requirement, student_eligible: int = 0) -> int:
        """How many courses this requirement node demands.

        ALL_OF and ANY_OF are *structural* - they are satisfied by their
        children, not by courses directly - so they demand no slots of their
        own. Only leaf-ish nodes consume courses.

        CREDITS is the interesting case. The requirement states a credit
        total, not a course count, so there is no count to read off the
        definition - and inventing one (say, min_credits / 3) would assume a
        course size the source never states. Instead the node is given one
        slot per course the STUDENT actually holds that is eligible for it.
        That is data-derived, deterministic, and never larger than the number
        of courses that could possibly count.
        """
        rtype = req.requirement_type
        if rtype == RequirementType.COURSE.value:
            return 1
        if rtype == RequirementType.CHOOSE_N.value:
            return req.min_count or 0
        if rtype == RequirementType.CREDITS.value:
            # Deliberately ZERO. A matching allocates course-slots, which is
            # the wrong unit for a credit minimum - giving one slot per
            # eligible course let a 6-credit requirement claim five courses
            # and starve a competing count requirement. Credit requirements
            # are settled after the matching by allocate_credits().
            return 0
        return 0

    # ------------------------------------------------------------------ #
    # global allocation
    # ------------------------------------------------------------------ #

    def _optimize_globally(
        self,
        requirements: list[Requirement],
        candidates: list[Candidate],
        eligibility: dict[str, set[str]],
        option_categories: dict[tuple[str, str], set[str]],
        share: bool,
        student: Student,
        version: ProgramVersion,
    ):
        """Re-allocate under the adopted global objective.

        Returns a new AllocationPlan, or None to keep the matching's result -
        which happens when the optimizer cannot PROVE optimality within its
        bound. An unproven allocation is never presented as optimal.

        Only course-to-requirement assignment is decided here. Categories are
        assigned afterwards by the Phase 4.2 strategy, which remains the
        authority on category semantics.
        """
        if self._computing_baseline:
            # The baseline audit must not recurse into another baseline.
            return None

        specs: list[RequirementSpec] = []
        for req in requirements:
            needed = self._slots_needed(req)
            if needed <= 0:
                continue
            cats: dict[str, frozenset[str]] = {}
            for candidate in candidates:
                if req.code not in candidate.eligible_requirements:
                    continue
                course_id = candidate.course_key.split(":")[0]
                cats[candidate.course_key] = frozenset(
                    option_categories.get((course_id, req.code), set())
                )
            if not cats:
                continue
            specs.append(
                RequirementSpec(
                    code=req.code,
                    system=req.requirement_system if share else "*",
                    needed_count=needed,
                    needed_categories=req.min_distinct_categories or 0,
                    categories=cats,
                    sort_key=(req.sort_order, req.code),
                )
            )

        if not specs:
            return None

        problem = AllocationProblem(
            requirements=tuple(specs),
            course_keys=tuple(sorted(c.course_key for c in candidates)),
            share_across_systems=share,
            # Natural identities, so the optimizer's tie-break survives a
            # re-ingest that changes surrogate ids.
            course_order={c.course_key: c.sort_key for c in candidates},
        )
        context = ObjectiveContext(
            baseline_satisfied=self._baseline_satisfied(student, version)
        )

        from app.services.audit.optimizer import optimize

        result = optimize(problem, self.objective, context)
        if not result.exact:
            # Documented fallback: keep the matching rather than claim an
            # optimum that was not proven.
            logger.info(
                "global optimizer fell back for student %s (components: %s)",
                redact_id(student.id),
                result.fallback_components,
            )
            return None

        plan = AllocationPlan()
        per_requirement: dict[str, int] = defaultdict(int)
        system_of = {r.code: r.requirement_system for r in requirements}
        for assignment in sorted(
            result.allocation.assignments,
            key=lambda a: (a.requirement_code, a.course_key),
        ):
            index = per_requirement[assignment.requirement_code]
            per_requirement[assignment.requirement_code] += 1
            plan.assign(
                assignment.requirement_code,
                index,
                assignment.course_key,
                system_of.get(assignment.requirement_code, "major"),
            )
        return plan

    def _baseline_satisfied(
        self, student: Student, version: ProgramVersion
    ) -> frozenset[str]:
        """What the student had already EARNED - completed courses only.

        See DATA_MODEL.md section 21. Derived from stored StudentCourse rows
        through the real evaluator, never from a previous optimizer run.

        Evaluated against the SAME version as the audit that asked for it.
        Before Phase 6.0 this re-read `student.program_version_id`, which was
        harmless only because no audit could target any other version.
        """
        from app.services.audit.baseline import EARNED_STATUSES, baseline_from_result

        memo_key = (student.id, version.id)
        if self.baseline_memo is not None and memo_key in self.baseline_memo:
            return self.baseline_memo[memo_key]
        engine = DegreeAuditEngine(
            self.session, category_strategy=self.category_strategy, objective=self.objective
        )
        engine._computing_baseline = True
        satisfied = baseline_from_result(
            engine.audit(student, statuses=EARNED_STATUSES, program_version=version)
        ).satisfied
        if self.baseline_memo is not None:
            self.baseline_memo[memo_key] = satisfied
        return satisfied

    # ------------------------------------------------------------------ #
    # category-aware allocation
    # ------------------------------------------------------------------ #

    @staticmethod
    def _is_category_constrained(req: Requirement) -> bool:
        """The ONLY trigger for category-aware allocation.

        A requirement opts in by declaring how many distinct categories its
        source demands. There is no requirement code anywhere in this path -
        CORE_AH gets this behaviour because of what its data says, and so
        will any future Rutgers requirement shaped the same way.
        """
        return bool(req.min_distinct_categories)

    def _apply_category_strategy(
        self,
        requirements: list[Requirement],
        plan,
        candidates: list[Candidate],
        option_categories: dict[tuple[str, str], set[str]],
        strategy: CategoryAllocationStrategy | None = None,
    ) -> None:
        """Re-choose the courses of each distinct-category requirement.

        The candidate pool is deliberately narrow: the courses this
        requirement ALREADY holds, plus courses eligible for it that no
        requirement in the same system has claimed. Nothing is taken from
        another requirement, so this pass can only improve the category
        coverage of one node and can never unsatisfy another.
        """
        strategy = strategy or DEFAULT_STRATEGY

        for req in sorted(requirements, key=lambda r: (r.sort_order, r.code)):
            if not self._is_category_constrained(req):
                continue

            held = set(plan.courses_for(req.code))
            pool: list[CategoryCandidate] = []
            for candidate in candidates:
                if req.code not in candidate.eligible_requirements:
                    continue
                claimed_systems = plan.systems_for_course(candidate.course_key)
                if candidate.course_key not in held and (
                    req.requirement_system in claimed_systems
                ):
                    # Held by a different requirement in this system.
                    continue
                course_id = candidate.course_key.split(":")[0]
                pool.append(
                    CategoryCandidate(
                        course_key=candidate.course_key,
                        sort_key=candidate.sort_key,
                        categories=frozenset(
                            option_categories.get((course_id, req.code), set())
                        ),
                    )
                )

            if not pool:
                continue

            request = CategoryRequest(
                requirement_code=req.code,
                needed_count=req.min_count or 0,
                needed_categories=req.min_distinct_categories or 0,
                candidates=tuple(pool),
            )
            chosen = strategy.select(request)

            # Only rewrite when the strategy actually improves or preserves
            # what is there; an empty result must never wipe an allocation.
            if not chosen.assignments:
                continue

            plan.release(req.code)
            for index, (course_key, category) in enumerate(chosen.assignments):
                plan.assign(
                    req.code, index, course_key, req.requirement_system, category
                )

    # ------------------------------------------------------------------ #
    # sequence categories (Phase 6.4)
    # ------------------------------------------------------------------ #

    @staticmethod
    def _apply_sequence_categories(
        requirements: list[Requirement],
        plan,
        candidates: list[Candidate],
        course_by_key: dict,
    ) -> None:
        """Let a COMPLETED sequence cover its category.

        The matching fills slots without knowing that "411-412" covers a
        category only as a pair, so it may hold one course of the pair and
        leave the other unused. For each uncovered category with a sequence
        the student holds partly, the missing members - if eligible here and
        unclaimed in this system - replace held courses that cover nothing
        (no selected category, no complete sequence). Local and deterministic,
        like the category pass: no other requirement loses a course.
        """
        for req in sorted(requirements, key=lambda r: (r.sort_order, r.code)):
            if not req.category_sequences:
                continue
            key_of: dict[str, str] = {}
            for c in candidates:
                if req.code in c.eligible_requirements and c.course_key in course_by_key:
                    key_of.setdefault(course_by_key[c.course_key]["ref"].course_string,
                                      c.course_key)
            for category, sequences in sorted(req.category_sequences.items()):
                held_strings = {course_by_key[k]["ref"].course_string
                                for k in plan.courses_for(req.code) if k in course_by_key}
                if any(set(seq) <= held_strings for seq in sequences):
                    continue
                if category in set(plan.categories_for(req.code)):
                    continue
                for seq in sequences:
                    missing = [c for c in seq if c not in held_strings]
                    if not missing or not all(c in key_of for c in seq):
                        continue
                    free = [key_of[c] for c in missing
                            if req.requirement_system not in plan.systems_for_course(key_of[c])]
                    if len(free) != len(missing):
                        continue
                    protected = {key_of[c] for s2 in req.category_sequences.values()
                                 for q in s2 for c in q if c in key_of}
                    spare = sorted(
                        (slot for slot, k in plan.by_slot.items()
                         if slot[0] == req.code and not plan.slot_category.get(slot)
                         and k not in protected),
                        key=lambda slot: plan.by_slot[slot], reverse=True)
                    if len(spare) < len(free):
                        continue
                    for slot, course_key in zip(spare, free):
                        plan.release_slot(slot)
                        plan.assign(req.code, slot[1], course_key, req.requirement_system)
                    break

    # ------------------------------------------------------------------ #
    # grade quotas (Phase 6.4)
    # ------------------------------------------------------------------ #

    @staticmethod
    def _apply_grade_quotas(
        requirements: list[Requirement],
        plan,
        candidates: list[Candidate],
        course_by_key: dict,
        grade_evaluations: list[GradeEvaluation],
    ) -> dict:
        """Hold each quota node to "at most N courses at or below grade G".

        Deterministic LOCAL repair first: while a node holds more low-graded
        courses than its quota allows, swap one (that carries no selected
        category) for an eligible course not claimed in the same system whose
        grade is known to be above G. Nothing is taken from another
        requirement, so the repair can only help this node.

        Then the verdict, three-valued: known low grades over the quota are a
        violation; grades that cannot be compared (P vs "C", temporary
        grades) that COULD exceed it make the node provisional.

        One course is one allocation identity, so a retaken course is
        counted once, by the attempt that represents it.
        """
        state: dict = {}
        for req in sorted(requirements, key=lambda r: (r.sort_order, r.code)):
            if req.grade_quota_max_count is None:
                continue
            limit, ceiling = req.grade_quota_max_count, req.grade_quota_at_most

            def low(key, ceiling=ceiling):
                return at_most(course_by_key[key]["outcome"], ceiling)

            held = [k for k in plan.courses_for(req.code) if k in course_by_key]
            bad = sorted(k for k in held if low(k) is Tri.YES)
            if len(bad) > limit:
                free = sorted(
                    c.course_key for c in candidates
                    if req.code in c.eligible_requirements
                    and c.course_key not in held
                    and req.requirement_system not in plan.systems_for_course(c.course_key)
                    and low(c.course_key) is Tri.NO
                )
                slot_of = {plan.by_slot[k]: k for k in plan.by_slot if k[0] == req.code}
                for course_key in list(reversed(bad)):
                    if len(bad) <= limit or not free:
                        break
                    slot = slot_of.get(course_key)
                    if slot is None or plan.slot_category.get(slot):
                        continue
                    replacement = free.pop(0)
                    plan.release_slot(slot)
                    plan.assign(req.code, slot[1], replacement, req.requirement_system)
                    bad.remove(course_key)
                held = [k for k in plan.courses_for(req.code) if k in course_by_key]

            known = sorted(k for k in held if low(k) is Tri.YES)
            unknown = sorted(k for k in held if low(k) is Tri.UNKNOWN)
            violation = None
            if len(known) > limit:
                violation = (f"at most {limit} course(s) with a grade of {ceiling} or lower may "
                             f"count (found {len(known)})")
            for key in held:
                verdict = low(key)
                entry = course_by_key[key]
                if verdict is Tri.YES:
                    result = "unsatisfied" if violation else "satisfied"
                    reason = f"counts toward the quota of {limit}"
                elif verdict is Tri.UNKNOWN:
                    result, reason = "unknown", "grade cannot be compared with the quota grade"
                else:
                    result, reason = "satisfied", "above the quota grade"
                grade_evaluations.append(GradeEvaluation(
                    requirement_code=req.code, course=entry["ref"], kind="grade_quota",
                    required_grade=ceiling, earned_grade=entry.get("grade"),
                    term_code=entry["term_code"], result=result, reason=reason,
                    policy_source=POLICY_SOURCES["grades_and_records"]))
            state[req.code] = {
                "violation": violation,
                "pending": not violation and len(known) + len(unknown) > limit,
                "counted": known, "unknown": unknown, "limit": limit, "ceiling": ceiling,
            }
        return state

    # ------------------------------------------------------------------ #
    # evaluation
    # ------------------------------------------------------------------ #

    def _evaluate(
        self,
        req: Requirement,
        children_by_parent: dict,
        plan,
        course_by_key: dict,
        eligibility: dict[str, set[str]],
        allocations: list[Allocation],
        option_categories: dict[tuple[str, str], set[str]] | None = None,
        min_grade_for: dict[str, str | None] | None = None,
        quota_state: dict | None = None,
    ) -> RequirementResult:
        kids = sorted(
            children_by_parent.get(req.id, []), key=lambda r: (r.sort_order, r.code)
        )
        child_results = [
            self._evaluate(
                k,
                children_by_parent,
                plan,
                course_by_key,
                eligibility,
                allocations,
                option_categories,
                min_grade_for,
                quota_state,
            )
            for k in kids
        ]

        rtype = req.requirement_type
        allocated_keys = plan.courses_for(req.code)
        allocated = [course_by_key[k] for k in allocated_keys if k in course_by_key]
        refs = [entry["ref"] for entry in allocated]
        # Provisional: in progress, a non-final outcome, or (Phase 6.4) a
        # minimum grade this requirement imposes that cannot be compared.
        provisional = any(
            entry["status"] == EnrollmentStatus.IN_PROGRESS.value
            or entry.get("provisional")
            or req.code in entry.get("grade_pending", ())
            for entry in allocated
        )
        required_grade = (min_grade_for or {}).get(req.code)
        quota = (quota_state or {}).get(req.code)
        if quota and quota["pending"]:
            provisional = True

        for entry in allocated:
            shared_systems = plan.systems_for_course(entry["key"])
            also_in = [sys for sys in shared_systems if sys != req.requirement_system]
            reason = (
                f"Course {entry['ref'].course_string} is eligible for "
                f"'{req.name}' and was allocated to it."
            )
            if also_in:
                reason += (
                    f" It also counts toward {', '.join(also_in)} requirements, "
                    "which this program's sharing policy permits."
                )
            allocations.append(
                Allocation(
                    course=entry["ref"],
                    requirement_code=req.code,
                    requirement_name=req.name,
                    requirement_system=req.requirement_system,
                    shared_with_systems=also_in,
                    term_code=entry["term_code"],
                    status=entry["status"],
                    credits_applied=entry["credits"],
                    reason=reason,
                    earned_grade=entry.get("grade"),
                    required_grade=required_grade,
                )
            )

        result = RequirementResult(
            requirement_code=req.code,
            requirement_name=req.name,
            requirement_type=rtype,
            status=RequirementStatus.UNSATISFIED,
            allocated_courses=refs,
            children=child_results,
            reason="",
            source_prose=req.source_prose,
            curation_status=req.curation_status,
        )

        if rtype == RequirementType.ALL_OF.value:
            self._eval_all_of(result, child_results)
        elif rtype == RequirementType.ANY_OF.value:
            self._eval_any_of(result, req, child_results)
        elif rtype == RequirementType.COURSE.value:
            self._eval_course(result, refs, provisional)
        elif rtype == RequirementType.CHOOSE_N.value:
            self._eval_choose_n(
                result,
                req,
                allocated,
                provisional,
                option_categories or {},
                plan.categories_for(req.code),
                quota,
            )
        elif rtype == RequirementType.CREDITS.value:
            self._eval_credits(result, req, allocated, provisional)
        else:
            result.status = RequirementStatus.INDETERMINATE
            result.reason = f"Unknown requirement type {rtype!r}; cannot evaluate."

        return result

    @staticmethod
    def _eval_all_of(result: RequirementResult, children: list[RequirementResult]) -> None:
        if not children:
            result.status = RequirementStatus.INDETERMINATE
            result.reason = "Group has no child requirements defined; cannot evaluate."
            return
        done = [c for c in children if c.status is RequirementStatus.SATISFIED]
        prov = [c for c in children if c.status is RequirementStatus.PROVISIONALLY_SATISFIED]
        indet = [c for c in children if c.status is RequirementStatus.INDETERMINATE]
        result.needed_count = len(children)
        result.satisfied_count = len(done) + len(prov)

        if indet:
            result.status = RequirementStatus.INDETERMINATE
            result.reason = (
                f"{len(indet)} of {len(children)} sub-requirements could not be determined."
            )
        elif len(done) == len(children):
            result.status = RequirementStatus.SATISFIED
            result.reason = f"All {len(children)} sub-requirements are satisfied."
        elif len(done) + len(prov) == len(children):
            result.status = RequirementStatus.PROVISIONALLY_SATISFIED
            result.reason = (
                f"All {len(children)} sub-requirements are satisfied, but "
                f"{len(prov)} rely on in-progress coursework."
            )
        elif done or prov:
            result.status = RequirementStatus.PARTIALLY_SATISFIED
            result.reason = f"{len(done) + len(prov)} of {len(children)} sub-requirements satisfied."
        else:
            result.reason = f"None of the {len(children)} sub-requirements are satisfied yet."

    @staticmethod
    def _eval_any_of(
        result: RequirementResult, req: Requirement, children: list[RequirementResult]
    ) -> None:
        need = req.min_count or 1
        result.needed_count = need
        done = [c for c in children if c.status is RequirementStatus.SATISFIED]
        prov = [c for c in children if c.status is RequirementStatus.PROVISIONALLY_SATISFIED]
        result.satisfied_count = len(done) + len(prov)

        if not children:
            result.status = RequirementStatus.INDETERMINATE
            result.reason = "Alternative group has no options defined; cannot evaluate."
        elif len(done) >= need:
            result.status = RequirementStatus.SATISFIED
            names = ", ".join(c.requirement_name for c in done[:need])
            result.reason = f"Satisfied via {names}."
        elif len(done) + len(prov) >= need:
            result.status = RequirementStatus.PROVISIONALLY_SATISFIED
            result.reason = "Satisfied, but relies on in-progress coursework."
        elif done or prov:
            result.status = RequirementStatus.PARTIALLY_SATISFIED
            result.reason = f"{len(done)+len(prov)} of {need} alternatives satisfied."
        else:
            result.reason = f"None of the {len(children)} alternatives are satisfied."

    @staticmethod
    def _eval_course(result: RequirementResult, refs: list[CourseRef], provisional: bool) -> None:
        result.needed_count = 1
        result.satisfied_count = len(refs)
        if refs and not provisional:
            result.status = RequirementStatus.SATISFIED
            result.reason = f"Completed {refs[0].course_string}."
        elif refs:
            result.status = RequirementStatus.PROVISIONALLY_SATISFIED
            result.reason = f"{refs[0].course_string} is in progress."
        else:
            result.reason = "Required course not completed."

    def _eval_choose_n(
        self,
        result: RequirementResult,
        req: Requirement,
        allocated: list[dict],
        provisional: bool,
        option_categories: dict[tuple[str, str], set[str]],
        chosen_categories: list[str] | None = None,
        quota: dict | None = None,
    ) -> None:
        need = req.min_count or 0
        got = len(allocated)
        result.needed_count = need
        result.satisfied_count = got

        violations = self._constraint_violations(req, allocated)
        if quota and quota["violation"]:
            violations.append(quota["violation"])

        # "meet at least N of these goals" - a SEPARATE condition from the
        # course count. Rutgers SAS Arts and the Humanities requires two
        # courses AND two distinct goals, so both are checked.
        if req.min_distinct_categories:
            # Prefer the categories the ALLOCATION selected. A course
            # certified Xp and Xq counted under exactly one of them, and the
            # audit reports that edge rather than re-deriving a best case.
            selected = chosen_categories or []
            if selected:
                single_covered = set(selected)
            else:
                course_categories = {
                    entry["key"]: frozenset(
                        option_categories.get((entry["ref"].course_id, req.code), set())
                    )
                    for entry in allocated
                }
                course_categories = {k: v for k, v in course_categories.items() if v}
                _, assignment = max_distinct_categories(course_categories)
                single_covered = set(assignment.values())
            # Phase 6.4: a category member may be a SEQUENCE ("411-412"). It
            # covers its category only when EVERY course of it is allocated
            # here. Sequence courses carry no single-course category, so a
            # course is never counted toward two categories.
            held = {entry["ref"].course_string for entry in allocated}
            sequence_covered = {
                category
                for category, sequences in (req.category_sequences or {}).items()
                if any(set(seq) <= held for seq in sequences)
            }
            distinct = len(single_covered | sequence_covered)
            result.distinct_categories = distinct
            result.needed_distinct_categories = req.min_distinct_categories
            if distinct < req.min_distinct_categories:
                violations.append(
                    f"at least {req.min_distinct_categories} distinct categories are "
                    f"required (found {distinct})"
                )

        if violations:
            result.status = RequirementStatus.PARTIALLY_SATISFIED
            result.reason = f"{got} of {need} selected, but: " + "; ".join(violations)
            return

        if got >= need and not provisional:
            result.status = RequirementStatus.SATISFIED
            result.reason = f"{got} of {need} courses completed."
        elif got >= need:
            result.status = RequirementStatus.PROVISIONALLY_SATISFIED
            result.reason = f"{got} of {need} courses, some in progress."
        elif got:
            result.status = RequirementStatus.PARTIALLY_SATISFIED
            result.reason = f"{got} of {need} courses completed."
        else:
            result.reason = f"0 of {need} courses completed."

    @staticmethod
    def _constraint_violations(req: Requirement, allocated: list[dict]) -> list[str]:
        """Check the constraints measured in the real CS elective clause."""
        return _constraint_checks(req, allocated)

    @staticmethod
    def _eval_credits(
        result: RequirementResult, req: Requirement, allocated: list[dict], provisional: bool
    ) -> None:
        need = req.min_credits or Decimal(0)
        # Decimal start value: `sum()` of nothing is the INT 0, which reached
        # clients as the number 0 where every other value of this field is a
        # decimal string, and made Pydantic warn on every serialization of any
        # audit whose credit requirement had no eligible course (SAS Core's
        # CORE_NS, on the real development record). Found in Phase 6.0.
        got = sum(((e["credits"] or Decimal(0)) for e in allocated), Decimal(0))
        result.needed_credits = need
        result.satisfied_credits = got
        if got >= need and not provisional:
            result.status = RequirementStatus.SATISFIED
            result.reason = f"{got} of {need} credits earned."
        elif got >= need:
            result.status = RequirementStatus.PROVISIONALLY_SATISFIED
            result.reason = f"{got} of {need} credits, some in progress."
        elif got:
            result.status = RequirementStatus.PARTIALLY_SATISFIED
            result.reason = f"{got} of {need} credits earned."
        else:
            result.reason = f"0 of {need} credits earned."

    # ------------------------------------------------------------------ #
    # entry point
    # ------------------------------------------------------------------ #

    def audit(
        self,
        student: Student,
        *,
        statuses: frozenset[str] | None = None,
        program_version: ProgramVersion | None = None,
        projected: tuple = (),
    ) -> DegreeAuditResult:
        """Evaluate a student's degree progress.

        `projected` (Phase 6.5) - hypothetical attempts as (StudentCourse,
        Course) pairs that are NOT in the database: transient rows, never
        added to the session, appended to the record for this one evaluation.
        The Planning Engine passes planned courses as IN-PROGRESS attempts, so
        every rule treats them exactly as it treats real in-progress work -
        provisionally, with no grade assumed. Omitted, behaviour is unchanged.

        `statuses` restricts which StudentCourse rows are considered. It
        changes the INPUT, never the rules: passing
        `frozenset({"completed"})` yields the baseline audit defined in
        DATA_MODEL.md section 21. Omitted, behaviour is unchanged.

        `program_version` selects the RULES explicitly (Phase 6.0). Omitted,
        the student's own binding is used, exactly as before. Supplied, the
        same academic record is evaluated against that version - which is how
        a hypothetical "what if I were a Mathematics major?" audit is answered
        WITHOUT writing a different program onto the Student. The engine
        reads the student's record and never modifies it either way.
        """
        if program_version is None:
            version = self.session.get(ProgramVersion, student.program_version_id)
            if version is None:
                raise ValueError(f"student {student.id} has no program version")
        else:
            version = program_version
        program = version.program

        # The catalog-year check guards the student's BINDING: a stored
        # program_version_id that disagrees with the stored catalog_year is a
        # data error. A version chosen explicitly for a hypothesis is not a
        # binding, so the check would only restate the hypothesis as an error.
        # Callers that choose a version (the scenario service) state their own
        # catalog-year assumption instead.
        is_binding = version.id == student.program_version_id

        findings: list[AuditFinding] = []

        # Catalog-year isolation: the student's declared catalog year must be
        # the version being evaluated. A mismatch is reported, never patched.
        if is_binding and student.catalog_year != version.catalog_year:
            findings.append(
                AuditFinding(
                    severity=Severity.BLOCKING,
                    code="catalog_year_mismatch",
                    message=(
                        f"Student catalog year {student.catalog_year} does not match the "
                        f"program version being audited ({version.catalog_year})."
                    ),
                    remediation="Audit against the program version for the student's catalog year.",
                )
            )

        requirements = self._load_requirements(version.id)
        if not requirements:
            return DegreeAuditResult(
                program_name=program.name,
                program_code=program.code,
                degree_type=program.degree_type,
                catalog_year=version.catalog_year,
                status=AuditStatus.INSUFFICIENT_DATA,
                findings=[
                    *findings,
                    AuditFinding(
                        severity=Severity.BLOCKING,
                        code="no_requirements",
                        message="No requirements are defined for this program version.",
                    ),
                ],
                disclaimers=[DISCLAIMER],
            )

        by_code = {r.code: r for r in requirements}
        children_by_parent: dict = defaultdict(list)
        roots = []
        for r in requirements:
            if r.parent_id is None:
                roots.append(r)
            else:
                children_by_parent[r.parent_id].append(r)

        eligibility, option_categories = self._load_eligibility(
            [r.id for r in requirements]
        )

        # Program-level rules are loaded BEFORE candidates, because an
        # exclusion rule changes which credits count toward the degree.
        rules = self._load_rules(version.id)
        excluded = excluded_course_strings(rules)

        # --- build candidates from the student's record ---
        course_by_key: dict[str, dict] = {}
        candidates: list[Candidate] = []
        rule_views: list[StudentCourseView] = []
        excluded_refs: list[CourseRef] = []
        credits_completed = Decimal(0)
        credits_applicable = Decimal(0)
        credits_excluded = Decimal(0)
        credits_in_progress = Decimal(0)

        records = self._load_student_courses(student, statuses)
        if projected:
            records = sorted(
                [*records, *projected],
                key=lambda r: (r[1].course_string, r[1].supplement_code, r[0].term_code))
        attempts_by_course = _attempts_by_course(records)
        credits_by_id = {course.id: course.credits for _, course in records}
        choice_by_course = {cid: degree_credit_attempt(atts)
                            for cid, atts in attempts_by_course.items()}
        representative = {cid: c.attempt.handle for cid, c in choice_by_course.items()
                          if c.attempt is not None}
        reported_repeats: set = set()

        # Phase 6.4: the minimum grade each requirement imposes - its own or
        # the strictest one inherited from an ancestor.
        by_id = {r.id: r for r in requirements}
        min_grade_for: dict[str, str | None] = {}
        for r in requirements:
            path, node = [], r
            while node is not None:
                path.append(node.min_grade)
                node = by_id.get(node.parent_id)
            min_grade_for[r.code] = strictest(path)
        grade_evaluations: list[GradeEvaluation] = []
        effective_eligibility: dict[str, set[str]] = {}

        for sc, course in records:
            ref = CourseRef(
                course_id=str(course.id),
                course_string=course.course_string,
                supplement_code=course.supplement_code,
                title=course.title,
                credits=course.credits,
            )
            credits = sc.credits_earned if sc.credits_earned is not None else course.credits
            is_excluded = course.course_string in excluded

            # Rules see the whole record, including excluded and failed
            # courses - a D in an excluded course is still a D on the
            # transcript, and the grade rule must be able to see it.
            rule_views.append(
                StudentCourseView(
                    ref=ref,
                    course_string=course.course_string,
                    subject_code=course.subject_code,
                    offering_unit_code=course.offering_unit_code,
                    status=sc.status,
                    grade=sc.grade,
                    credits=credits,
                )
            )

            # One course identity, one allocation identity (Phase 6.2). When a
            # course has a representative attempt, every OTHER attempt is kept
            # on the record - the rules above still see it - but it is neither
            # credited nor allocated. Without this, two passing attempts of
            # 01:198:314 filled two CS elective slots and counted 8 credits.
            chosen = representative.get(course.id)
            if chosen is not None and chosen is not sc:
                if course.id not in reported_repeats:
                    reported_repeats.add(course.id)
                    findings.append(
                        AuditFinding(
                            severity=Severity.INFO,
                            code="repeated_course",
                            message=(
                                f"{course.course_string} appears more than once on the "
                                f"record; the {chosen.status.replace('_', ' ')} attempt "
                                f"from term {chosen.term_code} is the one counted. Other "
                                "attempts remain on the record but earn no additional credit."
                            ),
                        )
                    )
                if sc.status in COUNTABLE and earns_credit(
                        outcome(sc.status, sc.grade, sc.credit_origin)) is Tri.NO:
                    findings.append(
                        AuditFinding(
                            severity=Severity.WARNING,
                            code="non_passing_grade",
                            message=(
                                f"{course.course_string} has grade {sc.grade}; it does not "
                                "count toward requirements."
                            ),
                        )
                    )
                continue

            if sc.status == EnrollmentStatus.COMPLETED.value:
                credits_completed += credits or Decimal(0)
                # The distinction this phase exists to make: completed credits
                # are not the same as credits that count toward THIS degree.
                if is_excluded:
                    credits_excluded += credits or Decimal(0)
                else:
                    credits_applicable += credits or Decimal(0)
            elif sc.status == EnrollmentStatus.IN_PROGRESS.value:
                credits_in_progress += credits or Decimal(0)

            if is_excluded:
                excluded_refs.append(ref)
                findings.append(
                    AuditFinding(
                        severity=Severity.INFO,
                        code="course_excluded_from_degree",
                        message=(
                            f"{course.course_string} earns no credit toward this program. "
                            "The course remains valid and may count toward another program."
                        ),
                    )
                )
                # Excluded courses cannot be allocated to requirements either.
                continue

            if sc.status not in COUNTABLE:
                continue
            attempt_outcome = outcome(sc.status, sc.grade, sc.credit_origin)
            if earns_credit(attempt_outcome) is Tri.NO:
                findings.append(
                    AuditFinding(
                        severity=Severity.WARNING,
                        code="non_passing_grade",
                        message=(
                            f"{course.course_string} has grade {sc.grade}; it does not count "
                            "toward requirements."
                        ),
                    )
                )
                continue

            key = f"{course.id}:{sc.term_code}"
            choice = choice_by_course.get(course.id)
            provisional = bool(choice and choice.attempt and choice.attempt.handle is sc
                               and choice.provisional)
            if provisional and sc.status == EnrollmentStatus.COMPLETED.value:
                findings.append(
                    AuditFinding(
                        severity=Severity.WARNING,
                        code="grade_outcome_not_final",
                        message=(
                            f"{course.course_string} has grade {sc.grade!r}, which is not a final "
                            "outcome CoursePilot can interpret; it counts only provisionally."
                        ),
                    )
                )

            # Minimum grades decide ELIGIBILITY per requirement, before any
            # allocation: a D in a "C or better" course is not a candidate for
            # that requirement at all, so the allocator can never use it there.
            eligible = set(eligibility.get(str(course.id), set()))
            grade_pending: set[str] = set()
            for code in sorted(eligible):
                minimum = min_grade_for.get(code)
                if not minimum:
                    continue
                check = degree_minimum_grade(attempts_by_course[course.id], minimum)
                used = check.attempt
                grade_evaluations.append(GradeEvaluation(
                    requirement_code=code, course=ref, kind="minimum_grade",
                    required_grade=minimum, earned_grade=used.grade if used else None,
                    term_code=used.term_code if used else None,
                    credit_origin=used.credit_origin if used else "rutgers",
                    result={Tri.YES: "satisfied", Tri.NO: "unsatisfied",
                            Tri.UNKNOWN: "unknown"}[check.status],
                    reason=check.reason, policy_source=POLICY_SOURCES["grades_and_records"]))
                if check.status is Tri.NO:
                    eligible.discard(code)
                elif check.status is Tri.UNKNOWN:
                    grade_pending.add(code)
            effective_eligibility[str(course.id)] = eligible

            course_by_key[key] = {
                "key": key,
                "ref": ref,
                "status": sc.status,
                "term_code": sc.term_code,
                "credits": credits,
                "subject_code": course.subject_code,
                "course_number": course.course_number,
                # Phase 6.4
                "grade": sc.grade,
                "outcome": attempt_outcome,
                "provisional": provisional,
                "grade_pending": grade_pending,
            }
            candidates.append(
                Candidate(
                    course_key=key,
                    sort_key=(course.course_string, course.supplement_code, sc.term_code),
                    eligible_requirements=frozenset(eligible),
                )
            )

        # --- build slots ---
        # option_count drives most-constrained-first ordering: a requirement
        # only one course can satisfy must be filled before a large pool that
        # the same course could also serve.
        options_per_requirement: dict[str, int] = defaultdict(int)
        for course_id, codes in eligibility.items():
            for code in codes:
                options_per_requirement[code] += 1

        # How many of the STUDENT's countable courses are eligible for each
        # requirement. Used to size CREDITS requirements (see _slots_needed).
        student_eligible_per_requirement: dict[str, int] = defaultdict(int)
        for candidate in candidates:
            for code in candidate.eligible_requirements:
                student_eligible_per_requirement[code] += 1

        slots: list[Slot] = []
        for r in requirements:
            needed = self._slots_needed(r, student_eligible_per_requirement.get(r.code, 0))
            for i in range(needed):
                slots.append(
                    Slot(
                        requirement_code=r.code,
                        slot_index=i,
                        sort_key=(r.sort_order, r.code),
                        option_count=options_per_requirement.get(r.code, 0),
                        system=r.requirement_system,
                    )
                )

        # EXCLUSIVE is the default, so a program that has not stated a
        # sharing rule behaves exactly as it did before Phase 3.75.
        share = version.sharing_policy == SharingPolicy.SHARE_ACROSS_SYSTEMS.value

        # --- global allocation (Phase 4.5) ---
        # The matching below maximizes FILLED SLOTS, which is not the same as
        # maximizing COMPLETED REQUIREMENTS (DATA_MODEL.md 17-19). The global
        # optimizer answers "which allocation should be evaluated" under the
        # adopted objective; the evaluator still decides what it MEANS, and
        # the Phase 4.2 category strategy still owns category assignment.
        #
        # The matching remains the FALLBACK: if the optimizer cannot prove
        # optimality within its bound, its result is discarded rather than
        # presented as optimal.
        plan = allocate(slots, candidates, share_across_systems=share)
        optimized = self._optimize_globally(
            requirements, candidates, eligibility, option_categories, share, student,
            version,
        )
        if optimized is not None:
            plan = optimized

        # --- distinct-category requirements, re-chosen AFTER the matching ---
        # The matching fills slots without knowing categories exist, so it can
        # hand a requirement two courses certified for the SAME category and
        # leave a satisfying pair unused. This pass re-chooses that one
        # requirement's courses from what it already holds plus what is still
        # unclaimed in its own system.
        #
        # Deliberately LOCAL: no other requirement's allocation is touched, so
        # a category-constrained requirement can never take a course away from
        # a requirement that has no alternative. Fixing that would need a
        # global objective, which this phase does not implement.
        self._apply_category_strategy(
            requirements, plan, candidates, option_categories, self.category_strategy
        )

        # --- credit requirements, settled AFTER the matching ---
        # Count requirements have already claimed what they need, so a credit
        # requirement takes only from what is left in its own system - and
        # only until its minimum is met.
        by_code_all = {r.code: r for r in requirements}
        for req in sorted(requirements, key=lambda r: (r.sort_order, r.code)):
            if req.requirement_type != RequirementType.CREDITS.value:
                continue
            needed = req.min_credits or Decimal(0)

            # Courses eligible for this requirement that are not already
            # claimed in this requirement's system.
            available: list[tuple[str, Decimal | None, str]] = []
            for key, entry in course_by_key.items():
                if req.code not in effective_eligibility.get(entry["ref"].course_id, set()):
                    continue
                if req.requirement_system in plan.systems_for_course(key):
                    continue
                # The course string is the tie-break: a natural key, so the
                # choice survives a re-ingest that changes surrogate ids.
                available.append(
                    (key, entry["credits"], entry["ref"].course_string)
                )

            for i, course_key in enumerate(
                allocate_credits(req.code, needed, available)
            ):
                plan.assign(req.code, i, course_key, req.requirement_system)

        # --- sequence categories, then grade quotas (Phase 6.4) ---
        self._apply_sequence_categories(requirements, plan, candidates, course_by_key)
        quota_state = self._apply_grade_quotas(
            requirements, plan, candidates, course_by_key, grade_evaluations
        )

        # --- evaluate ---
        allocations: list[Allocation] = []
        results = [
            self._evaluate(
                r,
                children_by_parent,
                plan,
                course_by_key,
                eligibility,
                allocations,
                option_categories,
                min_grade_for,
                quota_state,
            )
            for r in sorted(roots, key=lambda x: (x.sort_order, x.code))
        ]

        unallocated = [
            course_by_key[k]["ref"]
            for k in sorted(course_by_key)
            if k not in plan.allocated_course_keys
        ]

        # --- program-level rules ---
        # Phase 6.4: grade-count rules see the courses applied to the MAJOR
        # (each course once, by its counted attempt); GPA rules see every
        # attempt with its credits.
        major_codes = {r.code for r in requirements if r.requirement_system == "major"}
        applied: dict[str, StudentCourseView] = {}
        for code in sorted(major_codes):
            for key in plan.courses_for(code):
                entry = course_by_key.get(key)
                if entry is None or entry["ref"].course_string in applied:
                    continue
                applied[entry["ref"].course_string] = StudentCourseView(
                    ref=entry["ref"], course_string=entry["ref"].course_string,
                    subject_code=entry["subject_code"], offering_unit_code="",
                    status=entry["status"], grade=entry.get("grade"), credits=entry["credits"])
        credits_of = {}
        for cid, atts in attempts_by_course.items():
            for a in atts:
                earned = a.handle.credits_earned
                credits_of[(a.course_key, a.term_code)] = (
                    earned if earned is not None else credits_by_id.get(cid))
        context = RuleContext(
            applied_to_major=[applied[k] for k in sorted(applied)],
            attempts=[a for atts in attempts_by_course.values() for a in atts],
            credits_of=credits_of,
        )
        rule_results: list[RuleResult] = [evaluate_rule(r, rule_views, context) for r in rules]
        for rr in rule_results:
            if rr.status is RequirementStatus.NOT_EVALUABLE:
                findings.append(
                    AuditFinding(
                        severity=Severity.WARNING,
                        code="rule_not_evaluable",
                        message=f"{rr.rule_name}: {rr.reason}",
                        requirement_code=rr.rule_code,
                        remediation=(
                            "This is an authoritative Rutgers rule. Confirm it with an "
                            "academic advisor - CoursePilot cannot check it."
                        ),
                    )
                )
            elif rr.status is RequirementStatus.UNSATISFIED:
                findings.append(
                    AuditFinding(
                        severity=Severity.BLOCKING,
                        code="program_rule_violated",
                        message=f"{rr.rule_name}: {rr.reason}",
                        requirement_code=rr.rule_code,
                    )
                )

        # --- overall status, derived ---
        def worst(rs: list[RequirementResult]) -> AuditStatus:
            if any(r.status is RequirementStatus.INDETERMINATE for r in rs):
                return AuditStatus.INSUFFICIENT_DATA
            if all(r.status is RequirementStatus.SATISFIED for r in rs):
                return AuditStatus.COMPLETE
            if all(
                r.status
                in (
                    RequirementStatus.SATISFIED,
                    RequirementStatus.PROVISIONALLY_SATISFIED,
                )
                for r in rs
            ):
                return AuditStatus.IN_PROGRESS
            return AuditStatus.INCOMPLETE

        status = worst(results)

        # A violated program rule means the degree is not complete, regardless
        # of how many requirement slots were filled.
        if any(r.status is RequirementStatus.UNSATISFIED for r in rule_results):
            status = AuditStatus.INCOMPLETE

        # An authoritative rule we cannot check outranks a clean requirement
        # tree. Reporting COMPLETE here would be a promise the data does not
        # support - this is the whole point of the INDETERMINATE state.
        if (
            status is AuditStatus.COMPLETE
            and any(r.status is RequirementStatus.NOT_EVALUABLE for r in rule_results)
        ):
            status = AuditStatus.INDETERMINATE

        if any(f.severity is Severity.BLOCKING and f.code == "catalog_year_mismatch" for f in findings):
            status = AuditStatus.INSUFFICIENT_DATA

        # Remaining is measured against APPLICABLE credits, not completed ones.
        # Counting excluded courses toward the total would tell a student they
        # are closer to graduating than they are.
        remaining = None
        if version.total_credits_min is not None:
            remaining = max(Decimal(0), version.total_credits_min - credits_applicable)

        return DegreeAuditResult(
            program_name=program.name,
            program_code=program.code,
            degree_type=program.degree_type,
            catalog_year=version.catalog_year,
            status=status,
            credits_completed=credits_completed,
            credits_applicable_to_degree=credits_applicable,
            credits_excluded=credits_excluded,
            credits_in_progress=credits_in_progress,
            credits_required_min=version.total_credits_min,
            credits_remaining=remaining,
            requirements=results,
            rules=rule_results,
            allocation=allocations,
            sharing_policy=version.sharing_policy,
            findings=findings,
            excluded_courses=excluded_refs,
            grade_evaluations=grade_evaluations,
            unallocated_courses=unallocated,
            disclaimers=[DISCLAIMER],
        )
