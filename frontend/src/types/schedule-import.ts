export type ScheduleImportRow = {
  id: string; term_code: string; course_string: string; section_number: string;
  index_number: string | null; state: string; issues: string[]; excluded: boolean;
  replace_existing: boolean;
  match_status: "EXACT_MATCH" | "MULTIPLE_MATCHES" | "NO_MATCH" | "NEEDS_CONFIRMATION";
  /** Phase 7.4: the pre-selected section's index (from the exact SOC match). */
  resolved_index?: string | null;
  keep_record?: boolean;
  /** What confirming does to the record, shown before it does it. */
  history_effect?: "add_in_progress" | "already_in_progress" | "planned_to_in_progress" | "keep_completed" | "record_says_completed" | null;
  section_effect?: "add" | "already_confirmed" | "replace" | "differs" | null;
  outcome?: { history: string | null; section: string | null };
};
export type DroppedCourse = {
  term_code: string; course_string: string; section_number: string | null;
  index_number: string | null; source: string;
};
export type ScheduleImport = {
  id: string; state: string; version: number; expires_at: string;
  parser_version: string; rows: ScheduleImportRow[]; ready: boolean;
  /** Phase 7.4: confirmed earlier for the term, not on this newer schedule. */
  possibly_dropped?: DroppedCourse[];
};
export type Registration = {
  id: string; term_code: string; course_string: string; section_number: string;
  index_number: string | null; source: string; confirmed_at: string;
  metadata_state: "available" | "unavailable";
  /** Phase 7.4: false when the newest confirmed import of its term does not list it. */
  on_latest_import?: boolean;
  supplement_code: string; instructors: string[];
  modality: {classification: string};
  /** Server-normalized meetings from the EXACT matched SOC section; empty when unavailable. */
  normalized_meetings: import("@/types/schedule").MeetingInterval[];
  meetings: {day: string | null; start: string | null; end: string | null;
    mode: string | null; campus: string | null; building: string | null; room: string | null}[];
};
