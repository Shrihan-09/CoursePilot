/**
 * Mirrors of the Planning Engine response (backend/app/domain/planning.py) and
 * the Phase 7.2 saved-plan / planning-context views (routes/plans.py).
 */

import type { ScenarioAssumption, ScenarioTarget } from "@/types/degree";

export type EligibilityState = "satisfied_by_history" | "conditional_on_plan" | "unsatisfied" | "needs_confirmation";

export type OfferingEvidenceKind =
  | "confirmed_in_term"
  | "not_offered_in_term"
  | "historically_offered_in_season"
  | "offered_in_other_seasons_only"
  | "target_term_data_pending"
  | "no_offering_evidence";

/** 7.2.4: may history speak for the target term? (services/term_authority.py) */
export type TermAuthority = "ready" | "published_pending" | "not_published" | "unchecked";

export type PlanSeverity = "blocker" | "needs_confirmation" | "warning";

export type PlanReason =
  | "satisfies_requirement"
  | "prerequisites_satisfied_by_history"
  | "prerequisites_conditional_on_plan"
  | "no_prerequisite_published"
  | "required_prerequisite_for"
  | "required_corequisite_for"
  | "unlocks_downstream_course"
  | "confirmed_offering_in_term"
  | "historically_offered_in_season"
  | "rules_from_earlier_term"
  | "selected_by_canonical_tie_break"
  | "ranked_by_requirement_coverage"
  | "requires_math_placement"
  | "prerequisites_met_by_placement"
  | "prerequisites_need_confirmation";

export type PlanStatus = "covers_all_requirements" | "partial" | "nothing_to_plan";

export type Pace = "balanced" | "lighter" | "finish_earlier";

export interface PlanningConstraints {
  max_credits_per_term: string | number;
  max_courses_per_term: number;
  max_terms: number;
  include_summer: boolean;
  include_winter: boolean;
  /** SOFT: what to aim for - never a minimum (7.2.1). */
  target_credits_per_term?: string | number | null;
  pace?: Pace | null;
  /** Phase 7.3 / Prompt 09: the workload level and the student's own credit range. */
  workload_level?: string | null;
  credit_min?: number | null;
  credit_max?: number | null;
  major_subject_each_term?: boolean;
}

export interface PlanObservation {
  code: "earlier_completion_possible" | "light_term" | string;
  message: string;
  term_code: string | null;
  details: Record<string, unknown>;
}

export interface OfferingEvidence {
  kind: OfferingEvidenceKind;
  target_term: string;
  observed_terms: string[];
  same_season_terms: string[];
  most_recent_term: string | null;
  rules_term: string | null;
  target_term_coverage: string | null;
  target_term_authority?: TermAuthority;
  target_term_checked_at?: string | null;
  /** Phase 7.3: a fact - offered in `same_season_offered` of the last `same_season_loaded` same-season terms. */
  same_season_offered?: number;
  same_season_loaded?: number;
}

export interface ConditionalDependency {
  course: string;
  term_code: string;
  source: "planned" | "in_progress" | string;
  required_grade: string | null;
}

export interface PrerequisiteEvidence {
  state: EligibilityState;
  rules_term: string | null;
  raw_text: string | null;
  canonical_text: string | null;
  combination: string | null;
  corequisite_text: string | null;
  conditional_on: ConditionalDependency[];
  unknown_reasons: string[];
}

export interface PlannedCourse {
  course: string;
  title: string;
  credits: string;
  term_code: string;
  reasons: PlanReason[];
  requirements: { requirement_code: string; requirement_name: string; requirement_system: string; provisional: boolean }[];
  prerequisite: PrerequisiteEvidence;
  offering: OfferingEvidence;
  corequisite_group: string[];
  unlocks: string[];
  interchangeable_alternatives: number;
  /** Phase 7.3: generic topics course; `topics` = this term's section subtitles. */
  is_topics_course?: boolean;
  topics?: string[];
  /** Phase 7.3 workload levels: the numbers behind the weight, and why it's in this term. */
  workload?: WorkloadEvidence | null;
  role?: "anchor" | "multi_requirement" | "major" | "filler" | null;
  role_reason?: string | null;
  /** Phase 7.4: e.g. the requirement's status comes from Degree Navigator, or NEEDS REVIEW. */
  notes?: string[];
  /** Phase 7.6: the structured reason for the recommendation. */
  trace?: RecommendationTrace | null;
}

