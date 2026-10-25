/**
 * Degree Navigator audit import (Phase 7.4) - the API's shapes, as returned by
 * /api/v1/degree-audit-imports. The page renders these; it decides nothing.
 */

export type DnStatus = "satisfied" | "in_progress_or_planned" | "not_satisfied" | "unknown";
export type PlanningStatus = "satisfied" | "in_progress" | "planned" | "not_satisfied" | "unknown";
export type Reconciliation =
  | "agrees"
  | "dn_satisfied_cp_not"
  | "cp_satisfied_dn_not"
  | "allocation_differs"
  | "unmapped"
  | "unknown";

export interface DnIssue {
  code: string;
  field: string | null;
  severity: "info" | "needs_review" | "conflict" | "invalid";
  message: string;
}

export interface Freshness {
  as_of: string | null;
  as_of_source: string | null;
  age_days: number | null;
  stale: boolean;
  reasons: string[];
  message: string;
}

export interface DnRequirementRow {
  id: string;
  kind: "requirement" | "goal";
  row_index: number;
  parent_row_index: number | null;
  dn_number: number | null;
  dn_heading: string | null;
  dn_code: string | null;
  title: string | null;
  extracted_icon: string | null;
  extracted_total: number | null;
  extracted_completed: number | null;
  extracted_needs: string | null;
  dn_courses: { course: string; term_code: string | null; kind: string | null; icon?: string }[];
  dn_status: DnStatus;
  planning_status: PlanningStatus;
  planning_rule: string;
  requirement_code: string | null;
  mapping_review_status: string | null;
  state: "matched" | "needs_review" | "unmapped";
  issues: DnIssue[];
  excluded: boolean;
  edited: boolean;
  reconciliation: Reconciliation | null;
  cp_status: string | null;
  cp_courses: string[];
  needs_review: boolean;
  label: string | null;
  import_id?: string;
}

export interface DnCourseRow {
  id: string;
  list_kind: string;
  requirement_number: number | null;
  raw_text: string;
  course_code: string | null;
  credits: string | null;
  term: string | null;
  grade: string | null;
  kind: string;
  state: "matched" | "needs_review" | "info";
  issues: DnIssue[];
  excluded: boolean;
  history_row_id: string | null;
}

export interface DnHistoryRow {
  id: string;
  course_code: string | null;
  term_code: string | null;
  status: string | null;
  grade: string | null;
  credits_earned: string | null;
  state: "matched" | "needs_review" | "unrecognized" | "conflict" | "invalid";
  issues: DnIssue[];
  excluded: boolean;
  resolution: "keep_existing" | "replace_existing" | null;
  outcome: string | null;
}

export interface DnImportSummary {
  id: string;
  state: "processing" | "extracted" | "failed" | "confirmed" | "discarded" | "expired";
  version: number;
  block_index: number;
  block_count: number;
  dn_subject: string | null;
  dn_version_label: string | null;
  as_of: string | null;
  as_of_source: string | null;
  freshness: Freshness | null;
  track_check: "matches_track" | "matches_school_core" | "mismatch" | "no_mapping" | null;
  track_message: string | null;
  mapping_key: string | null;
  warnings: string[];
  failure_code: string | null;
  failure_message: string | null;
  created_at: string;
  expires_at: string | null;
  confirmed_at: string | null;
  superseded_at: string | null;
}

export interface DnImportDetail extends DnImportSummary {
  requirements: DnRequirementRow[];
  courses: DnCourseRow[];
  history: DnHistoryRow[];
  history_version: number | null;
  history_outcome: { inserted: number; replaced: number; skipped: number; excluded: number } | null;
  readiness: {
    ready: boolean;
    blocking: number;
    requirement_counts: Record<string, number>;
    course_counts: Record<string, number>;
    history_counts: Record<string, number>;
    track_blocks: boolean;
  };
}

export interface DnProgress {
  source: "degree_navigator" | "coursepilot_estimate";
  label: string;
  imports: DnImportSummary[];
  requirements: DnRequirementRow[];
  nudge: string | null;
  unmapped_label?: string;
  disagreement_label?: string;
}
