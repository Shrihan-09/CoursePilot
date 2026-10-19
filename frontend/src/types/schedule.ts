/**
 * Mirrors of the Schedule Engine response (backend/app/domain/schedule.py,
 * engine 6.7). Structure (sections, meetings, conflicts) and live
 * availability arrive as SEPARATE fields and are kept separate in the UI.
 */

export type ScheduleStatus =
  | "options_found"
  | "no_valid_schedule"
  | "term_schedule_not_published"
  | "course_eligibility_failed"
  | "search_limit_reached";

export type ScheduleSeverity = "blocker" | "needs_confirmation" | "warning";

export type ScheduleReason =
  | "no_time_conflicts"
  | "all_meeting_times_verified"
  | "all_hard_constraints_satisfied"
  | "all_restrictions_satisfied_or_absent"
  | "all_sections_open_current"
  | "all_sections_open_when_last_observed"
  | "required_components_included"
  | "matches_preferred_campus";

export type Modality = "in_person" | "synchronous_online" | "asynchronous" | "hybrid" | "unknown";
export interface ModalityEvidence {
  classification: Modality;
  complete: boolean;
  observed_modes: Modality[];
  reasons: string[];
  component_indexes: string[];
  classifier_version: string;
}

export type MeetingKind = "timed" | "asynchronous" | "arranged" | "tba" | "malformed";
export type AvailabilityState = "open" | "closed" | "unknown";
export type AvailabilityFreshness = "current" | "stale" | "unobserved";
export type RestrictionOutcome = "unrestricted" | "satisfied" | "not_satisfied" | "unknown";

/** SOC day codes: H = Thursday, U = Sunday. */
export type DayCode = "M" | "T" | "W" | "H" | "F" | "S" | "U";

export interface SchedulePreferences {
  earliest_start: string | null;
  latest_end: string | null;
  avoid_days: DayCode[];
  minimum_minutes_between_classes: number;
  preferred_campuses: string[];
  availability: "prefer_open" | "require_open";
}

export interface MeetingInterval {
  kind: MeetingKind;
  day: DayCode | null;
  start_minute: number | null;
  end_minute: number | null;
  start_date: string | null;
  end_date: string | null;
  mode_code: string | null;
  mode_desc: string | null;
  campus: string | null;
  campus_name: string | null;
  building: string | null;
  room: string | null;
}

export interface RestrictionEvidence {
  outcome: RestrictionOutcome;
  entries: { kind: string; code: string; unit_code: string | null }[];
  matched: { kind: string; code: string; unit_code: string | null } | null;
  open_to_text: string | null;
  eligibility_text: string | null;
  special_permission: string | null;
  reasons: string[];
}

export interface AvailabilityEvidence {
  state: AvailabilityState;
  freshness: AvailabilityFreshness;
  observed_at: string | null;
  source: string | null;
  source_hash: string | null;
  since: string | null;
  reasons: string[];
}

export interface SectionChoice {
  course: string;
  component: "primary" | "required_companion" | string;
  course_string: string;
  supplement_code: string;
  title: string | null;
  section_number: string;
  index_number: string;
  campus_code: string;
  meetings: MeetingInterval[];
  modality?: ModalityEvidence;
  instructors: string[];
  availability: AvailabilityEvidence;
  restriction: RestrictionEvidence;
  cross_listed_indexes: string[];
  time_verified: boolean;
  notes: string | null;
  equivalent_sections: { index_number: string; section_number: string; availability: AvailabilityEvidence; instructors: string[] }[];
  credits: string | null;
  meeting_components: string[];
  component_evidence: string | null;
  meeting_text_in_notes: string | null;
}

export interface ScheduleIssue {
  severity: ScheduleSeverity;
  code: string;
  message: string;
  courses: string[];
  indexes: string[];
  details: Record<string, unknown>;
}

export interface ScheduleScore {
  needs_confirmation: number;
  unknown_time_sections: number;
  preference_misses: number;
  class_days: number;
  gap_minutes: number;
  closed_sections_if_current: number;
  unknown_availability_if_current: number;
}

export interface OptionAvailability {
  state: AvailabilityState;
  freshness: AvailabilityFreshness;
  as_of: string | null;
  all_open_now: boolean;
  courses: { course: string; state: AvailabilityState; freshness: AvailabilityFreshness; as_of: string | null; reasons: string[]; modality?: ModalityEvidence }[];
}

export interface ScheduleOption {
  rank: number;
  choices: SectionChoice[];
  total_credits: string | null;
  score: ScheduleScore;
  reasons: ScheduleReason[];
  issues: ScheduleIssue[];
  days: DayCode[];
  availability: OptionAvailability;
}

export interface ScheduleResult {
  modality_options?: { course: string; term_code: string; modes: Modality[]; unknown_bundle_count: number; evidence_scope: "returned_schedule_options"; classifier_version: string }[];
  term_code: string;
  status: ScheduleStatus;
  requested_courses: string[];
  preferences: SchedulePreferences;
  options: ScheduleOption[];
  issues: ScheduleIssue[];
  relationships: { kind: string; course: string; related: string; evidence: string | null; source: string | null }[];
  metadata: {
    schedule_engine_version: string;
    term_code: string;
    settings_source: string;
    section_dataset: string[];
    availability_freshness: AvailabilityFreshness | null;
    availability_observed_at: string | null;
    availability_source: string | null;
    availability_mode: string;
    availability_current_window_seconds: number | null;
    ranking_objective: string[];
    search: { nodes: number; limit_reached: boolean };
  };
}

export interface ScheduleRequest {
  term: string;
  courses: string[];
  preferences: SchedulePreferences;
  max_results?: number;
}