/** Phase 7.6 (backend app/domain/planning.py RecommendationTrace). */
export interface RecommendationTrace {
  requirements: {
    requirement_code: string;
    requirement_name: string;
    requirement_system: string;
    status_source: "degree_navigator" | "estimated";
    status_rule: string | null;
  }[];
  unlocks_for: string[];
  offering_kind: string;
  offering_term: string;
  prerequisite_state: string;
  prerequisite_rules_term: string | null;
  core_codes: { term_code: string; basis: "planned_term" | "latest_offering"; codes: string[]; goals: Record<string, boolean> } | null;
  restriction: {
    outcome: "unrestricted" | "satisfied" | "unknown" | "not_satisfied" | "sections_not_published";
    sections: number;
    open_sections: number;
    open_to: string[];
    reasons: string[];
    message: string | null;
  } | null;
  pattern: { seasons: string[]; only_season: string | null; next_chance: string | null; message: string | null } | null;
  unlock_chain_depth: number;
  sources: { fact: string; source: string; term_code: string | null; data_source_id: string | null; fetched_at: string | null; detail: string | null }[];
}

/** Phase 7.6: planning toward a major the student has not declared. */
export interface Declaration {
  status: "undeclared";
  program_key: string;
  label: string;
  requirements: {
    code: string;
    text: string;
    sources: string[];
    courses: Record<string, "met" | "in_progress" | "below_minimum" | "not_taken" | "unknown">;
    min_grade: string | null;
    status: "met" | "not_met" | "in_progress" | "unknown" | "not_evaluated";
  }[];
  sources: { id: string; title: string; url: string; kind: string; retrieved_at: string }[];
  conflicts: string[];
  message: string | null;
}

export interface WorkloadEvidence {
  weight: string;
  credits: string;
  contact_hours: string | null;
  has_lab: boolean;
  level: number;
  technical: string | null;
  components: Record<string, string>;
  delivery: ("async" | "online" | "in_person")[];
  basis_term: string | null;
}

export interface TermWorkload {
  level: string;
  budget: string;
  total: string;
  credit_floor: string;
  credit_max: string;
  contact_hours: string;
  notes: string[];
  /** Task 2: the budget before commitments, and the commitments' weekly hours. */
  base_budget?: string | null;
  commitment_hours?: number;
  /** Task 2: courses counting toward the major (a lab with its lecture counts once). */
  major_related?: number;
  major_min?: number;
  major_max?: number;
  major_range_source?: "level" | "override";
  /** Task 2: "Why this fits you: ..." */
  fit?: string | null;
  shortfalls?: string[];
}

export interface PlanTerm {
  term_code: string;
  label: string;
  courses: PlannedCourse[];
  credits: string;
  workload?: TermWorkload | null;
  /** Credits of free electives (courses the student chooses) toward the degree total - never a course. */
  free_elective_credits?: string;
  /** Degree credits after this term: record + everything planned so far. */
  cumulative_credits?: string | null;
}

/** The school's cited degree credit total and where the plan leaves it. */
export interface DegreeCredits {
  required: string;
  completed: string;
  in_progress: string;
  planned_courses: string;
  planned_free_electives: string;
  total: string;
  met: boolean;
  source_title: string;
  source_url: string;
  source_quote: string;
  retrieved_at: string;
}

export interface PlanningIssue {
  severity: PlanSeverity;
  code: string;
  message: string;
  requirement_code: string | null;
  course: string | null;
  details: Record<string, unknown>;
}

export interface RequirementProjection {
  requirement_code: string;
  requirement_name: string;
  status_before: string;
  status_after: string;
}

export interface PlanMetadata {
  planning_engine_version: string;
  audit_engine_version: string;
  start_term: string;
  constraints: PlanningConstraints;
  constraints_source: string;
  academic_fingerprint: string;
  rules_fingerprint: string;
  offering_dataset: string[];
  /** Task 1: each choose-one requirement's commitment (chosen alternatives). */
  alternative_choices?: { requirement_code: string; status: string; chosen: { code: string; name: string }[] }[];
}

