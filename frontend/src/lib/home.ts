/**
 * Home dashboard logic (UI polish B1/B2). Presentation only: every number is
 * one the engines stated (audit, modeled progress, saved plan, the bundled
 * registration schedule). Nothing here decides anything academic.
 */
import { termLabel } from "@/lib/academic/vocabulary";
import type { ProgressMetric } from "@/types/progress";
import type { ReadinessState } from "@/types/personalization";
import type { SavedPlan } from "@/types/plan";

/* ------------------------------------------------------------ record stats */

/**
 * Courses planned (not yet taken) in the current saved plan. Counted from the
 * plan's terms, not from history entries marked "planned" - the plan is where
 * planned courses live (fixes "Planned 0" with five courses next term).
 */
export function plannedCourseCount(plan: SavedPlan | null | undefined): number {
  if (!plan) return 0;
  const seen = new Set<string>();
  for (const term of plan.result.terms) for (const c of term.courses) seen.add(`${c.course}@${term.term_code}`);
  return seen.size;
}

/** Whole numbers for credit figures the engine sends as decimals ("34.0" -> "34"). */
export function wholeCredits(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "-";
  const n = Number(value);
  if (!Number.isFinite(n)) return String(value);
  return Number.isInteger(n) ? String(n) : String(Math.round(n * 10) / 10);
}

/* ------------------------------------------------------------ rings */

export interface RingValue {
  key: "degree" | "major" | "core";
  label: string;
  /** Fractions of the full circle, 0..1, summing to at most 1. */
  done: number;
  inProgress: number;
  /** Needs review / unknown: drawn grey, never as done. */
  review: number;
  available: boolean;
  /** Plain-language numbers for the legend ("66 / 120 credits"). */
  summary: string;
  detail: string;
}

const clamp = (n: number) => (Number.isFinite(n) ? Math.min(1, Math.max(0, n)) : 0);

/** Clamp the three segments in order so they never exceed a full ring. */
function segments(done: number, inProgress: number, review: number) {
  const d = clamp(done);
  const p = Math.min(clamp(inProgress), 1 - d);
  const r = Math.min(clamp(review), 1 - d - p);
  return { done: d, inProgress: p, review: r };
}

/** Outer ring: degree credits (earned + in progress) against the degree total. */
export function degreeRing(credits: SavedPlan["result"]["degree_credits"] | null | undefined): RingValue {
  if (!credits || !(Number(credits.required) > 0)) {
    return { key: "degree", label: "Degree credits", done: 0, inProgress: 0, review: 0, available: false,
      summary: "Not modeled", detail: "The degree credit total isn't modeled for your school and catalog year yet." };
  }
  const total = Number(credits.required);
  const earned = Number(credits.completed);
  const current = Number(credits.in_progress);
  return { key: "degree", label: "Degree credits", ...segments(earned / total, current / total, 0), available: true,
    summary: `${wholeCredits(earned + current)} / ${wholeCredits(total)} credits`,
    detail: `${wholeCredits(earned)} earned, ${wholeCredits(current)} in progress, of the ${wholeCredits(total)}-credit degree total.` };
}

const ratio = (c: { units: { numerator: number; denominator: number } }) =>
  c.units.denominator ? c.units.numerator / c.units.denominator : 0;

/**
 * Middle (major) and inner (Core) rings from the modeled-progress metric.
 * Unknown obligations are a grey segment. When Degree Navigator disagrees
 * with a requirement the metric counts as met (`needsReview`), the counted
 * part is shown grey too - never as done.
 */
