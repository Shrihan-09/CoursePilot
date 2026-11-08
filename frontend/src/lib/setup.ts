/**
 * Prompt 10a first-run setup wizard: steps, resume and routing rules, as pure
 * functions so they are tested without a browser. Nothing here is academic:
 * the wizard writes the same profile fields Planning preferences writes.
 */
import type { DnProgress } from "@/types/degree-audit";
import type { Me, Onboarding, Program } from "@/types/onboarding";
import type { PlanningProfileInput } from "@/types/personalization";

export type SetupStepKey =
  | "welcome" | "about" | "record" | "intensity" | "week" | "schedule" | "musts" | "build";

export interface SetupStep {
  key: SetupStepKey;
  title: string;
  /** One line: why the question matters. */
  why: string;
  /** Every step but About you can be skipped; the defaults apply. */
  skippable: boolean;
}

export const SETUP_STEPS: SetupStep[] = [
  { key: "welcome", title: "Welcome", why: "", skippable: false },
  { key: "about", title: "About you", why: "Your program decides which requirements CoursePilot checks.", skippable: false },
  { key: "record", title: "Bring your record", why: "Your real courses make the plan start from where you are.", skippable: true },
  { key: "intensity", title: "How intense?", why: "This sets how many credits and major courses go in each semester.", skippable: true },
  { key: "week", title: "Your week", why: "Time you spend elsewhere lowers how much each semester holds.", skippable: true },
  { key: "schedule", title: "Schedule shape", why: "These shape the weekly schedules you build later.", skippable: true },
  { key: "musts", title: "Musts", why: "Courses you must take go in first.", skippable: true },
  { key: "build", title: "Build my plan", why: "", skippable: false },
];

export const STEP_KEYS = SETUP_STEPS.map((s) => s.key);

export function stepIndex(key: SetupStepKey): number {
  return STEP_KEYS.indexOf(key);
}

/** "Step 3 of 8". */
export function stepCounter(key: SetupStepKey): string {
  return `Step ${stepIndex(key) + 1} of ${SETUP_STEPS.length}`;
}

/** Percent of the bar to fill when this step is showing. */
export function stepProgress(key: SetupStepKey): number {
  return Math.round(((stepIndex(key) + 1) / SETUP_STEPS.length) * 100);
}

/**
 * Where a returning student lands: the first step not yet finished or
 * skipped. With no record yet (the program is chosen in About you), Welcome.
 */
export function resumeStep(status: Pick<Onboarding, "profile" | "setup">): SetupStepKey {
  if (!status.profile) return "welcome";
  const done = new Set(status.setup?.completed_steps ?? []);
  return STEP_KEYS.find((k) => !done.has(k)) ?? "build";
}

/** The saved list after finishing (or skipping) `key`, in wizard order. */
export function withStepDone(done: readonly string[], key: SetupStepKey): SetupStepKey[] {
  const set = new Set([...done, key]);
  return STEP_KEYS.filter((k) => set.has(k));
}

/** New users (setup not completed) go to the wizard; returning users go Home. */
export function homeFor(me: Pick<Me, "onboarding_complete"> | null): "/onboarding" | "/home" {
  return me && !me.onboarding_complete ? "/onboarding" : "/home";
}

/* ------------------------------------------------------------- uploads */

export type ImportCardState = "not_added" | "processing" | "needs_review" | "confirmed";

export const IMPORT_CARD_LABEL: Record<ImportCardState, string> = {
  not_added: "Not added",
  processing: "Processing",
  needs_review: "Needs review",
  confirmed: "Confirmed",
};

/** One card's state from the API's import states (and an upload in flight). */
export function importCardState(states: readonly string[], uploading = false): ImportCardState {
  if (uploading || states.includes("processing")) return "processing";
  if (states.includes("extracted")) return "needs_review";
  if (states.includes("confirmed")) return "confirmed";
  return "not_added";
}

/* ------------------------------------------------------------- labels */

export type SupportLabel = "Beta supported" | "Preview — not yet reviewed" | "Coming soon";

/** The support label a program shows, from its versions' published status only. */
export function programSupportLabel(program: Pick<Program, "versions">): SupportLabel {
  const usable = program.versions.filter((v) => v.support_status !== "unavailable");
  if (usable.length === 0) return "Coming soon";
  return usable.some((v) => v.support_status === "supported") ? "Beta supported" : "Preview — not yet reviewed";
}

/** The label a plan carries: from a confirmed Degree Navigator audit, or estimated. */
export function planSourceLabel(progress: Pick<DnProgress, "source"> | null | undefined): string {
  return progress?.source === "degree_navigator"
    ? "From your Degree Navigator audit"
    : "Estimated — import your audit for accuracy";
}

/** Semesters a student could have started in: Fall and Spring, newest first. */
export function firstTermChoices(now: Date, yearsBack = 6): string[] {
  const y = now.getFullYear();
  const out: string[] = [];
  // Up to this year's Fall (an incoming student's first semester), newest first.
  for (let year = y; year >= y - yearsBack; year--) out.push(`${year}9`, `${year}1`);
  return out;
}

/** A saved planning profile as an input again: the server refuses its read-only fields. */
export function asProfileInput<T extends PlanningProfileInput>(p: T): PlanningProfileInput {
  const { version: _v, confirmed_at: _c, source: _s, effective_max_terms: _e, ...input } =
    p as T & Partial<Record<"version" | "confirmed_at" | "source" | "effective_max_terms", unknown>>;
  return input;
}
