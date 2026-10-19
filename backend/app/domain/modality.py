"""Evidence-based delivery modes; these describe sections, never permanent courses."""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MODALITY_CLASSIFIER_VERSION = "1.0.0"


class Modality(StrEnum):
    IN_PERSON = "in_person"
    SYNCHRONOUS_ONLINE = "synchronous_online"
    ASYNCHRONOUS = "asynchronous"
    HYBRID = "hybrid"
    UNKNOWN = "unknown"


class ModalityEvidence(BaseModel):
    classification: Modality = Modality.UNKNOWN
    complete: bool = False
    observed_modes: list[Modality] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    component_indexes: list[str] = Field(default_factory=list)
    classifier_version: str = MODALITY_CLASSIFIER_VERSION


class ModalityPreferences(BaseModel):
    """Validated future scheduling settings. Not applied by the current engines.

    `preferred` is soft. `excluded` is an explicit hard request, which must
    not be silently accepted as enforced until scheduling integration exists.
    """

    model_config = ConfigDict(extra="forbid")
    application_state: Literal["not_applied"] = "not_applied"
    preferred: Modality | None = None
    excluded: list[Modality] = Field(default_factory=list, max_length=4)

    @model_validator(mode="after")
    def _known_nonconflicting(self) -> "ModalityPreferences":
        if self.preferred is Modality.UNKNOWN or Modality.UNKNOWN in self.excluded:
            raise ValueError("Unknown is missing evidence, not a delivery preference.")
        if self.preferred in self.excluded:
            raise ValueError("A preferred modality cannot also be excluded.")
        if len(set(self.excluded)) == 4:
            raise ValueError("At least one known modality must remain allowed.")
        self.excluded = sorted(set(self.excluded))
        return self


class CourseModalityOptions(BaseModel):
    """Term-specific evidence among returned bundles, not exhaustive availability."""
    course: str
    term_code: str
    modes: list[Modality] = Field(default_factory=list)
    unknown_bundle_count: int = 0
    evidence_scope: Literal["returned_schedule_options"] = "returned_schedule_options"
    classifier_version: str = MODALITY_CLASSIFIER_VERSION
