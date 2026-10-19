/**
 * The student-facing words for the engines' states (Phase 7.2).
 *
 * This module TRANSLATES; it never decides. Every function takes a value an
 * engine produced and returns how to say it - a label, an icon, a tone and a
 * plain sentence. Nothing here computes whether a requirement is met, a
 * course is eligible, or a schedule is valid, and no function maps one
 * engine state onto another.
 *
 * Two rules every mapping keeps (tests/unit/vocabulary.test.ts):
 *
 *   1. UNKNOWN is not FAILED. "CoursePilot cannot determine this" is a
 *      statement about CoursePilot's data; "you do not meet this" is a
 *      statement about the student. They never share a label, an icon or
 *      the danger tone.
 *   2. Never colour alone: every state has an icon and words.
 */

import type { AuditStatus, FindingSeverity, RequirementStatus } from "@/types/degree";
import type {
  EligibilityState,
  OfferingEvidenceKind,
  PlanReason,
  PlannedCourse,
  PlanSeverity,
  PlanStatus,
  StaleReason,
} from "@/types/plan";
import type {
  AvailabilityEvidence,
  RestrictionOutcome,
  ScheduleReason,
  ScheduleSeverity,
  ScheduleStatus,
} from "@/types/schedule";

export type Tone = "neutral" | "info" | "success" | "warning" | "danger";

/** Icon keys, resolved to components in components/academic/status.tsx. */
export type IconKey = "complete" | "progress" | "partial" | "remaining" | "unknown" | "failed" | "info" | "attention";

export interface Presentation {
  label: string;
  tone: Tone;
  icon: IconKey;
  /** One plain sentence: what this state means for the student. */
  meaning: string;
}

/* --------------------------------------------------------------- degree */

export const REQUIREMENT_STATUS: Record<RequirementStatus, Presentation> = {
  satisfied: {
    label: "Complete", tone: "success", icon: "complete",
    meaning: "Your completed courses meet this requirement.",
  },
  provisionally_satisfied: {
    label: "In progress", tone: "info", icon: "progress",
    meaning: "Met once your in-progress courses are completed with qualifying grades.",
  },
  partially_satisfied: {
    label: "Partly complete", tone: "neutral", icon: "partial",
    meaning: "Some of this requirement is met; more is still needed.",
  },
  unsatisfied: {
    label: "Still needed", tone: "neutral", icon: "remaining",
    meaning: "Nothing on your record meets this yet.",
  },
  indeterminate: {
    label: "Can't determine", tone: "warning", icon: "unknown",
    meaning: "CoursePilot cannot determine this automatically from the data it has. It is not a finding that you fail it.",
  },
  not_evaluable: {
    label: "Can't evaluate automatically", tone: "warning", icon: "unknown",
    meaning: "CoursePilot cannot evaluate this condition automatically. It is not a finding that you fail it.",
  },
};

/** Program rules (GPA, grade quotas, exclusions): here "unsatisfied" IS a violation. */
export const RULE_STATUS: Record<RequirementStatus, Presentation> = {
  ...REQUIREMENT_STATUS,
  unsatisfied: {
    label: "Not met", tone: "danger", icon: "failed",
    meaning: "Your record does not meet this program rule.",
  },
  satisfied: {
    label: "Met", tone: "success", icon: "complete",
    meaning: "Your record meets this program rule.",
  },
};

export const AUDIT_STATUS: Record<AuditStatus, Presentation> = {
  complete: {
    label: "All requirements complete", tone: "success", icon: "complete",
    meaning: "Every requirement CoursePilot evaluates is met by completed coursework.",
  },
  in_progress: {
    label: "Complete once current courses finish", tone: "info", icon: "progress",
    meaning: "Every requirement is met or will be once your in-progress courses are completed.",
  },
  incomplete: {
    label: "Requirements remaining", tone: "neutral", icon: "remaining",
    meaning: "Some requirements are not yet met.",
  },
  indeterminate: {
    label: "Some conditions can't be determined", tone: "warning", icon: "unknown",
    meaning: "Requirements look met, but a program condition can't be evaluated automatically.",
  },
  insufficient_data: {
    label: "Not enough information", tone: "warning", icon: "unknown",
    meaning: "CoursePilot can't reach an overall result with the data it has.",
  },
};

