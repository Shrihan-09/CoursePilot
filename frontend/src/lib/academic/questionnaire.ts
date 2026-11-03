/**
 * Task 2 planning questionnaire, as plain words and numbers. The hour
 * mappings mirror the server's (backend app.services.planning.questionnaire);
 * the server's numbers are what plans use.
 */
import type { BlockKind, BlockedTime, Commitments, WorkloadLevel } from "@/types/personalization";
import type { Pace } from "@/types/plan";

export const BLOCK_KIND: Record<BlockKind, string> = {
  job: "Job shift", club: "Club or org meeting", greek_life: "Greek life", athletics: "Athletics",
  research: "Research", recruiting: "Recruiting", other: "Other",
};
const GREEK_HOURS: Record<Commitments["greek_life"], number> = { none: 0, rushing: 6, new_member: 10, active: 4 };
const CLUB_HOURS: Record<Commitments["clubs"], number> = { none: 0, light: 3, heavy: 8 };

/** Weekly hours, as the server maps them (app.services.planning.questionnaire). */
export function commitmentHours(c: Commitments): number {
  return c.job_hours_per_week + GREEK_HOURS[c.greek_life] + CLUB_HOURS[c.clubs]
    + c.athletics_hours_per_week + c.research_hours_per_week + c.recruiting_hours_per_week;
}

/** Workload points the commitments take: hours / 3, to the nearest half. */
export function budgetReduction(hours: number): number {
  return Math.round((hours * 2) / 3) / 2;
}

export function commitmentSummary(c: Commitments | null | undefined): string {
  const hours = c ? commitmentHours(c) : 0;
  if (!hours) return "No commitments";
  return `About ${hours} hours a week, so each semester's workload budget is ${budgetReduction(hours)} lower (never below the credit minimum)`;
}

/** The server's WORKLOAD_VS_GRADUATION_TARGET rule, shown while answering. */
export function paceLevelConflict(pace: Pace | null | undefined, level: WorkloadLevel | null | undefined): string | null {
  if (pace === "finish_earlier" && level === "light") {
    return "You chose to graduate early with Light semesters. Light semesters carry 12-14 credits, so finishing early may not fit; the plan will say if it does not.";
  }
  if (pace === "lighter" && level === "challenging") {
    return "You chose No rush with Challenging semesters. Challenging semesters carry 17-21 credits, so your plan may finish well before your target.";
  }
  return null;
}

export function blockedTimeText(b: BlockedTime): string {
  return `${BLOCK_KIND[b.kind]}: ${b.days.join("")} ${b.start}-${b.end}`;
}

/** Spec 09 asks job hours as bands; each band is stored as the hours below. */
export const JOB_BANDS: { hours: number; label: string }[] = [
  { hours: 0, label: "No job" },
  { hours: 8, label: "Under 10 hours a week" },
  { hours: 15, label: "10-20 hours a week" },
  { hours: 25, label: "More than 20 hours a week" },
];

/** SOC campus codes in New Brunswick. */
export const CAMPUSES: { code: string; label: string }[] = [
  { code: "BUS", label: "Busch" },
  { code: "CAC", label: "College Avenue" },
  { code: "LIV", label: "Livingston" },
  { code: "D/C", label: "Douglass/Cook" },
  { code: "DNB", label: "Downtown New Brunswick" },
];

/** Major-related courses per semester, as the questionnaire offers it. */
export const MAJOR_FOCUS: { key: string; label: string; min: number | null; max: number | null }[] = [
  { key: "level", label: "Use my level's default", min: null, max: null },
  { key: "1", label: "1", min: 1, max: 1 },
  { key: "2-3", label: "2-3", min: 2, max: 3 },
  { key: "3-4", label: "3-4", min: 3, max: 4 },
];
