"""Validator: the last gate before sections reach the database.

Every rule here is grounded in a measurement against the real payload, not in
what sounds reasonable. Two rules that "sound reasonable" are deliberately
absent, and the reasons matter:

  * **end_time > start_time** - three real Rutgers meetings violate it
    (07:966:123 at 1100-1100, and 07:966:333 twice at 2330-1250). Enforcing it
    would reject authentic data. Warned about instead.

  * **enrollment >= 0 / enrolled <= capacity** - SOC provides no enrollment
    data at all, so there is nothing to validate. Writing these rules would
    imply the system has seat counts it does not have.

Rejections are loud and specific: at ~12,000 sections a message of "invalid
section" is useless.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum

from coursepilot_ingestion.section_schemas import NormalizedSection

logger = logging.getLogger(__name__)


class Level(StrEnum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(slots=True)
class SectionIssue:
    level: Level
    index_number: str
    field_name: str
    message: str

    def __str__(self) -> str:
        return f"[{self.level.value}] section {self.index_number} {self.field_name}: {self.message}"


@dataclass(slots=True)
class SectionValidationOutcome:
    valid: list[NormalizedSection] = field(default_factory=list)
    rejected: list[tuple[NormalizedSection, list[SectionIssue]]] = field(default_factory=list)
    warnings: list[SectionIssue] = field(default_factory=list)

    @property
    def error_messages(self) -> list[str]:
        return [str(i) for _, issues in self.rejected for i in issues]

    @property
    def warning_messages(self) -> list[str]:
        return [str(w) for w in self.warnings]


class SectionValidator:
    """Validates normalized sections, individually and as a batch."""

    # Observed: 1 to 5 meeting patterns. More would signal a parsing error
    # rather than an unusual section, but it is a warning because the observed
    # maximum is not a documented Rutgers limit.
    OBSERVED_MAX_MEETINGS = 5
    OBSERVED_MAX_INSTRUCTORS = 2

    def _check_one(self, s: NormalizedSection) -> list[SectionIssue]:
        issues: list[SectionIssue] = []
        idx = s.index_number

        # --- identity ---
        if not s.section_number.strip():
            issues.append(SectionIssue(Level.ERROR, idx, "section_number", "is empty"))

        if not s.term_code.strip():
            issues.append(SectionIssue(Level.ERROR, idx, "term_code", "is empty"))

        # The parent course key must be complete, or the section can never be
        # linked to an offering.
        missing = [
            name
            for name, value in (
                ("offering_unit_code", s.offering_unit_code),
                ("subject_code", s.subject_code),
                ("course_number", s.course_number),
            )
            if not value.strip()
        ]
        if missing:
            issues.append(
                SectionIssue(
                    Level.ERROR,
                    idx,
                    "course_natural_key",
                    f"incomplete parent course identity, missing {missing}",
                )
            )

        if not s.campus_code.strip() or s.campus_code == "UNKNOWN":
            issues.append(
                SectionIssue(Level.WARNING, idx, "campus_code", f"is {s.campus_code!r}")
            )

        # --- meetings ---
        if len(s.meetings) > self.OBSERVED_MAX_MEETINGS:
            issues.append(
                SectionIssue(
                    Level.WARNING,
                    idx,
                    "meetings",
                    f"{len(s.meetings)} patterns exceeds the observed maximum of "
                    f"{self.OBSERVED_MAX_MEETINGS}",
                )
            )

        seen_ordinals = set()
        for m in s.meetings:
            if m.meeting_index in seen_ordinals:
                issues.append(
                    SectionIssue(
                        Level.ERROR,
                        idx,
                        "meetings",
                        f"duplicate meeting_index {m.meeting_index}; ordinals are the natural key",
                    )
                )
            seen_ordinals.add(m.meeting_index)

            if m.crosses_midnight_or_zero_length:
                # Real Rutgers data. Surfaced, never rejected - dropping the
                # section would lose a genuine offering over a source quirk.
                issues.append(
                    SectionIssue(
                        Level.WARNING,
                        idx,
                        "meeting.time",
                        f"end {m.end_time_military} <= start {m.start_time_military} "
                        f"on day {m.meeting_day}; kept as-is (occurs in real SOC data)",
                    )
                )

            if m.meeting_mode_code == "UNKNOWN":
                issues.append(
                    SectionIssue(
                        Level.WARNING, idx, "meeting.mode", "meetingModeCode was absent"
                    )
                )

        # --- instructors ---
        if len(s.instructors) > self.OBSERVED_MAX_INSTRUCTORS:
            issues.append(
                SectionIssue(
                    Level.WARNING,
                    idx,
                    "instructors",
                    f"{len(s.instructors)} exceeds the observed maximum of "
                    f"{self.OBSERVED_MAX_INSTRUCTORS}",
                )
            )

        ordinals = [i.instructor_index for i in s.instructors]
        if len(ordinals) != len(set(ordinals)):
            issues.append(
                SectionIssue(
                    Level.ERROR, idx, "instructors", "duplicate instructor_index ordinals"
                )
            )

        # --- cross-listings ---
        regs = [x.registration_index for x in s.cross_listings]
        if len(regs) != len(set(regs)):
            issues.append(
                SectionIssue(
                    Level.ERROR,
                    idx,
                    "cross_listings",
                    "duplicate registration_index; it is part of the natural key",
                )
            )

        return issues

    def _check_batch(
        self, sections: list[NormalizedSection]
    ) -> list[tuple[int, SectionIssue]]:
        """Cross-record checks, returned as (batch position, issue) pairs.

        The important one is index uniqueness within a term. It was measured
        true (11,992 distinct of 11,992), and the database enforces it - but
        catching a collision here names every offender, whereas the database
        would just raise an IntegrityError on whichever row it reached first.
        """
        issues: list[tuple[int, SectionIssue]] = []

        def flag_collisions(
            groups: dict[tuple, list[tuple[int, NormalizedSection]]],
            field_name: str,
            describe: Callable[[tuple, list[NormalizedSection]], str],
        ) -> None:
            """Emit one ERROR per member of every colliding group.

            Per member, not per group: a duplicate key means we cannot tell
            which record is correct, so every participant must be rejected.
            Flagging only one would let the rest through as if they were fine.
            """
            for key, members in groups.items():
                if len(members) < 2:
                    continue
                message = describe(key, [s for _, s in members])
                for position, section in members:
                    issues.append(
                        (
                            position,
                            SectionIssue(
                                Level.ERROR, section.index_number, field_name, message
                            ),
                        )
                    )

        by_natural_key: dict[tuple, list[tuple[int, NormalizedSection]]] = defaultdict(list)
        by_offering_number: dict[tuple, list[tuple[int, NormalizedSection]]] = defaultdict(list)
        for position, section in enumerate(sections):
            by_natural_key[section.natural_key].append((position, section))
            by_offering_number[(*section.offering_natural_key, section.section_number)].append(
                (position, section)
            )

        flag_collisions(
            by_natural_key,
            "natural_key",
            lambda key, members: (
                f"registration index {key[1]!r} reused within term {key[0]} by "
                + str(
                    sorted(
                        f"{s.offering_unit_code}:{s.subject_code}:{s.course_number}"
                        f"/{s.supplement_code or '-'}@{s.campus_code}"
                        for s in members
                    )
                )
            ),
        )

        # (offering, section_number) was also measured unique. A violation here
        # means our offering-resolution assumption is wrong for that record.
        flag_collisions(
            by_offering_number,
            "section_number",
            lambda key, members: (
                f"section number {key[-1]!r} reused within one offering {key[:4]} "
                f"by indexes {sorted(s.index_number for s in members)}"
            ),
        )

        return issues

    def validate(self, sections: list[NormalizedSection]) -> SectionValidationOutcome:
        # Findings are keyed by POSITION in the batch, never by index_number.
        # Detecting duplicate index_numbers is this validator's own job, so
        # index_number is by definition not a safe identifier here: two
        # colliding sections would share a key and have their findings merged.
        errors_by_position: dict[int, list[SectionIssue]] = defaultdict(list)
        outcome = SectionValidationOutcome()

        for position, section in enumerate(sections):
            for issue in self._check_one(section):
                if issue.level is Level.ERROR:
                    errors_by_position[position].append(issue)
                else:
                    outcome.warnings.append(issue)

        # Run over every section, including ones already rejected above: a
        # record can be both malformed and part of a collision, and the caller
        # deserves to see both reasons rather than whichever ran first.
        for position, issue in self._check_batch(sections):
            if issue.level is Level.ERROR:
                errors_by_position[position].append(issue)
            else:
                outcome.warnings.append(issue)

        for position, section in enumerate(sections):
            errors = errors_by_position.get(position)
            if errors:
                outcome.rejected.append((section, errors))
            else:
                outcome.valid.append(section)

        if outcome.rejected:
            logger.warning("rejected %d section(s) during validation", len(outcome.rejected))
            for msg in outcome.error_messages[:10]:
                logger.warning("  %s", msg)

        return outcome