export const FINDING_SEVERITY: Record<FindingSeverity, { tone: Tone; label: string }> = {
  blocking: { tone: "danger", label: "Needs attention" },
  warning: { tone: "warning", label: "Warning" },
  info: { tone: "info", label: "Note" },
};

/* ----------------------------------------------------------------- plan */

export const PLAN_STATUS: Record<PlanStatus, Presentation> = {
  covers_all_requirements: {
    label: "Covers every remaining requirement", tone: "success", icon: "complete",
    meaning: "If you pass these courses, the Degree Engine projects every requirement met.",
  },
  partial: {
    label: "Partial plan", tone: "warning", icon: "partial",
    meaning: "Some requirements could not be planned with the evidence CoursePilot has. See what's blocking them below.",
  },
  nothing_to_plan: {
    label: "Nothing left to plan", tone: "success", icon: "complete",
    meaning: "The Degree Engine finds no remaining requirement to plan for.",
  },
};

export const ELIGIBILITY_STATE: Record<EligibilityState, Presentation> = {
  satisfied_by_history: {
    label: "Prerequisites met", tone: "success", icon: "complete",
    meaning: "Your completed courses meet the published prerequisites.",
  },
  conditional_on_plan: {
    label: "Prerequisites met if earlier courses are passed", tone: "info", icon: "progress",
    meaning: "The prerequisites are met once the earlier courses this depends on are passed.",
  },
  unsatisfied: {
    label: "Prerequisites not met", tone: "danger", icon: "failed",
    meaning: "The published prerequisites are not met.",
  },
  needs_confirmation: {
    label: "Needs confirmation", tone: "warning", icon: "unknown",
    meaning: "CoursePilot cannot evaluate this prerequisite automatically, so it did not place the course. Confirm with the department or an advisor.",
  },
};

export const PLAN_SEVERITY: Record<PlanSeverity, { tone: Tone; label: string; icon: IconKey }> = {
  blocker: { tone: "danger", label: "Could not be planned", icon: "failed" },
  needs_confirmation: { tone: "warning", label: "Needs confirmation", icon: "unknown" },
  warning: { tone: "info", label: "Note", icon: "info" },
};

export const STALE_REASON: Record<StaleReason, string> = {
  program_changed: "Your program or catalog year changed.",
  academic_record_changed: "Your academic record changed.",
  program_rules_changed: "Your program's requirements were updated.",
  rutgers_data_changed: "CoursePilot loaded newer Rutgers course data.",
  engine_updated: "CoursePilot's planning engine was updated.",
  start_term_passed: "The plan's first semester has started or is already on your record.",
  planning_preferences_changed: "You changed your planning preferences.",
};

/* ------------------------------------------------- readiness (7.2.1) */

export const READINESS_CHECK: Record<"done" | "missing" | "warning" | "info", { tone: Tone; icon: IconKey; label: string }> = {
  done: { tone: "success", icon: "complete", label: "Done" },
  missing: { tone: "neutral", icon: "remaining", label: "Needed" },
  warning: { tone: "warning", icon: "attention", label: "Worth knowing" },
  info: { tone: "info", icon: "info", label: "Note" },
};

export const PACE_LABEL: Record<string, string> = {
  balanced: "Balanced",
  lighter: "Lighter",
  finish_earlier: "Finish earlier",
};

export const GOAL_KIND: Record<string, string> = {
  second_major: "Another major",
  minor: "Minor",
  certificate: "Certificate",
};

const SEASONS: Record<string, string> = { "0": "Winter", "1": "Spring", "7": "Summer", "9": "Fall" };

/** "20271" -> "Spring 2027". Display only (term codes are YYYYT). */
export function termLabel(code: string | null | undefined): string {
  if (!code || code.length !== 5) return code ?? "";
  const season = SEASONS[code[4] ?? ""];
  return season ? `${season} ${code.slice(0, 4)}` : code;
}

