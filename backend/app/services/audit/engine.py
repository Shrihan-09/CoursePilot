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

from app.domain.audit import (
    Allocation,
    AuditFinding,
    AuditStatus,
    CourseRef,
    DegreeAuditResult,
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
    Candidate,
    Slot,
    allocate,
    allocate_credits,
    max_distinct_categories,
)
from app.services.audit.categories import (
    DEFAULT_STRATEGY,
    CategoryAllocationStrategy,
    CategoryCandidate,
    CategoryRequest,
)
from app.services.audit.rules import (
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

# Grades that do not earn credit toward a requirement.
FAILING_GRADES = {"F", "D-", "NC", "W"}


class DegreeAuditEngine:
    """Evaluates one student against one program version."""

    def __init__(
        self,
        session: Session,
        category_strategy: CategoryAllocationStrategy | None = None,
    ) -> None:
        self.session = session
        # Internal seam, not a user-facing setting: it exists so the two
        # category strategies can be compared on identical real input.
        # Production always uses DEFAULT_STRATEGY.
        self.category_strategy = category_strategy or DEFAULT_STRATEGY

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
            )
            for k in kids
        ]

        rtype = req.requirement_type
        allocated_keys = plan.courses_for(req.code)
        allocated = [course_by_key[k] for k in allocated_keys if k in course_by_key]
        refs = [entry["ref"] for entry in allocated]
        provisional = any(
            entry["status"] == EnrollmentStatus.IN_PROGRESS.value for entry in allocated
        )

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
    ) -> None:
        need = req.min_count or 0
        got = len(allocated)
        result.needed_count = need
        result.satisfied_count = got

        violations = self._constraint_violations(req, allocated)

        # "meet at least N of these goals" - a SEPARATE condition from the
        # course count. Rutgers SAS Arts and the Humanities requires two
        # courses AND two distinct goals, so both are checked.
        if req.min_distinct_categories:
            # Prefer the categories the ALLOCATION selected. A course
            # certified Xp and Xq counted under exactly one of them, and the
            # audit reports that edge rather than re-deriving a best case.
            selected = chosen_categories or []
            if selected:
                distinct = len(set(selected))
            else:
                course_categories = {
                    entry["key"]: frozenset(
                        option_categories.get((entry["ref"].course_id, req.code), set())
                    )
                    for entry in allocated
                }
                course_categories = {k: v for k, v in course_categories.items() if v}
                distinct, _ = max_distinct_categories(course_categories)
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
        problems: list[str] = []

        if req.max_outside_subject is not None and req.constraint_subject_code:
            outside = [
                e for e in allocated if e["subject_code"] != req.constraint_subject_code
            ]
            if len(outside) > req.max_outside_subject:
                problems.append(
                    f"at most {req.max_outside_subject} may be outside subject "
                    f"{req.constraint_subject_code} (found {len(outside)})"
                )

        if req.min_at_level is not None and req.min_at_level_count is not None:
            at_level = [
                e
                for e in allocated
                if e["subject_code"] == (req.constraint_subject_code or e["subject_code"])
                and e["course_number"].isdigit()
                and int(e["course_number"]) >= req.min_at_level
            ]
            if len(at_level) < req.min_at_level_count:
                problems.append(
                    f"at least {req.min_at_level_count} must be at the {req.min_at_level} "
                    f"level or above (found {len(at_level)})"
                )
        return problems

    @staticmethod
    def _eval_credits(
        result: RequirementResult, req: Requirement, allocated: list[dict], provisional: bool
    ) -> None:
        need = req.min_credits or Decimal(0)
        got = sum((e["credits"] or Decimal(0)) for e in allocated)
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
        self, student: Student, *, statuses: frozenset[str] | None = None
    ) -> DegreeAuditResult:
        """Evaluate a student's degree progress.

        `statuses` restricts which StudentCourse rows are considered. It
        changes the INPUT, never the rules: passing
        `frozenset({"completed"})` yields the baseline audit defined in
        DATA_MODEL.md section 21. Omitted, behaviour is unchanged.
        """
        version = self.session.get(ProgramVersion, student.program_version_id)
        if version is None:
            raise ValueError(f"student {student.id} has no program version")
        program = version.program

        findings: list[AuditFinding] = []

        # Catalog-year isolation: the student's declared catalog year must be
        # the version being evaluated. A mismatch is reported, never patched.
        if student.catalog_year != version.catalog_year:
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

        for sc, course in self._load_student_courses(student, statuses):
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
            if sc.grade in FAILING_GRADES:
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
            eligible = eligibility.get(str(course.id), set())
            course_by_key[key] = {
                "key": key,
                "ref": ref,
                "status": sc.status,
                "term_code": sc.term_code,
                "credits": credits,
                "subject_code": course.subject_code,
                "course_number": course.course_number,
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
        plan = allocate(slots, candidates, share_across_systems=share)

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
                if req.code not in eligibility.get(entry["ref"].course_id, set()):
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
            )
            for r in sorted(roots, key=lambda x: (x.sort_order, x.code))
        ]

        unallocated = [
            course_by_key[k]["ref"]
            for k in sorted(course_by_key)
            if k not in plan.allocated_course_keys
        ]

        # --- program-level rules ---
        rule_results: list[RuleResult] = [evaluate_rule(r, rule_views) for r in rules]
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
            unallocated_courses=unallocated,
            disclaimers=[DISCLAIMER],
        )
