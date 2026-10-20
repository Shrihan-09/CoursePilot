"""Course-change INTENT (Phase 7.2.4): what a student could mean by "take 443 out".

Foundation only. A future assistant may interpret a sentence into one of these
intents; the deterministic services compute what each would do; the planner
performs the change. Nothing here edits a plan, and nothing is ever swapped
silently: every option states its consequences.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class ChangeIntent(StrEnum):
    REPLACE_SAME_REQUIREMENT = "replace_same_requirement"
    MOVE_TO_ANOTHER_TERM = "move_to_another_term"
    REMOVE_AND_LEAVE_REQUIREMENT_OPEN = "remove_and_leave_requirement_open"
    FIND_DIFFERENT_SECTION = "find_different_section"
    CHANGE_MODALITY = "change_modality"
    REDUCE_SEMESTER_LOAD = "reduce_semester_load"
    PRIORITIZE_DIFFERENT_REQUIREMENT = "prioritize_different_requirement"


class ReplacementCandidate(BaseModel):
    course: str
    requirement_code: str
    #: Offering status for the SAME term (domain.planning.OfferingStatus value).
    offering_status: str
    #: Always False here: prerequisites/co-requisites are verified by the
    #: planner when the change is applied, never assumed by this listing.
    eligibility_verified: bool = False


class ChangeOption(BaseModel):
    intent: ChangeIntent
    available: bool
    #: Closed code explaining availability (e.g. no_section_data_for_term).
    reason: str
    candidates: list[str] = Field(default_factory=list)          # term codes, requirement codes
    replacements: list[ReplacementCandidate] = Field(default_factory=list)
    #: Closed codes, e.g. "requirement_left_open:CS_ELECTIVES", "loses_prerequisite_for:01:198:416".
    consequences: list[str] = Field(default_factory=list)


class ChangeOptions(BaseModel):
    course: str
    term_code: str
    requirements: list[str]
    dependents: list[str]
    options: list[ChangeOption]
    #: This is an analysis. Applying a choice is the planner's job.
    applied: bool = False


__all__ = ["ChangeIntent", "ChangeOption", "ChangeOptions", "ReplacementCandidate"]
