import type { Allocation, RequirementResult } from "./degree";
export interface Contribution {
  units: { numerator: number; denominator: number };
  percent: string | null;
  exact_percent: { numerator: number; denominator: number } | null;
}
export interface ProgressMetric {
  scope: "modeled_overall" | "primary_program" | "school_core";
  label: string;
  availability: "available" | "unavailable";
  denominator_units: number | null;
  denominator_definition: string;
  completed: Contribution;
  projected_current: Contribution;
  projected_plan: Contribution | null;
  remaining: Contribution;
  unknown_obligations: number;
  percentage_kind: "confirmed_lower_bound" | "exact" | "unavailable";
  status: string;
  drill_down: string;
  requirement_codes: string[];
  requirement_results: RequirementResult[];
  allocations: Allocation[];
  explanation: string;
}
export interface ProgressResponse {
  schema_version: string;
  metric_policy_version: string;
  display_percent_decimal_places: number;
  coverage: "partial";
  graduation_eligibility: "not_assessed";
  coverage_limitations: string[];
  projection_state: "available" | "absent" | "stale" | "incomparable";
  metrics: ProgressMetric[];
}
