"""Validation of one academic-history entry (Phase 7.0).

A history entry is one `StudentCourse` row: a course, a term, a status, and
- depending on those - a grade, earned credits and a credit origin. This
module decides whether a proposed entry is storable, and says WHY not in
structured issues a client can render without parsing prose:

```json
{"code": "GRADE_UNRECOGNIZED", "field": "grade", "severity": "invalid",
 "message": "..."}
```

It is the ONE place both writers - the manual history API and transcript
confirmation - validate against, so a transcript row can never be stored
under looser rules than a typed one.

## What it decides, and what it does not

It enforces storage semantics, mirroring the database CHECKs and adding the
rules the database cannot express:

  * a completed Rutgers course has a grade, and that grade means something
    to `app.domain.grades.outcome` (a symbol it would classify as
    UNRECOGNIZED is refused, never stored to degrade every audit later);
  * planned / in-progress rows carry no grade (there is none yet);
  * transfer and exam credit carry no grade - Rutgers posts no grade for
    them, and storing the other institution's letter in a Rutgers grade
    column would fabricate one (SAS "Academic Credit");
  * transfer and exam credit are completed credit - "planned AP credit" is
    not a thing a record holds;
  * the term is a Rutgers `YYYYT` code (`app.domain.attempts.term_order`),
    because repeat ordering, co-requisite timing and the planner's start
    term all order by it.

It never decides academic meaning - whether a grade satisfies a minimum,
whether a retake replaces an attempt, what a GPA is. Those stay in
`app.domain.grades`, `app.domain.attempts` and the Degree Engine. A
second, route-level opinion about any of them is exactly what this module
exists to prevent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from app.domain.attempts import term_order
from app.domain.grades import OutcomeKind, outcome

STATUSES = ("completed", "in_progress", "planned")
CREDIT_ORIGINS = ("rutgers", "transfer", "exam_credit")
#: StudentCourse.credits_earned is Numeric(4, 1).
MAX_CREDITS = Decimal("999.9")


class IssueSeverity(StrEnum):
    #: The value cannot be stored as it is. Blocks.
    INVALID = "invalid"
    #: Plausible, but a person must decide (never guessed). Blocks until
    #: resolved or the row is excluded.
    NEEDS_REVIEW = "needs_review"
    #: Disagrees with what is already recorded. Blocks until resolved.
    CONFLICT = "conflict"
    #: Worth showing; does not block.
    INFO = "info"


BLOCKING = frozenset({IssueSeverity.INVALID, IssueSeverity.NEEDS_REVIEW,
                      IssueSeverity.CONFLICT})


@dataclass(frozen=True, slots=True)
class Issue:
    code: str
    field: str | None
    severity: IssueSeverity
    message: str

    @property
    def blocking(self) -> bool:
        return self.severity in BLOCKING

    def as_dict(self) -> dict:
        return {"code": self.code, "field": self.field, "severity": self.severity.value,
                "message": self.message}


@dataclass(slots=True)
class Entry:
    """A proposed entry, values already normalized where normalization is
    lossless (case and surrounding space of a grade). Course identity is the
    service's concern - it needs the database."""

    term_code: str | None
    status: str | None
    grade: str | None
    credits_earned: Decimal | None
    credit_origin: str | None
    issues: list[Issue] = field(default_factory=list)

    @property
    def blocking(self) -> bool:
        return any(i.blocking for i in self.issues)


def normalize_grade(grade: str | None) -> str | None:
    """Strip and upper-case; empty is None. The cache fingerprint hashes the
    stored string, so "b+" and "B+" must not be two different records."""
    if grade is None:
        return None
    text = grade.strip().upper()
    return text or None


