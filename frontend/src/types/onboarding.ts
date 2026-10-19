/**
 * Mirrors of the Phase 7.0/7.1 API response models (backend/app/api/v1/routes/
 * onboarding.py, account.py, programs.py). Hand-written, so kept literal: a
 * field the frontend believes in but the API does not send is how wrong
 * academic statements get rendered.
 */

export interface Issue {
  code: string;
  field: string | null;
  severity: "invalid" | "needs_review" | "conflict" | "info";
  message: string;
}

export interface ApiErrorBody {
  detail: string;
  error: { code: string; message: string; request_id: string | null; issues?: Issue[] };
}

export interface Me {
  sign_in_method: "oidc" | "dev";
  institution: { status: "not_configured" | "verified" | "not_verified" };
  has_academic_profile: boolean;
  onboarding_complete: boolean;
}

export interface ProgramVersion {
  catalog_year: string;
  support_status: "supported" | "pending_review" | "unavailable";
  lifecycle_state: string;
  publication_basis: string | null;
  requirement_count: number;
  not_evaluable_rule_count: number;
  total_credits_min: string | null;
}

export interface Program {
  program_key: string;
  school_code: string;
  school_name: string;
  program_code: string;
  name: string;
  degree_type: string;
  variant: string;
  versions: ProgramVersion[];
}

export interface Warning {
  code: string;
  severity: "warning" | "info";
  message: string;
}

export interface ProgramView {
  program_key: string;
  program_name: string;
  program_code: string;
  school_code: string;
  degree_type: string;
  variant: string;
  catalog_year: string;
  lifecycle_state: string;
  publication_basis: string | null;
  support_status: "supported" | "pending_review";
  warnings: Warning[];
}

export interface Profile {
  program: ProgramView;
  onboarding_completed_at: string | null;
  transcript_reported_gpa: { value: string; source: string; as_of: string | null } | null;
}

export type RowState = "matched" | "needs_review" | "unrecognized" | "conflict" | "invalid";

export interface ImportRow {
  row_id: string;
  row_index: number;
  page_number: number;
  line_number: number;
  section: string;
  extracted: {
    raw_text: string;
    course_code: string | null;
    title: string | null;
    term: string | null;
    section: string | null;
    supplement: string | null;
    credits: string | null;
    credit_prefix: string | null;
    grade: string | null;
  };
  normalized: {
    course_code: string | null;
    supplement_code: string | null;
    course_title: string | null;
    term_code: string | null;
    status: string | null;
    grade: string | null;
    credits_earned: string | null;
    credit_origin: string | null;
  };
  state: RowState;
  issues: Issue[];
  excluded: boolean;
  edited: boolean;
  resolution: "keep_existing" | "replace_existing" | null;
  conflicts_with_entry_id: string | null;
  outcome: string | null;
  entry_id: string | null;
}

export interface TermSummary {
  term_code: string;
  term_label: string;
  credits_attempted: string | null;
  rows_credit_sum: string | null;
  consistent: boolean | null;
  term_average: string | null;
  cumulative_average: string | null;
}

export interface ImportSummary {
  import_id: string;
  state: "processing" | "extracted" | "failed" | "confirmed" | "discarded" | "expired";
  failure_code: string | null;
  created_at: string;
  expires_at: string | null;
  confirmed_at: string | null;
  page_count: number | null;
  parser_version: string | null;
  layout_verified: boolean;
  version: number;
  transcript_reported_gpa: string | null;
  warnings: string[];
  ready: boolean;
  blocking_rows: number;
  row_counts: Record<string, number>;
  rows_inserted: number;
  rows_replaced: number;
  rows_skipped: number;
  rows_excluded: number;
}

export interface ImportDetail extends ImportSummary {
  terms: TermSummary[];
  rows: ImportRow[];
}

export interface Onboarding {
  state:
    | "needs_profile"
    | "needs_academic_history"
    | "reviewing_transcript"
    | "ready_to_complete"
    | "complete";
  profile: Profile | null;
  history_counts: Record<string, number>;
  open_imports: ImportSummary[];
  next_steps: string[];
}

export interface Entry {
  entry_id: string;
  course_code: string;
  supplement_code: string;
  title: string | null;
  term_code: string;
  status: "completed" | "in_progress" | "planned";
  grade: string | null;
  credits_earned: string | null;
  catalog_credits: string | null;
  credit_origin: "rutgers" | "transfer" | "exam_credit";
  source_kind: string;
  from_transcript_import: boolean;
}
