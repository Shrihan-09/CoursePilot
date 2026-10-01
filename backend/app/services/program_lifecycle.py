"""Program lifecycle: who may move a program version, and when (Phase 6.3).

```
catalog_page           DISCOVERED  (navigation tree)  ->  FETCHED  (archived snapshot)
program_candidate      PARSED      (a credential heading on a fetched page - a claim)
program_version        parsed -> validated -> reviewed -> published
                                     ^            |          |
                                     └── needs_rereview <────┘   (source or definition changed)
```

| state | meaning | set by |
|---|---|---|
| parsed | a curated definition is loaded | RequirementLoader |
| validated | deterministic checks pass: schema, verbatim quotes, engine load | `mark_validated` |
| reviewed | a NAMED HUMAN approved it against a specific source snapshot | `record_review` |
| published | student-facing support; requires an approved review matching the CURRENT source and definition | `publish` |
| needs_rereview | the source prose or the definition changed after review | `check_source`, `definition_changed` |

## Enforcement

  * This module is the only code that changes `lifecycle_state`. A test
    scans the application for any other assignment.
  * DISCOVERED/PARSED -> PUBLISHED is impossible: `publish` requires
    `reviewed`, and `reviewed` requires an approved `program_review`.
  * Machine validation is not review. `mark_validated` can only reach
    `validated`; nothing an automated check does reaches `reviewed`.
  * A reviewer must be a named human. AI, model, system, bot, pipeline and
    placeholder names are refused - including the assistant that encoded the
    definition. `curated_by` records who ENCODED it; that is never review.
  * A review approves ONE source text and ONE definition (both sha256). A
    publish re-checks both against the version's current values, so neither
    can change between review and publication unnoticed.

## Legacy publication

Versions published before review records existed (the CS B.A.) carry
`publication_basis = 'legacy_curated'`. Their basis is stated, not
disguised as a review, and they are subject to the same source-change rule.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ProgramReview, ProgramVersion

PARSED, VALIDATED, REVIEWED, PUBLISHED, NEEDS_REREVIEW = (
    "parsed", "validated", "reviewed", "published", "needs_rereview")
STATES = (PARSED, VALIDATED, REVIEWED, PUBLISHED, NEEDS_REREVIEW)

#: Labels that identify a machine, a model or nobody - never a reviewer.
_NOT_A_HUMAN = re.compile(
    r"\b(claude|anthropic|openai|gpt|chatgpt|gemini|llm|ai|assistant|model|bot|robot|"
    r"system|automated|automation|auto|script|pipeline|ci|cron|unknown|n/?a|none|"
    r"test|anonymous|coursepilot|validator|validation|parser|checker|linter|extractor)\b",
    re.I,
)


class LifecycleError(ValueError):
    """A transition that the lifecycle forbids."""


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(UTC)


def assert_human_reviewer(reviewer: str | None) -> str:
    name = (reviewer or "").strip()
    if len(name) < 3 or _NOT_A_HUMAN.search(name):
        raise LifecycleError(
            f"{reviewer!r} is not a human reviewer. Review must be recorded by the "
            "named person who checked the definition against the Rutgers source.")
    return name


def mark_validated(version: ProgramVersion, now: datetime | None = None) -> None:
    """Deterministic checks passed. Reaches `validated`, never further."""
    if version.lifecycle_state not in (PARSED, VALIDATED, NEEDS_REREVIEW):
        raise LifecycleError(f"cannot validate a {version.lifecycle_state} version")
    if not version.definition_sha256 or not version.source_prose_sha256:
        raise LifecycleError("validation needs the definition hash and the source prose hash")
    version.lifecycle_state = VALIDATED
    version.validated_at = _now(now)


def record_review(
    session: Session, version: ProgramVersion, *, reviewer: str, decision: str,
    notes: str | None = None, now: datetime | None = None,
) -> ProgramReview:
    """Append a human review. Approval moves validated -> reviewed."""
    name = assert_human_reviewer(reviewer)
    if decision not in ("approved", "changes_requested"):
        raise LifecycleError(f"unknown review decision {decision!r}")
    if version.lifecycle_state != VALIDATED:
        raise LifecycleError(
            f"only a validated version can be reviewed (this one is {version.lifecycle_state})")
    review = ProgramReview(
        program_version_id=version.id, reviewer=name, reviewed_at=_now(now),
        decision=decision, source_prose_sha256=version.source_prose_sha256,
        definition_sha256=version.definition_sha256, notes=notes)
    session.add(review)
    if decision == "approved":
        version.lifecycle_state = REVIEWED
    session.flush()
    return review


def latest_review(session: Session, version: ProgramVersion) -> ProgramReview | None:
    return session.scalar(
        select(ProgramReview).where(ProgramReview.program_version_id == version.id)
        .order_by(ProgramReview.reviewed_at.desc(), ProgramReview.created_at.desc()).limit(1))


def publish(session: Session, version: ProgramVersion) -> None:
    """reviewed -> published, only if the approval covers what exists NOW."""
    if version.lifecycle_state != REVIEWED:
        raise LifecycleError(
            f"only a reviewed version can be published (this one is {version.lifecycle_state})")
    review = latest_review(session, version)
    if review is None or review.decision != "approved":
        raise LifecycleError("no approved human review on record")
    if (review.source_prose_sha256, review.definition_sha256) != (
            version.source_prose_sha256, version.definition_sha256):
        raise LifecycleError("the approved review does not cover the current source and definition")
    version.lifecycle_state = PUBLISHED
    version.publication_basis = "human_review"


def check_source(version: ProgramVersion, current_prose_sha256: str,
                 now: datetime | None = None) -> str:
    """Compare the latest fetched source prose with what this version rests on.

    Returns "unchanged", "changed" or "baseline" (first check of a version
    that had no recorded source hash). A change to a reviewed or published
    version moves it to `needs_rereview` - it is no longer student-facing -
    and the earlier hash stays in its review records as history.
    """
    version.last_checked_at = _now(now)
    if version.source_prose_sha256 is None:
        version.source_prose_sha256 = current_prose_sha256
        return "baseline"
    if version.source_prose_sha256 == current_prose_sha256:
        return "unchanged"
    if version.lifecycle_state in (VALIDATED, REVIEWED, PUBLISHED):
        version.lifecycle_state = NEEDS_REREVIEW
    version.source_prose_sha256 = current_prose_sha256
    return "changed"


def definition_changed(version: ProgramVersion, new_definition_sha256: str) -> bool:
    """A re-encoded definition invalidates validation and review alike."""
    if version.definition_sha256 in (None, new_definition_sha256):
        version.definition_sha256 = new_definition_sha256
        return False
    version.definition_sha256 = new_definition_sha256
    if version.lifecycle_state in (REVIEWED, PUBLISHED):
        version.lifecycle_state = NEEDS_REREVIEW
    elif version.lifecycle_state == VALIDATED:
        version.lifecycle_state = PARSED
    return True


__all__ = ["NEEDS_REREVIEW", "PARSED", "PUBLISHED", "REVIEWED", "STATES", "VALIDATED",
           "LifecycleError", "assert_human_reviewer", "check_source", "definition_changed",
           "latest_review", "mark_validated", "publish", "record_review"]
