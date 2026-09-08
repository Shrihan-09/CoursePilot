/**
 * Hand-written mirrors of the backend Pydantic models.
 *
 * These are temporary. Once the API surface stabilizes, generate them from
 * the FastAPI OpenAPI schema (`openapi-typescript`) so the two cannot drift —
 * a frontend that believes a stale contract is exactly how a "valid" badge
 * ends up rendered over an invalid plan. Tracked in docs/ARCHITECTURE.md.
 */

export type CheckStatus = "passed" | "failed" | "indeterminate" | "not_applicable";
export type Severity = "blocking" | "warning" | "info";
export type PlanStatus = "valid" | "invalid" | "needs_revision" | "insufficient_data";

export interface SourceRef {
  source_id: string;
  kind: string;
  url: string | null;
  retrieved_at: string;
  academic_year: string | null;
  term_code: string | null;
  verification: "verified" | "unverified" | "stale" | "conflicted" | "deprecated";
}

export interface Finding {
  kind: string;
  status: CheckStatus;
  severity: Severity;
  message: string;
  subject_ref: string | null;
  sources: SourceRef[];
  remediation: string | null;
}

export interface ValidationReport {
  findings: Finding[];
  checks_run: string[];
  checks_skipped: string[];
}

export interface Capabilities {
  course_search: boolean;
  degree_audit: boolean;
  semester_planning: boolean;
  schedule_generation: boolean;
  registration_likelihood: boolean;
  calendar_sync: boolean;
  discussions: boolean;
}

export interface MetaResponse {
  service: string;
  version: string;
  environment: string;
  llm_provider: string;
  retrieval_strategy: string;
  capabilities: Capabilities;
  loaded_terms: string[];
}
