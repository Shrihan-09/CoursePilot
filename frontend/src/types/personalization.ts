/**
 * Mirrors of the Phase 7.2.1 Planning Profile, readiness and current-schedule
 * views (backend/app/api/v1/routes/personalization.py).
 *
 * A Planning Profile is what the student WANTS. Nothing here is a fact about
 * their record, and nothing about their record is ever stored here.
 */

import type { Pace } from "@/types/plan";
import type { DayCode, SectionChoice, Modality } from "@/types/schedule";

export interface AdditionalGoal {
  kind: "second_major" | "minor" | "certificate";
  program_key: string | null;
  label: string;
  status: "exploring";
}

/** Task 2: a fixed weekly block - a commitment TYPE only, never a name. */
export type BlockKind = "job" | "club" | "greek_life" | "athletics" | "research" | "recruiting" | "other";
export interface BlockedTime { kind: BlockKind; days: DayCode[]; start: string; end: string }

export interface ScheduleDefaults {
  modality?: { application_state: "not_applied"; preferred: Exclude<Modality, "unknown"> | null; excluded: Exclude<Modality, "unknown">[] };
  earliest_start: string | null;
  latest_end: string | null;
  avoid_days: DayCode[];
  blocked_times?: BlockedTime[];
  /** Task 2: SOC campus codes (BUS, CAC, LIV, D/C, DNB) - orders schedules, never excludes. */
  preferred_campuses?: string[];
}

/** Task 2 planning questionnaire. */
export type GreekLife = "none" | "rushing" | "new_member" | "active";
export type ClubLoad = "none" | "light" | "heavy";
export interface Commitments {
  job_hours_per_week: number;
  greek_life: GreekLife;
  clubs: ClubLoad;
  athletics_hours_per_week: number;
  research_hours_per_week: number;
  recruiting_hours_per_week: number;
}
export const NO_COMMITMENTS: Commitments = {
  job_hours_per_week: 0, greek_life: "none", clubs: "none",
  athletics_hours_per_week: 0, research_hours_per_week: 0, recruiting_hours_per_week: 0,
};
/** One semester's answers; null fields keep the usual answers. */
export interface TermAnswers {
  term_code: string;
  major_courses_min: number | null;
  major_courses_max: number | null;
  credit_min?: number | null;
  credit_max?: number | null;
  commitments: Commitments | null;
  blocked_times: BlockedTime[] | null;
}

/** 7.2.4 academic strategy: ordering among VALID courses only. */
export type Strategy = "balanced" | "major_first" | "requirements_first" | "unlock_future_options";

/** Phase 7.3: per-term intensity and how fillers are chosen. */
export type WorkloadLevel = "light" | "balanced" | "semi_rigorous" | "challenging";
export type FillerPreference = "flexible" | "in_person" | "no_preference";
/** `retake`: the student chose WHEN to retake a course on their record (10a follow-up). */
export interface CoursePin { course: string; term_code: string; retake?: boolean }

export interface PlanningProfileInput {
  pace: Pace;
  strategy?: Strategy;
  target_credits_per_term: string | number;
  max_credits_per_term: string | number;
  max_courses_per_term: number;
  max_terms: number | null;
  include_summer: boolean;
  include_winter: boolean;
  schedule: ScheduleDefaults;
  additional_goals: AdditionalGoal[];
  workload_level?: WorkloadLevel | null;
  filler_preference?: FillerPreference;
  /** Correctness fixes item 5: at least one major-own-subject course each term. */
  major_subject_each_term?: boolean;
  pins?: CoursePin[];
  /** Phase 7.3: overrides the graduation target pace, e.g. "20281". */
  expected_graduation_term?: string | null;
  commitments?: Commitments;
  /** true: in an honors program. false or null (skipped): honors-only courses are not planned. */
  honors?: boolean | null;
  term_overrides?: TermAnswers[];
  /** Task 2: credits per semester (null: the workload level's range). */
  credit_min?: number | null;
  credit_max?: number | null;
  /** Task 2: major-related courses per semester (null: the workload level's range). */
  major_courses_min?: number | null;
  major_courses_max?: number | null;
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
  strategies?: { value: Strategy; label: string; description: string }[];
  workload_levels?: { value: WorkloadLevel; label: string; description: string; credit_floor: string; credit_max: string; budget: string; major_min?: number; major_max?: number }[];
  greek_life?: { value: GreekLife; label: string; hours: number }[];
  clubs?: { value: ClubLoad; label: string; hours: number }[];
  commitments_text?: string;
  major_related_text?: string;
  filler_preferences?: { value: FillerPreference; label: string; description: string }[];
  workload_weight?: string;
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
  confirmed_identities?: {term_code: string; course_string: string; section_number: string | null; index_number: string; supplement_code: string; source: string; confirmed_at: string}[];
  /** Phase 7.4: a newer confirmed WebReg import of this term does not list it. */
  not_on_latest_webreg?: boolean;
  /** Prompt 11 A3: the student's own "doing well" mark (orders their suggestions only). */
  doing_well?: boolean;
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