export function metricRing(key: "major" | "core", label: string, metric: ProgressMetric | undefined, needsReview = false): RingValue {
  if (!metric || metric.availability !== "available" || !metric.denominator_units) {
    return { key, label, done: 0, inProgress: 0, review: 0, available: false,
      summary: "Not modeled", detail: `${label} progress isn't available for your program yet; no percentage is assumed.` };
  }
  const budget = metric.denominator_units;
  const done = ratio(metric.completed) / budget;
  const current = Math.max(0, ratio(metric.projected_current) - ratio(metric.completed)) / budget;
  const unknown = metric.unknown_obligations / budget;
  const seg = needsReview ? segments(0, current, done + unknown) : segments(done, current, unknown);
  const pct = metric.completed.percent !== null ? `${Number(metric.completed.percent).toFixed(0)}% met` : "Not measured";
  const parts = [needsReview ? `${pct}, needs review: Degree Navigator disagrees` : pct];
  if (current > 0) parts.push(`${Number(metric.projected_current.percent ?? 0).toFixed(0)}% with courses in progress`);
  if (metric.unknown_obligations > 0) parts.push(`${metric.unknown_obligations} can't be determined yet`);
  return { key, label, ...seg, available: true, summary: needsReview ? "Needs review" : pct, detail: `${parts.join("; ")}.` };
}

/* ------------------------------------------------------------ registration */

export interface RegistrationSchedule {
  term_code: string;
  term_label: string;
  tiers: { min_degree_credits: number; label: string; opens_at: string }[];
  all_students_opens_at: string;
  closes_at: string | null;
  nightly_closure: Record<string, string>;
  source_url: string;
  source_basis: string;
}

export interface RegistrationWindow {
  term_label: string;
  tier_label: string;
  opens_at: Date;
  closes_at: Date | null;
  state: "upcoming" | "open" | "closed";
  credits: number;
  source_url: string;
}

/** The tier a student with `earnedDegreeCredits` registers in (the Registrar's table). */
export function registrationWindow(schedule: RegistrationSchedule | null | undefined, earnedDegreeCredits: number | null, now: Date): RegistrationWindow | null {
  if (!schedule || earnedDegreeCredits === null || !Number.isFinite(earnedDegreeCredits)) return null;
  const tiers = [...schedule.tiers].sort((a, b) => b.min_degree_credits - a.min_degree_credits);
  const tier = tiers.find((t) => earnedDegreeCredits >= t.min_degree_credits);
  if (!tier) return null;
  const opens = new Date(tier.opens_at);
  const closes = schedule.closes_at ? new Date(schedule.closes_at) : null;
  const state = now < opens ? "upcoming" : closes && now >= closes ? "closed" : "open";
  return { term_label: schedule.term_label, tier_label: tier.label, opens_at: opens, closes_at: closes, state,
    credits: earnedDegreeCredits, source_url: schedule.source_url };
}

