"""Baseline semantics for regression-aware objectives (Phase 4.5, Part A).

## What a baseline is for

Objectives B and C from Phase 4.4 talk about a requirement "regressing".
That is meaningless without a reference point: satisfied compared to WHAT?
This module answers that question, and the answer is derived from the
existing model rather than invented.

## What the model actually provides

Measured by inspection of the schema and engine:

  * `StudentCourse.status` is one of `completed`, `in_progress`, `planned`.
  * `COUNTABLE = {completed, in_progress}`; a planned course "is an
    intention, not evidence" and never enters allocation.
  * **No table stores audit results or requirement assignments.** There are
    17 tables and none of them records an allocation. A course's requirement
    assignment is an INTERPRETATION, recomputed on every audit, never a
    stored fact.

That last point decides most of the question.

## The four candidate baselines, judged against the model

| | Candidate | Verdict |
|---|---|---|
| A | audit over COMPLETED courses only | derivable today from authoritative student state |
| B | the previous optimizer output | would require a new table; and see below |
| C | the previous semester's planned allocation | planned courses are not evidence, and nothing is stored |
| D | some other authoritative state | none exists - there is no registrar-supplied allocation |

**Baseline B is rejected on principle, not only on cost.** An optimizer's
previous output is not an academic fact. Storing it would let an arbitrary
earlier run - including one produced by a version of the allocator since
found to be wrong - acquire the authority to constrain later audits.

So:

    Baseline = the audit computed from COMPLETED courses only,
               taking requirements whose status is SATISFIED.

## Why in-progress work is excluded

The evaluator already distinguishes `SATISFIED` from
`PROVISIONALLY_SATISFIED`, precisely because an in-progress course can still
be failed. A baseline that counted in-progress work would let the audit
promise to protect a completion the student has not earned, and could
"regress" it later through no change in their record. The baseline therefore
contains only what is already earned.

## Why this is computed through the real evaluator

`DegreeAuditEngine.audit(student, statuses={"completed"})` restricts the
INPUT and leaves every rule untouched: grades, exclusions, category
distinctness, sharing policy and group propagation all behave exactly as in a
normal audit. Reimplementing satisfaction here would create a second source
of truth for what "satisfied" means, which is the one thing the audit
architecture has consistently refused to do.

## Scope of "regression"

The evaluator exposes four quantities that could each regress. This module
protects the FIRST only:

  * **requirement satisfaction** - satisfied -> not satisfied. IN SCOPE.
  * partial progress (2/3 -> 1/3) - out of scope; Phase 4.4 showed any
    scalar progress measure embeds a weighting nobody has chosen.
  * category coverage (2 categories -> 1) - out of scope as a separate
    quantity; it already participates through satisfaction, since a
    category-constrained requirement is unsatisfied without its categories.
  * credits - out of scope; credit requirements are not allocated by the
    matching at all (`_slots_needed(CREDITS) == 0`), so this phase has
    nothing to protect there.

Narrowing to satisfaction keeps the definition free of invented semantics
and matches what a student would describe as "it said I was done with this".
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.audit import RequirementResult, RequirementStatus

#: The only statuses that constitute already-earned academic state.
EARNED_STATUSES = frozenset({"completed"})


@dataclass(frozen=True, slots=True)
class Baseline:
    """What the student had already earned, as requirement codes."""

    satisfied: frozenset[str]

    #: Codes satisfied only because of in-progress work, recorded for
    #: explanation but deliberately NOT protected.
    provisional: frozenset[str] = frozenset()

    def __len__(self) -> int:
        return len(self.satisfied)


def _walk(results: list[RequirementResult]):
    for result in results:
        yield result
        yield from _walk(result.children)


def baseline_from_result(result) -> Baseline:
    """Extract the protected set from a completed-courses-only audit.

    Only `SATISFIED` counts. `PROVISIONALLY_SATISFIED` cannot appear in a
    completed-only audit by construction - there is no in-progress work to
    rely on - but it is collected rather than ignored so that a caller
    passing a wider audit gets an honest answer instead of a silent one.
    """
    satisfied = set()
    provisional = set()
    for node in _walk(result.requirements):
        if node.status is RequirementStatus.SATISFIED:
            satisfied.add(node.requirement_code)
        elif node.status is RequirementStatus.PROVISIONALLY_SATISFIED:
            provisional.add(node.requirement_code)
    return Baseline(frozenset(satisfied), frozenset(provisional))


def compute_baseline(session, student) -> Baseline:
    """The student's earned baseline, via the real evaluator.

    Deterministic and reproducible: it depends only on stored StudentCourse
    rows with status `completed`, never on any previous audit run.
    """
    from app.services.audit.engine import DegreeAuditEngine

    result = DegreeAuditEngine(session).audit(student, statuses=EARNED_STATUSES)
    return baseline_from_result(result)


__all__ = [
    "EARNED_STATUSES",
    "Baseline",
    "baseline_from_result",
    "compute_baseline",
]
