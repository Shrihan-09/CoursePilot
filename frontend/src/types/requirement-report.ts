/** Prompt 08 B1: "Report a wrong requirement" (POST /requirements/reports). */
export type ReportStatus = "open" | "resolved" | "dismissed";

export interface RequirementReportInput {
  program_key: string;
  catalog_year: string;
  requirement_code: string;
  message: string;
}

export interface RequirementReport {
  id: string;
  program_key: string;
  catalog_year: string;
  requirement_code: string;
  requirement_name: string;
  message: string;
  status: ReportStatus;
  resolved_note: string | null;
  created_at: string;
  /** A redacted account reference, for grouping only. */
  reporter: string;
}
