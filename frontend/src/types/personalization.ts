/**
 * Mirrors of the Phase 7.2.1 Planning Profile, readiness and current-schedule
 * views (backend/app/api/v1/routes/personalization.py).
 *
 * A Planning Profile is what the student WANTS. Nothing here is a fact about
 * their record, and nothing about their record is ever stored here.
 */

import type { Pace } from "@/types/plan";
import type { DayCode, SectionChoice } from "@/types/schedule";

export interface AdditionalGoal {
  kind: "second_major" | "minor" | "certificate";
  program_key: string | null;
  label: string;
  status: "exploring";
}

export interface ScheduleDefaults {
  earliest_start: string | null;
  latest_end: string | null;
  avoid_days: DayCode[];
}

export interface PlanningProfileInput {
  pace: Pace;
  target_credits_per_term: string | number;
  max_credits_per_term: string | number;
  max_courses_per_term: number;
  max_terms: number | null;
  include_summer: boolean;
  include_winter: boolean;
  schedule: ScheduleDefaults;
  additional_goals: AdditionalGoal[];
}

export interface PlanningProfile extends PlanningProfileInput {
  version: number;
  confirmed_at: string;
  source: string;
  effective_max_terms: number;
}

export interface PlanningProfileResponse {
  profile: PlanningProfile | null;
  defaults: PlanningProfileInput;
  paces: { value: Pace; label: string; description: string }[];
  disclaimer: string;
}

export type ReadinessState =
  | "needs_program"
  | "needs_academic_history"
  | "needs_planning_profile"
  | "ready_with_warnings"
  | "ready";

export interface Readiness {
  state: ReadinessState;
  checks: { code: string; state: "done" | "missing" | "warning" | "info"; message: string }[];
}

export interface CurrentCourse {
  course: string;
  title: string | null;
  term_code: string;
  transcript_section: string | null;
  confirmed: SectionChoice[];
  unavailable: { index_number: string; component: string }[];
  sections_available: number;
}

export interface CurrentSchedule {
  term_code: string | null;
  term_label: string | null;
  other_in_progress_terms: string[];
  term_published: boolean;
  courses: CurrentCourse[];
}

export interface CourseSections {
  course: string;
  term_code: string;
  term_published: boolean;
  transcript_section: string | null;
  sections: SectionChoice[];
}