/** "Fri, Nov 13, 9:00 AM" in Rutgers' time zone. */
export function easternTime(d: Date): string {
  return d.toLocaleString("en-US", { timeZone: "America/New_York", weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

/* ------------------------------------------------------------ next up */

export interface NextUp {
  kind: "setup" | "personalize" | "build_plan" | "stale_plan" | "choices" | "registration" | "upload_dn" | "schedule" | "degree";
  title: string;
  body: string;
  href: string;
  cta: string;
}

export interface NextUpInput {
  onboardingState: string;
  readiness: ReadinessState | undefined;
  plan: SavedPlan | null | undefined;
  /** Open "Your choices" questions for the first plan term (null: unknown). */
  choicesPending: number | null;
  /** A confirmed Degree Navigator audit for the student's own program (null: unknown). */
  hasDnAudit: boolean | null;
  registration: RegistrationWindow | null;
}

/**
 * The single most important action. Order: finish setup, personalize, build a
 * plan, refresh a stale plan, settle "Your choices", then the registration
 * window (time-bound), then the Degree Navigator upload, then the schedule.
 */
export function nextUp(i: NextUpInput): NextUp {
  if (i.onboardingState !== "complete") {
    return { kind: "setup", title: "Finish setting up", body: "Add your courses so CoursePilot can show your progress.", href: "/onboarding", cta: "Continue setup" };
  }
  if (i.readiness === "needs_planning_profile") {
    return { kind: "personalize", title: "Personalize your plan", body: "Tell CoursePilot your pace and how many credits to aim for. It takes about a minute.", href: "/plan/personalize", cta: "Personalize" };
  }
  if (!i.plan) {
    return { kind: "build_plan", title: "Build your semester plan", body: "Lay out the courses that complete your remaining requirements.", href: "/plan", cta: "Build a plan" };
  }
  if (i.plan.freshness.state === "stale") {
    return { kind: "stale_plan", title: "Your plan is out of date", body: "Something it was built from has changed. Regenerate it before scheduling.", href: "/plan", cta: "Review plan" };
  }
  const first = i.plan.result.terms[0];
  if (first && (i.choicesPending ?? 0) > 0) {
    const n = i.choicesPending ?? 0;
    return { kind: "choices", title: `Finish "Your choices" for ${first.label}`, body: `${n} ${n === 1 ? "choice is" : "choices are"} still open. Settle ${n === 1 ? "it" : "them"} before you build a schedule.`, href: "/plan", cta: "Review choices" };
  }
  const reg = i.registration;
  if (first && reg && reg.state !== "closed" && reg.term_label === first.label) {
    const basis = `With ${wholeCredits(reg.credits)} earned degree credits you register in the ${reg.tier_label} group (Registrar's schedule; confirm in WebReg).`;
    return reg.state === "upcoming"
      ? { kind: "registration", title: `Your ${reg.term_label} registration opens ${easternTime(reg.opens_at)}`, body: basis, href: `/schedule?term=${first.term_code}`, cta: `Build ${first.label} schedule` }
      : { kind: "registration", title: `${reg.term_label} registration is open for you`, body: `${basis}${reg.closes_at ? ` Open until ${easternTime(reg.closes_at)}.` : ""}`, href: `/schedule?term=${first.term_code}`, cta: `Build ${first.label} schedule` };
  }
  if (i.hasDnAudit === false) {
    return { kind: "upload_dn", title: "Upload your Degree Navigator audit", body: "It lets CoursePilot check its progress against Rutgers' own audit and flag any disagreement.", href: "/degree", cta: "Upload audit" };
  }
  if (first) {
    return { kind: "schedule", title: `Build your ${first.label} schedule`, body: `${first.courses.length} planned courses, ${wholeCredits(first.credits)} credits.`, href: `/schedule?term=${first.term_code}`, cta: `Build ${first.label} schedule` };
  }
  return { kind: "degree", title: "Review your degree", body: "See each requirement and what counts toward it.", href: "/degree", cta: "View degree" };
}

/* ------------------------------------------------------------ graduation */

export function graduation(plan: SavedPlan | null | undefined, dnNotMet: number): { term: string | null; onTrack: boolean; why: string } {
  if (!plan) return { term: null, onTrack: false, why: "Build a plan to see when you could graduate." };
  const r = plan.result;
  const term = r.graduation_target ? termLabel(r.graduation_target.term_code) : r.terms.length ? termLabel(r.terms[r.terms.length - 1]?.term_code) : null;
  if (plan.freshness.state === "stale") return { term, onTrack: false, why: "Your plan is out of date; regenerate it to check again." };
  if (r.graduation_target?.conflict) return { term, onTrack: false, why: r.graduation_target.conflict };
  if (dnNotMet > 0) return { term, onTrack: false, why: "Degree Navigator says a requirement CoursePilot counts as met is not met." };
  if (r.status !== "covers_all_requirements" && r.status !== "nothing_to_plan") return { term, onTrack: false, why: "Some requirements couldn't be planned yet; see your plan for what's blocking them." };
  if (r.degree_credits && !r.degree_credits.met) return { term, onTrack: false, why: "The plan is short of the degree credit total." };
  return { term, onTrack: true, why: "If you pass the planned courses, every modeled requirement is met by then." };
}
