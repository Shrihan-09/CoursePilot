/**
 * Mirrors of the Planning Engine response (backend/app/domain/planning.py) and
 * the Phase 7.2 saved-plan / planning-context views (routes/plans.py).
 */

import type { ScenarioAssumption, ScenarioTarget } from "@/types/degree";

export type EligibilityState = "satisfied_by_history" | "conditional_on_plan" | "unsatisfied" | "needs_confirmation";

export type OfferingEvidenceKind =
  | "confirmed_in_term"
  | "not_offered_in_term"
  | "historically_offered_in_season"
  | "offered_in_other_seasons_only"
  | "no_offering_evidence";

export type PlanSeverity = "blocker" | "needs_confirmation" | "warning";

export type PlanReason =
  | "satisfies_requirement"
  | "prerequisites_satisfied_by_history"
  | "prerequisites_conditional_on_plan"
  | "no_prerequisite_published"
  | "required_prerequisite_for"
  | "required_corequisite_for"
  | "unlocks_downstream_course"
  | "confirmed_offering_in_term"
  | "historically_offered_in_season"
  | "rules_from_earlier_term"
  | "selected_by_canonical_tie_break";

export type PlanStatus = "covers_all_requirements" | "partial" | "nothing_to_plan";

export interface PlanningConstraints {
  max_credits_per_term: string | number;
  max_courses_per_term: number;
  max_terms: number;
  include_summer: boolean;
  include_winter: boolean;
}

export interface OfferingEvidence {
  kind: OfferingEvidenceKind;
  target_term: string;
  observed_terms: string[];
  same_season_terms: string[];
  most_recent_term: string | null;
  rules_term: string | null;
  target_term_coverage: string | null;
}

export interface ConditionalDependency {
  course: string;
  term_code: string;
  source: "planned" | "in_progress" | string;
  required_grade: string | null;
}

export interface PrerequisiteEvidence {
  state: EligibilityState;
  rules_term: string | null;
  raw_text: string | null;
  canonical_text: string | null;
  combination: string | null;
  corequisite_text: string | null;
  conditional_on: ConditionalDependency[];
  unknown_reasons: string[];
}

export interface PlannedCourse {
  course: string;
  title: string;
  credits: string;
  term_code: string;
  reasons: PlanReason[];
  requirements: { requirement_code: string; requirement_name: string; requirement_system: string; provisional: boolean }[];
  prerequisite: PrerequisiteEvidence;
  offering: OfferingEvidence;
  corequisite_group: string[];
  unlocks: string[];
  interchangeable_alternatives: number;
}

export interface PlanTerm {
  term_code: string;
  label: string;
  courses: PlannedCourse[];
  credits: string;
}

export interface PlanningIssue {
  severity: PlanSeverity;
  code: string;
  message: string;
  requirement_code: string | null;
  course: string | null;
  details: Record<string, unknown>;
}

export interface RequirementProjection {
  requirement_code: string;
  requirement_name: string;
  status_before: string;
  status_after: string;
}

export interface PlanMetadata {
  planning_engine_version: string;
  audit_engine_version: string;
  start_term: string;
  constraints: PlanningConstraints;
  constraints_source: string;
  academic_fingerprint: string;
  rules_fingerprint: string;
  offering_dataset: string[];
}

export interface PlanResult {
  target: ScenarioTarget;
  status: PlanStatus;
  terms: PlanTerm[];
  issues: PlanningIssue[];
  requirements: RequirementProjection[];
  assumptions: ScenarioAssumption[];
  metadata: PlanMetadata;
}

export type StaleReason =
  | "program_changed"
  | "academic_record_changed"
  | "program_rules_changed"
  | "rutgers_data_changed"
  | "engine_updated"
  | "start_term_passed";

export interface BlockedCourse {
  course: string;
  title: string | null;
  issue_code: "ELIGIBILITY_UNKNOWN" | "NO_PLACEABLE_OFFERING" | string;
  requirement_code: string | null;
  reasons: string[];
  prerequisite_text: string | null;
  prerequisite_term: string | null;
}

export interface SavedPlan {
  id: string;
  created_at: string;
  updated_at: string;
  start_term: string;
  constraints: PlanningConstraints;
  freshness: { state: "current" | "stale"; reasons: StaleReason[] };
  blocked_courses: BlockedCourse[];
  result: PlanResult;
}

export interface LoadedTerm {
  term_code: string;
  label: string;
  coverage: "complete" | "partial" | "unknown" | null;
  retrieved_at: string | null;
}

export interface PlanningContext {
  latest_recorded_term: string | null;
  earliest_start_term: string | null;
  earliest_start_label: string | null;
  loaded_terms: LoadedTerm[];
  default_constraints: PlanningConstraints;
  constraints_source: string;
}