def parse_credits(value) -> tuple[Decimal | None, Issue | None]:
    """A credit amount, or an issue. None stays None (catalog credits apply)."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None, None
    try:
        amount = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None, Issue("CREDITS_INVALID", "credits_earned", IssueSeverity.INVALID,
                           "Credits must be a number such as 3 or 4.0.")
    if not amount.is_finite() or amount < 0 or amount > MAX_CREDITS:
        return None, Issue("CREDITS_INVALID", "credits_earned", IssueSeverity.INVALID,
                           "Credits must be between 0 and 999.9.")
    if amount != amount.quantize(Decimal("0.1")):
        return None, Issue("CREDITS_INVALID", "credits_earned", IssueSeverity.INVALID,
                           "Credits may have at most one decimal place.")
    return amount.quantize(Decimal("0.1")), None


def validate_entry(*, term_code: str | None, status: str | None, grade: str | None,
                   credits_earned, credit_origin: str | None,
                   missing_severity: IssueSeverity = IssueSeverity.INVALID) -> Entry:
    """Validate one entry. Never raises; every problem is an Issue.

    `missing_severity` is how an ABSENT required value is reported: INVALID
    for a person typing a record (they must supply it), NEEDS_REVIEW for a
    transcript row (the document did not state it, and the student - not
    CoursePilot - must say what it is).
    """
    issues: list[Issue] = []
    grade = normalize_grade(grade)
    origin = (credit_origin or "rutgers").strip().lower()
    term = term_code.strip() if isinstance(term_code, str) else None
    term = term or None
    stat = status.strip().lower() if isinstance(status, str) else None
    stat = stat or None

    credits, credit_issue = parse_credits(credits_earned)
    if credit_issue:
        issues.append(credit_issue)

    if origin not in CREDIT_ORIGINS:
        issues.append(Issue("CREDIT_ORIGIN_INVALID", "credit_origin", IssueSeverity.INVALID,
                            "Credit origin must be rutgers, transfer or exam_credit."))
        origin = None

    if term is None:
        issues.append(Issue("TERM_MISSING", "term_code", missing_severity,
                            "Which term was this course taken (or planned)?"))
    elif term_order(term) is None:
        issues.append(Issue("TERM_INVALID", "term_code", IssueSeverity.INVALID,
                            "A term is a Rutgers term code: YYYY then 0 (winter), "
                            "1 (spring), 7 (summer) or 9 (fall), e.g. 20259."))

    if stat is None:
        issues.append(Issue("STATUS_MISSING", "status", missing_severity,
                            "Is this course completed, in progress, or planned?"))
    elif stat not in STATUSES:
        issues.append(Issue("STATUS_INVALID", "status", IssueSeverity.INVALID,
                            "Status must be completed, in_progress or planned."))
        stat = None

    external = origin in ("transfer", "exam_credit")
    if stat is not None and origin is not None:
        if external:
            if stat != "completed":
                issues.append(Issue(
                    "EXTERNAL_CREDIT_NOT_COMPLETED", "status", IssueSeverity.INVALID,
                    "Transfer and exam credit is recorded as completed credit."))
            if grade is not None:
                # Never stored: Rutgers posts no grade for external credit.
                issues.append(Issue(
                    "GRADE_NOT_ALLOWED", "grade", IssueSeverity.INVALID,
                    "Transfer and exam credit carries no Rutgers grade; leave the grade "
                    "empty."))
        elif stat in ("planned", "in_progress"):
            if grade is not None:
                issues.append(Issue(
                    "GRADE_NOT_ALLOWED", "grade", IssueSeverity.INVALID,
                    "A planned or in-progress course has no grade yet."))
        elif grade is None:
            issues.append(Issue("GRADE_MISSING", "grade", missing_severity,
                                "A completed Rutgers course needs its grade."))
        elif outcome(stat, grade, origin).kind is OutcomeKind.UNRECOGNIZED:
            issues.append(Issue(
                "GRADE_UNRECOGNIZED", "grade",
                IssueSeverity.INVALID if missing_severity is IssueSeverity.INVALID
                else IssueSeverity.NEEDS_REVIEW,
                "Not a Rutgers grade symbol (A, B+, B, C+, C, D, F, P, NC, W, NG, H, S, "
                "U, XF, TZ, or T followed by a letter grade)."))

    return Entry(term_code=term, status=stat, grade=grade, credits_earned=credits,
                 credit_origin=origin, issues=issues)


__all__ = ["BLOCKING", "CREDIT_ORIGINS", "MAX_CREDITS", "STATUSES", "Entry", "Issue",
           "IssueSeverity", "normalize_grade", "parse_credits", "validate_entry"]
