/**
 * Mirrors of the Degree Engine and scenario responses (Phase 7.2):
 * backend/app/domain/audit.py (DegreeAuditResult) and domain/scenario.py.
 *
 * Hand-written and literal. Decimals arrive as strings and are displayed, never
 * re-added: a total the frontend computes is a total the engine did not state.
 */

export type RequirementStatus =
  | "satisfied"
  | "provisionally_satisfied"
  | "partially_satisfied"
  | "unsatisfied"
  | "indeterminate"
  | "not_evaluable";

export type AuditStatus = "complete" | "in_progress" | "incomplete" | "indeterminate" | "insufficient_data";

export type FindingSeverity = "blocking" | "warning" | "info";

export interface CourseRef {
  course_id: string;
  course_string: string;
  supplement_code: string;
  title: string | null;
  credits: string | null;
}

export interface Allocation {
  course: CourseRef;
  requirement_code: string;
  requirement_name: string;
  requirement_system: string;
  shared_with_systems: string[];
  term_code: string | null;
  status: string;
  credits_applied: string | null;
  reason: string;
  earned_grade: string | null;
  required_grade: string | null;
}

export interface GradeEvaluation {
  requirement_code: string;
  course: CourseRef;
  kind: string;
  required_grade: string;
  earned_grade: string | null;
  term_code: string | null;
  credit_origin: string;
  result: "satisfied" | "unsatisfied" | "unknown" | string;
  reason: string;
  policy_source: string | null;
}

export interface RequirementResult {
  requirement_code: string;
  requirement_name: string;
  requirement_type: string;
  status: RequirementStatus;
  needed_count: number | null;
  satisfied_count: number;
  needed_credits: string | null;
  satisfied_credits: string;
  needed_distinct_categories: number | null;
  distinct_categories: number;
  allocated_courses: CourseRef[];
  eligible_not_allocated: CourseRef[];
  children: RequirementResult[];
  /** The engine's own sentence explaining the result. Shown as written. */
  reason: string;
  /** The requirement as published by Rutgers (curated source text). */
  source_prose: string | null;
  curation_status: string | null;
}

export interface RuleResult {
  rule_code: string;
  rule_name: string;
  rule_type: string;
  status: RequirementStatus;
  observed_count: number | null;
  allowed_count: number | null;
  affected_courses: CourseRef[];
  reason: string;
  source_prose: string | null;
  curation_status: string | null;
}

export interface AuditFinding {
  severity: FindingSeverity;
  code: string;
  message: string;
  requirement_code: string | null;
  remediation: string | null;
}

export interface DegreeAudit {
  program_name: string;
  program_code: string;
  degree_type: string;
  catalog_year: string;
  status: AuditStatus;
  credits_completed: string;
  credits_applicable_to_degree: string;
  credits_excluded: string;
  credits_in_progress: string;
  credits_required_min: string | null;
  credits_remaining: string | null;
  requirements: RequirementResult[];
  rules: RuleResult[];
  allocation: Allocation[];
  sharing_policy: string;
  findings: AuditFinding[];
  excluded_courses: CourseRef[];
  grade_evaluations: GradeEvaluation[];
  unallocated_courses: CourseRef[];
  disclaimers: string[];
}

/* ------------------------------------------------------------- scenarios */

export interface ScenarioTarget {
  program_key: string;
  program_name: string;
  program_code: string;
  degree_type: string;
  school_code: string;
  catalog_year: string;
  support_status: string;
  curation_status: string;
  is_current_program: boolean;
}

export interface ScenarioAssumption {
  code: string;
  message: string;
}

export interface ScenarioAudit {
  target: ScenarioTarget;
  assumptions: ScenarioAssumption[];
  audit: DegreeAudit;
}

export interface RequirementRef {
  code: string;
  name: string;
  system: string;
}

export interface CourseComparison {
  course_string: string;
  title: string | null;
  current_requirements: RequirementRef[];
  target_requirements: RequirementRef[];
  excluded_by_current: boolean;
  excluded_by_target: boolean;
}

export interface ProgramOutcome {
  program_name: string;
  degree_type: string;
  catalog_year: string;
  status: AuditStatus;
  credits_applicable_to_degree: string;
  credits_excluded: string;
  credits_required_min: string | null;
  credits_remaining: string | null;
  leaf_requirements: number;
  leaf_satisfied: number;
  leaf_provisionally_satisfied: number;
}

export interface ProgramComparison {
  current: ProgramOutcome;
  target: ProgramOutcome;
  courses_applied_in_both: CourseComparison[];
  courses_applied_only_current: CourseComparison[];
  courses_applied_only_target: CourseComparison[];
  courses_applied_in_neither: CourseComparison[];
  requirements_in_both: { code: string; name: string; current_status: RequirementStatus; target_status: RequirementStatus }[];
  requirements_only_current: { code: string; name: string; status: RequirementStatus }[];
  requirements_only_target: { code: string; name: string; status: RequirementStatus }[];
  notes: string[];
}

export interface ScenarioComparison {
  target: ScenarioTarget;
  assumptions: ScenarioAssumption[];
  comparison: ProgramComparison;
}