/** Correctness fixes item 3: requirement code -> chosen alternative codes. */
export function planChoices(plan: { result: { metadata: PlanMetadata } } | null | undefined): Record<string, string[]> {
  const out: Record<string, string[]> = {};
  for (const c of plan?.result.metadata.alternative_choices ?? []) {
    if (c.status !== "undecided" && c.chosen.length) out[c.requirement_code] = c.chosen.map((a) => a.code);
  }
  return out;
}

export interface PlanResult {
  target: ScenarioTarget;
  status: PlanStatus;
  terms: PlanTerm[];
  issues: PlanningIssue[];
  requirements: RequirementProjection[];
  assumptions: ScenarioAssumption[];
  observations: PlanObservation[];
  metadata: PlanMetadata;
  /** Phase 7.3: the stated math placement the plan relied on. */
  placement?: { course: string; title: string | null; source_url: string } | null;
  /** Phase 7.3: the last semester the plan may use, and why. */
  graduation_target?: { term_code: string; basis: string; entry_term: string | null; semesters: number | null; record_first_term?: string | null; conflict?: string | null } | null;
  /** Phase 7.6: which school Core applies ("unknown" when not encoded - never SAS by default). */
  school_core?: { school_code: string; status: "encoded" | "unknown"; requirement_sets: string[]; message: string } | null;
  /** Phase 7.6: set when the student is planning toward a major they have not declared. */
  declaration?: Declaration | null;
  /** The degree credit total (null: not modelled for this school/catalog year). */
  degree_credits?: DegreeCredits | null;
}

export type StaleReason =
  | "program_changed"
  | "academic_record_changed"
  | "program_rules_changed"
  | "rutgers_data_changed"
  | "engine_updated"
  | "start_term_passed"
  | "planning_preferences_changed"
  | "offering_assumption_contradicted"
  | "target_term_published";

export interface BlockedCourse {
  course: string;
  title: string | null;
  issue_code: "ELIGIBILITY_UNKNOWN" | "NO_PLACEABLE_OFFERING" | string;
  requirement_code: string | null;
  reasons: string[];
  prerequisite_text: string | null;
  prerequisite_term: string | null;
}

export interface SavedPlan {
  id: string;
  created_at: string;
  updated_at: string;
  start_term: string;
  constraints: PlanningConstraints;
  freshness: { state: "current" | "stale"; reasons: StaleReason[] };
  blocked_courses: BlockedCourse[];
  result: PlanResult;
  /** Prompt 11: a preview applies only to this revision. */
  revision?: number;
  edits?: PlanEdits;
  course_states?: PlannedCourseState[];
  needs_reselection?: Reselection[];
  /** What "Undo last change" would undo (null: nothing). */
  last_change?: string | null;
}

/* ------------------------------------------------------------ Prompt 11 A */

export type CourseState = "locked" | "user_selected" | "planner_selected" | "proposed_change";

export interface PlannedCourseState {
  course: string;
  term_code: string;
  state: CourseState;
  /** anchor (Musts pin / needed retake) | locked | user_selected | null */
  locked_reason: string | null;
}

export interface CoursePinEdit { course: string; term_code: string; retake?: boolean; source?: "must" | "lock" | "selection" }

export interface PlanEdits {
  locks: CoursePinEdit[];
  selections: CoursePinEdit[];
  avoid: { course: string; term_code: string | null }[];
  locked_terms: string[];
  term_caps: { term_code: string; credit_min: number; credit_max: number }[];
  alternative_selections: Record<string, string[]>;
}

export interface Reselection { course: string; term_code: string; source: string; message: string }

export type ChangeKind =
  | "swap" | "move" | "remove" | "add" | "lock" | "unlock" | "lock_term" | "unlock_term"
  | "lighten_term" | "choose_alternative";

export interface PlanChange {
  kind: ChangeKind;
  course?: string | null;
  term_code?: string | null;
  replacement?: string | null;
  to_term?: string | null;
  requirement_code?: string | null;
  alternatives?: string[];
}