function list(items: string[]): string {
  if (items.length <= 1) return items.join("");
  return `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
}

/**
 * The engine's placement reasons, in words. Every sentence is built only
 * from fields of the same PlannedCourse - nothing is inferred.
 */
export function planReasonText(reason: PlanReason, course: PlannedCourse): string {
  const off = course.offering;
  switch (reason) {
    case "satisfies_requirement": {
      const names = [...new Set(course.requirements.map((r) => r.requirement_name))];
      return names.length ? `Counts toward ${list(names)}` : "Counts toward a remaining requirement";
    }
    case "prerequisites_satisfied_by_history":
      return "You've already met its prerequisites";
    case "prerequisites_conditional_on_plan":
      return "Its prerequisites are met once the earlier courses it depends on are passed";
    case "no_prerequisite_published":
      return "Rutgers publishes no prerequisite for it";
    case "required_prerequisite_for":
      return course.unlocks.length
        ? `It's a prerequisite for ${list(course.unlocks)}`
        : "It's a prerequisite for a later course in this plan";
    case "required_corequisite_for":
      return course.corequisite_group.length
        ? `It must be taken together with ${list(course.corequisite_group)}`
        : "It must be taken together with a co-requisite";
    case "unlocks_downstream_course":
      return course.unlocks.length
        ? `Taking it now opens ${list(course.unlocks)} later`
        : "Taking it now opens later courses";
    case "confirmed_offering_in_term":
      return `Rutgers lists it in the ${termLabel(off.target_term)} schedule`;
    case "historically_offered_in_season":
      return off.most_recent_term
        ? `${termLabel(off.target_term)} isn't published yet; Rutgers offered it in ${termLabel(off.most_recent_term)}`
        : `${termLabel(off.target_term)} isn't published yet; Rutgers has offered it in this season before`;
    case "rules_from_earlier_term":
      return `Prerequisite rules are taken from ${termLabel(course.prerequisite.rules_term)}, the latest Rutgers published`;
    case "ranked_by_requirement_coverage":
      return "Ranks ahead on candidate requirement coverage; actual credit allocation is checked separately";
    case "selected_by_canonical_tie_break":
      return `One of ${course.interchangeable_alternatives + 1} equally suitable courses; chosen by a fixed tie-break, not by preference`;
  }
}

export const OFFERING_KIND: Record<OfferingEvidenceKind, string> = {
  confirmed_in_term: "Listed in that term's Rutgers schedule",
  not_offered_in_term: "Not in that term's (complete) Rutgers schedule",
  historically_offered_in_season: "Offered in past terms of the same season",
  offered_in_other_seasons_only: "Only offered in other seasons",
  no_offering_evidence: "No record of Rutgers offering it",
};

/**
 * Why a course could not be evaluated or placed. The codes are open-ended
 * (`prefix:detail`), so unknown ones are shown as the code itself rather
 * than guessed at.
 */
export function blockedReasonText(code: string): string {
  const [prefix, ...rest] = code.split(":");
  const detail = rest.join(":");
  switch (prefix) {
    case "unmodeled_condition":
      return `A condition CoursePilot doesn't model${detail ? ` (${detail.replace(/_/g, " ")})` : ""}`;
    case "unsupported":
      return "Prerequisite wording CoursePilot can't interpret automatically";
    case "corequisite_not_interpreted":
      return "Co-requisite wording CoursePilot can't interpret automatically";
    case "no_corequisite_published":
      return "Rutgers publishes no co-requisite for it";
    case "no_prerequisite_published":
      return "Rutgers publishes no prerequisite for it";
    case "no_offering_in_term":
      return "No Rutgers offering in the term whose rules were checked";
    case "campus_prerequisites_differ":
      return "Prerequisites differ between campuses";
    case "campus_corequisites_differ":
      return "Co-requisites differ between campuses";
    case "corequisite_on_some_sections":
      return "A co-requisite applies to only some sections";
    case "no_offering_evidence":
      return "No record of Rutgers offering it";
    case "offered_in_other_seasons_only":
      return "Only offered in seasons this plan doesn't include";
    case "not_offered_in_term":
      return "Not in the published schedule for the planned term";
    case "variable_credit":
      return "Variable credits - CoursePilot won't guess a credit value";
    case "no_prerequisite_path":
      return "No way to meet its prerequisites within this plan";
    default:
      if (prefix?.startsWith("dependency_")) {
        return detail ? `Depends on ${detail}, which couldn't be planned` : "Depends on a course that couldn't be planned";
      }
      return code;
  }
}

