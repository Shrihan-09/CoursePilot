"""Evidence for ranking already-valid alternatives; never an eligibility decision."""

from decimal import Decimal

from pydantic import BaseModel, Field


class CourseRankingEvidence(BaseModel):
    required_dependency: bool = False
    downstream_targets: int = Field(default=0, ge=0)
    scarcity: Decimal = Decimal(0)
    confirmed_offering: bool = False
    prerequisites_met_by_history: bool = False
    candidate_requirement_coverage: int = Field(default=0, ge=0)
    # 7.2.4 strategy evidence - facts about the course, not judgements.
    serves_major: bool = False                 # an open primary-major requirement
    serves_school: bool = False                # an open school/Core requirement
    unlock_breadth: int = Field(default=0, ge=0)   # requirement courses citing it as a prerequisite
    policy_version: str = "1.1.0"