export interface CourseLine { course: string; title: string; credits: string | number; state: CourseState }

export interface TermDiff {
  term_code: string;
  label: string;
  before: CourseLine[];
  after: CourseLine[];
  added: string[];
  removed: string[];
  credits_before: string | number;
  credits_after: string | number;
  workload_before: string | number | null;
  workload_after: string | number | null;
  budget: string | number | null;
}

export interface ForcedMove { course: string; title: string; from_term: string | null; to_term: string | null; reason: string }

export interface PlanDiff {
  terms: TermDiff[];
  forced_moves: ForcedMove[];
  graduation_before: string | null;
  graduation_after: string | null;
  last_term_before: string | null;
  last_term_after: string | null;
  requirements_lost: string[];
  requirements_gained: string[];
  new_issues: string[];
  summary: string;
  big: boolean;
}

export interface PlanPreview {
  preview_id: string;
  base_revision: number;
  change: PlanChange;
  valid: boolean;
  errors: string[];
  diff: PlanDiff | null;
  expires_at: string;
  applied: false;
}

export interface CourseOption {
  course: string;
  title: string;
  credits: string | number;
  requirement_codes: string[];
  requirements: string[];
  offering: "confirmed" | "historically offered";
  offering_term: string;
  workload_weight: string | number | null;
  contact_hours: string | number | null;
  delivery: string[];
  fills_multiple: boolean;
  impact: string;
  similar_to: string | null;
  group: "same_requirement" | "different_requirement";
}

export interface OptionGroup {
  kind: "same_requirement" | "different_requirement";
  requirement_code: string;
  requirement_name: string;
  options: CourseOption[];
}

export interface CourseOptions {
  course: string;
  term_code: string;
  title: string;
  state: CourseState;
  role: string | null;
  locked: boolean;
  locked_reason: string | null;
  requirements: string[];
  groups: OptionGroup[];
  move_terms: string[];
  can_remove: boolean;
  not_available: { course: string; reason: string }[];
}

export interface ChoiceQuestion {
  kind: "slot" | "alternative" | "already_set" | "your_choice";
  term_code: string;
  prompt: string;
  course: string | null;
  title: string | null;
  groups: OptionGroup[];
  requirement_code: string | null;
  alternatives: { code: string; name: string; estimated_terms: number | null }[];
  recommended: string[];
  reason: string | null;
}

export interface Choices { term_code: string | null; questions: ChoiceQuestion[] }

export interface SearchHit {
  course: string;
  title: string;
  credits: string | number;
  requirement_codes: string[];
  requirements: string[];
  offering: string;
  delivery: string[];
  workload_weight: string | number | null;
  contact_hours: string | number | null;
  open_status: "open" | "closed" | "unknown" | "not_published";
  why: string;
  similar_to: string | null;
  planned_term: string | null;
  fills_multiple?: boolean;
}

export interface CourseSearchResult {
  interpretation: {
    term_code: string;
    requirements: string[][];
    requirement_names: string[];
    topic: string | null;
    online: boolean;
    async_only: boolean;
    in_person: boolean;
    campus?: string | null;
    earliest?: string | null;
    latest?: string | null;
    avoid_days: string[];
    level: number | null;
    credits: string | number | null;
    open_only: boolean;
    fills_two: boolean;
    exclude: string[];
    did_you_mean: { phrase: string; options: { requirements: string[]; names: string[] }[] }[];
    notes: string[];
  };
  results: SearchHit[];
  not_available: { course: string; reason: string }[];
}

export interface LoadedTerm {
  term_code: string;
  label: string;
  coverage: "complete" | "partial" | "unknown" | null;
  retrieved_at: string | null;
}

export interface PlanningContext {
  /** "History starts" / "record through": completed or in-progress terms only. */
  earliest_recorded_term: string | null;
  latest_recorded_term: string | null;
  current_term: string;
  current_term_label: string;
  /** The default PLAN STARTING SEMESTER - always a future term. */
  earliest_start_term: string;
  earliest_start_label: string;
  start_options: { term_code: string; label: string }[];
  loaded_terms: LoadedTerm[];
  default_constraints: PlanningConstraints;
  constraints_source: string;
}
