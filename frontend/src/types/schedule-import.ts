export type ScheduleImportRow = {
  id: string; term_code: string; course_string: string; section_number: string;
  index_number: string | null; state: string; issues: string[]; excluded: boolean;
  replace_existing: boolean;
  match_status: "EXACT_MATCH" | "MULTIPLE_MATCHES" | "NO_MATCH" | "NEEDS_CONFIRMATION";
};
export type ScheduleImport = {
  id: string; state: string; version: number; expires_at: string;
  parser_version: string; rows: ScheduleImportRow[]; ready: boolean;
};
export type Registration = {
  id: string; term_code: string; course_string: string; section_number: string;
  index_number: string | null; source: string; confirmed_at: string;
  metadata_state: "available" | "unavailable";
  supplement_code: string; instructors: string[];
  modality: {classification: string};
  /** Server-normalized meetings from the EXACT matched SOC section; empty when unavailable. */
  normalized_meetings: import("@/types/schedule").MeetingInterval[];
  meetings: {day: string | null; start: string | null; end: string | null;
    mode: string | null; campus: string | null; building: string | null; room: string | null}[];
};