/* ------------------------------------------------------------- schedule */

export const SCHEDULE_STATUS: Record<ScheduleStatus, Presentation> = {
  options_found: {
    label: "Valid schedules found", tone: "success", icon: "complete",
    meaning: "These section combinations have no time conflicts and meet your hard settings.",
  },
  no_valid_schedule: {
    label: "No valid schedule", tone: "danger", icon: "failed",
    meaning: "No combination of sections works together with your settings. The reasons are below.",
  },
  term_schedule_not_published: {
    label: "Term not published", tone: "warning", icon: "unknown",
    meaning: "CoursePilot has no Rutgers section data for this term yet, so it cannot build a schedule. It never borrows another term's sections.",
  },
  course_eligibility_failed: {
    label: "Course eligibility problem", tone: "danger", icon: "failed",
    meaning: "At least one requested course can't be taken by you that term, per the prerequisite engine. See below.",
  },
  search_limit_reached: {
    label: "Search limit reached", tone: "warning", icon: "unknown",
    meaning: "The search stopped before it could prove any option. Try fewer courses or fewer settings.",
  },
};

export const SCHEDULE_SEVERITY: Record<ScheduleSeverity, { tone: Tone; label: string; icon: IconKey }> = {
  blocker: { tone: "danger", label: "Blocks this schedule", icon: "failed" },
  needs_confirmation: { tone: "warning", label: "Needs confirmation", icon: "unknown" },
  warning: { tone: "info", label: "Note", icon: "info" },
};

export const SCHEDULE_REASON: Record<ScheduleReason, string> = {
  no_time_conflicts: "No two meetings overlap",
  all_meeting_times_verified: "Every meeting time is published by Rutgers",
  all_hard_constraints_satisfied: "Meets all of your time and day settings",
  all_restrictions_satisfied_or_absent: "No section restriction excludes you",
  all_sections_open_current: "Every section is open right now",
  all_sections_open_when_last_observed: "Every section was open when last observed",
  required_components_included: "Includes every required lab or recitation Rutgers links to these sections",
  matches_preferred_campus: "Matches your preferred campus",
};

export const RESTRICTION_OUTCOME: Record<RestrictionOutcome, Presentation> = {
  unrestricted: {
    label: "No restriction listed", tone: "neutral", icon: "info",
    meaning: "Rutgers lists no \"open to\" restriction for this section.",
  },
  satisfied: {
    label: "Restriction matches your record", tone: "success", icon: "complete",
    meaning: "The section is restricted, and your program matches a listed group.",
  },
  not_satisfied: {
    label: "Restricted to other students", tone: "danger", icon: "failed",
    meaning: "Your record does not match the groups this section is open to.",
  },
  unknown: {
    label: "Restriction can't be checked", tone: "warning", icon: "unknown",
    meaning: "CoursePilot can't tell whether this restriction includes you. Check before registering.",
  },
};

function timeOf(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

/**
 * Live availability - a separate fact from structural validity. "Open now"
 * is said only when the observation is CURRENT; an older one is dated.
 */
export function availabilityText(a: AvailabilityEvidence): Presentation {
  if (a.freshness === "unobserved" || a.state === "unknown") {
    return {
      label: "Availability unknown", tone: "neutral", icon: "unknown",
      meaning: "CoursePilot has no recent open/closed observation for this section.",
    };
  }
  const word = a.state === "open" ? "Open" : "Closed";
  if (a.freshness === "current") {
    return {
      label: a.observed_at ? `${word} as of ${timeOf(a.observed_at)}` : word,
      tone: a.state === "open" ? "success" : "warning",
      icon: a.state === "open" ? "complete" : "failed",
      meaning: `Rutgers reported this section ${word.toLowerCase()} in CoursePilot's latest check.`,
    };
  }
  return {
    label: a.observed_at ? `${word} when last seen (${timeOf(a.observed_at)})` : `${word} when last seen`,
    tone: "neutral",
    icon: "info",
    meaning: "This observation is not current. The section may have opened or closed since.",
  };
}
