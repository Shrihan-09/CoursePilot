"""Tests for the architectural invariants of the domain contracts.

These are guardrail tests. They don't test behavior so much as they test that
the *design* still holds — that nothing has quietly made it possible for a
caller (or a future agent implementation) to declare a plan valid, or to
record a Rutgers fact with no source behind it.

If one of these fails, someone has weakened a load-bearing constraint.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.domain.provenance import SourceKind, SourceRef, Sourced, VerificationStatus
from app.domain.validation import (
    CheckKind,
    CheckStatus,
    Finding,
    Severity,
    ValidationReport,
)


def _source() -> SourceRef:
    return SourceRef(
        source_id="s1",
        kind=SourceKind.RUTGERS_OFFICIAL_CATALOG,
        retrieved_at=datetime.now(UTC),
    )


def test_sourced_value_requires_at_least_one_source() -> None:
    # An unsourced Rutgers fact must be impossible to construct, not merely
    # discouraged by convention.
    with pytest.raises(ValidationError):
        Sourced[str](value="unsourced claim", sources=[])


def test_records_default_to_unverified() -> None:
    # Defaulting to VERIFIED would let unchecked ingested data masquerade as
    # confirmed. The safe default is the distrustful one.
    assert _source().verification is VerificationStatus.UNVERIFIED


def test_blocking_finding_makes_report_invalid() -> None:
    report = ValidationReport(
        findings=[
            Finding(
                kind=CheckKind.PREREQUISITE,
                status=CheckStatus.FAILED,
                severity=Severity.BLOCKING,
                message="Student has not completed the prerequisite.",
            )
        ]
    )
    assert report.is_valid is False


def test_warning_finding_does_not_block() -> None:
    report = ValidationReport(
        findings=[
            Finding(
                kind=CheckKind.CREDIT_LOAD,
                status=CheckStatus.PASSED,
                severity=Severity.WARNING,
                message="This is a heavy credit load.",
            )
        ]
    )
    assert report.is_valid is True


def test_is_valid_cannot_be_assigned() -> None:
    # The core invariant: validity is derived from findings. If this ever
    # becomes settable, an LLM-authored response could set it directly.
    report = ValidationReport()
    with pytest.raises((AttributeError, ValueError)):
        report.is_valid = True  # type: ignore[misc]


def test_skipped_checks_flag_unverifiable_claims() -> None:
    # A report with nothing blocking is "valid", but if a check could not run
    # we must still be able to tell the student we did not fully verify it.
    # Valid-but-unverified is a real and common state; it must be visible.
    report = ValidationReport(checks_skipped=[CheckKind.OFFERING_AVAILABLE])
    assert report.is_valid is True
    assert report.has_unverifiable_claims is True


def test_indeterminate_finding_flags_unverifiable_claims() -> None:
    report = ValidationReport(
        findings=[
            Finding(
                kind=CheckKind.PREREQUISITE,
                status=CheckStatus.INDETERMINATE,
                severity=Severity.WARNING,
                message="Prerequisite text could not be parsed.",
            )
        ]
    )
    assert report.has_unverifiable_claims is True


def test_clean_report_is_valid_and_fully_verified() -> None:
    report = ValidationReport(checks_run=[CheckKind.COURSE_EXISTS])
    assert report.is_valid is True
    assert report.has_unverifiable_claims is False
