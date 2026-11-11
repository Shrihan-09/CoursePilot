import { CalendarDays, ChevronRight, Feather, Lock, LockOpen, MapPin, Monitor, RefreshCw, SlidersHorizontal } from "lucide-react";
import Link from "next/link";

import { StateBadge, STATUS_ICON } from "@/components/academic/status";
import { Button, buttonVariants } from "@/components/ui/button";
import { Alert, Progress } from "@/components/ui/feedback";
import {
  blockedReasonText,
  DN_NOT_MET_MESSAGE,
  DN_REVIEW,
  ELIGIBILITY_STATE,
  OFFERING_KIND,
  PLAN_SEVERITY,
  PACE_LABEL,
  PLAN_STATUS,
  deliveryText,
  offeringLabel,
  placementLabel,
  ROLE_LABEL,
  WORKLOAD_LEVEL_LABEL,
  workloadBreakdown,
  planReasonText,
  topicsText,
  STALE_REASON,
  termLabel,
} from "@/lib/academic/vocabulary";
import { courseTone } from "@/lib/tone";
import { cn } from "@/lib/utils";
import type { BlockedCourse, PlannedCourse, PlannedCourseState, PlanningIssue, PlanObservation, PlanTerm, SavedPlan } from "@/types/plan";
import { type PlanChanges, StateLine, UndoBar } from "./plan-changes";

/** Prompt 11: what the board can ask for (all through preview -> confirm). */
export interface BoardActions {
  changes: PlanChanges;
  states: Map<string, PlannedCourseState>;
  lockedTerms: string[];
  onOptions: (course: string, term: string) => void;
  onAddCourse?: (term: string) => void;
}
import { conditionalLabel, creditsText, electiveSlotText, loadText } from "@/lib/academic/plan-text";

/**
 * The Planning Engine's plan, semester by semester (Phase 7.2).
 *
 * "Why is this course here?" is answered from the engine's own fields -
 * reasons, requirement contributions, prerequisite and offering evidence -
 * with no AI and no inference. Courses the engine would NOT place stay out of
 * the semesters; they are listed with the original Rutgers condition.
 */

export function StaleNotice({ plan, onRegenerate, regenerating }: { plan: SavedPlan; onRegenerate: () => void; regenerating: boolean }) {
  if (plan.freshness.state === "current") return null;
  return (
    <Alert
      tone="warning"
      title="This plan is out of date"
      action={
        <Button size="sm" variant="secondary" onClick={onRegenerate} loading={regenerating}>
          <RefreshCw aria-hidden className="size-4" /> Regenerate
        </Button>
      }
    >
      <p>It was built from inputs that have since changed, so it may no longer be right:</p>
      <ul className="mt-1 list-disc pl-5">
        {plan.freshness.reasons.map((r) => <li key={r}>{STALE_REASON[r] ?? r}</li>)}
      </ul>
    </Alert>
  );
}

function WhyHere({ course }: { course: PlannedCourse }) {
  const pre = course.prerequisite;
  const eligibility = ELIGIBILITY_STATE[pre.state];
  return (
    <details className="group">
      <summary className="inline-flex min-h-8 cursor-pointer list-none items-center gap-1 text-xs font-medium text-primary-text [&::-webkit-details-marker]:hidden">
        <ChevronRight aria-hidden className="size-3.5 transition-transform group-open:rotate-90" />
        Why this course, this term
      </summary>
      <div className="mt-2 space-y-3 rounded border border-border bg-surface p-3 text-sm">
        <ul className="space-y-1">
          {course.reasons.map((r) => (
            <li key={r} className="flex gap-2 text-fg">
              <span aria-hidden className="text-fg-muted">·</span>
              <span>{planReasonText(r, course)}</span>
            </li>
          ))}
        </ul>
        <div className="space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs font-medium uppercase tracking-wide text-fg-muted">Prerequisites</span>
            <StateBadge state={pre.state === "conditional_on_plan" && pre.conditional_on.length
              ? { ...eligibility, label: conditionalLabel(pre.conditional_on) } : eligibility} />
          </div>
          {pre.conditional_on.length > 0 && (
            <ul className="space-y-0.5 text-fg-secondary">
              {pre.conditional_on.map((d) => (
                <li key={`${d.course}-${d.term_code}`}>
                  Needs <span className="code text-fg">{d.course}</span>
                  {d.required_grade ? ` with ${d.required_grade} or better` : ""} -{" "}
                  {d.source === "in_progress" ? "in progress" : "planned"} for {termLabel(d.term_code)}
                </li>
              ))}
            </ul>
          )}
          {pre.raw_text && (
            <p className="text-fg-secondary">
              <span className="text-fg-muted">Rutgers: </span>
              <span className="[overflow-wrap:anywhere]">{pre.raw_text.replace(/<[^>]+>/g, "").trim()}</span>
              {pre.rules_term && <span className="text-fg-muted"> ({termLabel(pre.rules_term)})</span>}
            </p>
          )}
        </div>
        <p className="text-fg-secondary">
          <span className="text-xs font-medium uppercase tracking-wide text-fg-muted">Offering </span>
          {OFFERING_KIND[course.offering.kind]}
          {course.offering.observed_terms.length > 0 && (
            <span className="text-fg-muted"> · seen in {course.offering.observed_terms.slice(-3).map(termLabel).join(", ")}</span>
          )}
        </p>
        {course.trace && <TraceDetails trace={course.trace} />}
      </div>
    </details>
  );
}

const SOURCE_LABEL: Record<string, string> = {
  soc_courses_json: "Rutgers Schedule of Classes",
  degree_navigator_audit: "your Degree Navigator audit",
  coursepilot_audit: "CoursePilot's estimate",
};

/** Phase 7.6: the structured reason - requirement status source, Core codes, restrictions, sources. */
function TraceDetails({ trace }: { trace: NonNullable<PlannedCourse["trace"]> }) {
  return (
    <div className="space-y-1 text-fg-secondary">
      {trace.requirements.map((r) => (
        <p key={r.requirement_code}>
          <span className="text-xs font-medium uppercase tracking-wide text-fg-muted">Fills </span>
          {r.requirement_name} -{" "}
          {r.status_source === "degree_navigator" ? "open according to your Degree Navigator audit" : "open by CoursePilot's estimate"}
        </p>
      ))}
      {trace.requirements.length === 0 && trace.unlocks_for.length > 0 && (
        <p>
          <span className="text-xs font-medium uppercase tracking-wide text-fg-muted">Needed for </span>
          {trace.unlocks_for.join(", ")}
        </p>
      )}
      {trace.core_codes && (
        <p>
          <span className="text-xs font-medium uppercase tracking-wide text-fg-muted">Core codes </span>
          {trace.core_codes.codes.join(", ") || "none"} ({termLabel(trace.core_codes.term_code)}
          {trace.core_codes.basis === "latest_offering" ? ", latest loaded offering" : ""})
        </p>
      )}
      {trace.restriction?.message && <p>{trace.restriction.message}</p>}
      {trace.sources.length > 0 && (
        <ul className="text-2xs text-fg-muted">
          {trace.sources.map((s) => (
            <li key={`${s.fact}-${s.term_code ?? ""}`}>
              {s.fact.replace(/_/g, " ")}: {SOURCE_LABEL[s.source] ?? s.source}
              {s.term_code ? `, ${termLabel(s.term_code)}` : ""}
              {s.fetched_at ? `, retrieved ${new Date(s.fetched_at).toLocaleDateString()}` : ""}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

const COURSE_STATUS: Record<string, string> = {
  met: "met",
  in_progress: "in progress",
  below_minimum: "below the minimum grade",
  not_taken: "not taken yet",
  unknown: "can't tell from your record",
};

/** Phase 7.6: "planning toward X (not yet declared)" and what declaring takes, cited. */
function DeclarationPanel({ declaration: d }: { declaration: NonNullable<SavedPlan["result"]["declaration"]> }) {
  const byId = Object.fromEntries(d.sources.map((s) => [s.id, s]));
  return (
    <section aria-labelledby="declaration-heading" className="space-y-2 rounded-xl border border-border bg-surface-elevated p-4">
      <h2 id="declaration-heading" className="text-sm font-semibold text-fg">{d.label}</h2>
      {d.message && <p className="text-sm text-fg-secondary">{d.message}</p>}
      {d.requirements.length > 0 && (
        <ul className="space-y-2 text-sm text-fg-secondary">
          {d.requirements.map((r) => (
            <li key={r.code}>
              <p className="text-fg">{r.text}</p>
              {Object.keys(r.courses).length > 0 && (
                <p className="text-xs">
                  {Object.entries(r.courses).map(([course, status]) => `${course}: ${COURSE_STATUS[status] ?? status}`).join(" · ")}
                </p>
              )}
              <p className="text-2xs text-fg-muted">
                Source:{" "}
                {r.sources.map((id, i) => (
                  <span key={id}>
                    {i > 0 ? "; " : ""}
                    <a className="underline underline-offset-2" href={byId[id]?.url} target="_blank" rel="noreferrer">{byId[id]?.title ?? id}</a>
                  </span>
                ))}
              </p>
            </li>
          ))}
        </ul>
      )}
      {d.conflicts.map((c) => <p key={c} className="text-xs font-medium text-warning">{c}</p>)}
    </section>
  );
}

/** Title-first cards retain all authoritative evidence behind Details. */
export function courseCategory(c: PlannedCourse): string {
  if (c.requirements.some((r) => r.requirement_system === "major")) return "Major";
  if (c.requirements.some((r) => ["core", "school_core", "school"].includes(r.requirement_system))) return "School Core / gen-ed";
  return c.requirements.length ? "Electives" : "Free elective";
}
function CourseCard({ course: c, actions }: { course: PlannedCourse; actions?: BoardActions }) {
  const state = actions?.states.get(`${c.course}@${c.term_code}`);
  return <li className={cn("min-w-0 space-y-2 rounded-lg border border-border bg-surface-elevated p-3", courseTone(c.course))} data-testid="plan-course">
    <h4 className="text-base font-semibold leading-snug [overflow-wrap:anywhere]">{c.title}</h4>
    <div className="flex flex-wrap items-center gap-2 text-xs text-fg-secondary"><span className="code">{c.course}</span><span>{Number(c.credits)} credits</span>{state && <StateLine state={state.state} reason={state.locked_reason} />}{!state && c.role === "anchor" && <span><Lock aria-hidden className="inline size-3" /> Anchor</span>}{c.after_stage_label && <span className="rounded border border-warning/40 px-1.5 py-0.5 text-warning" data-testid="after-stage">Planned {c.after_stage_label}</span>}</div>
    <div className="flex flex-wrap items-center justify-between gap-2"><span className="min-w-0 rounded bg-surface-hover px-2 py-1 text-xs [overflow-wrap:anywhere]">{c.requirements[0]?.requirement_name ?? "Requirement coverage not mapped"}</span><span className="text-xs text-fg-muted">{c.workload?.delivery.some((d) => d === "async" || d === "online") ? <Monitor aria-hidden className="mr-1 inline size-3" /> : <MapPin aria-hidden className="mr-1 inline size-3" />}{c.workload ? deliveryText(c.workload.delivery) : "Delivery unknown"}</span></div>
    <div className="flex flex-wrap items-start justify-between gap-2"><details className="min-w-0 flex-1 text-xs text-fg-secondary"><summary className="cursor-pointer py-2">Details</summary>
      {topicsText(c) && <p>{topicsText(c)}</p>}
      {c.role_reason && <p>{c.role_reason}</p>}
      {c.workload && <p>Workload {Number(c.workload.weight)} ({workloadBreakdown(c.workload)})</p>}
      {offeringLabel(c.offering) && <p>{offeringLabel(c.offering)}</p>}
      {c.requirements.slice(1).map((r) => <p key={r.requirement_code}>Also fills {r.requirement_name}</p>)}
      {(c.notes ?? []).map((n) => <p key={n}>{n}</p>)}<WhyHere course={c} />
    </details>{actions && <Button size="sm" variant="ghost" onClick={() => actions.onOptions(c.course, c.term_code)} aria-label={`Options for ${c.title}`}><SlidersHorizontal aria-hidden className="size-4" />Options</Button>}</div>
  </li>;
}

export function SemesterCard({ term, scheduleHref, note, maxCredits, actions, open = true, choices }: {
  term: PlanTerm; scheduleHref: string | null; note?: string; maxCredits?: number; actions?: BoardActions; open?: boolean; choices?: React.ReactNode;
}) {
  const locked = actions?.lockedTerms.includes(term.term_code) ?? false;
  const electives = Number(term.free_elective_credits ?? 0);
  const credits = Number(term.credits) + electives;
  const groups = ["Major", "School Core / gen-ed", "Electives", "Free elective"];
  return <details open={open} className="min-w-0 rounded-xl border border-border bg-surface p-3" data-testid="semester-section">
    <summary className="cursor-pointer space-y-2 rounded p-1 marker:text-fg-muted">
      <span className="text-lg font-bold" id={`term-${term.term_code}`}>{term.label}</span>
      <span className="ml-2 text-xs text-fg-secondary">{credits} credits / {term.workload?.major_related ?? term.courses.filter((c) => courseCategory(c) === "Major").length} major courses</span>
      {electives > 0 && <span className="block text-xs text-fg-muted">Includes {Number(term.credits)} course credits + {electiveSlotText(electives)}</span>}
      {term.workload ? <Progress value={Number(term.workload.total)} max={Number(term.workload.budget) || 1} label={`Workload ${Number(term.workload.total)} of ${Number(term.workload.budget)}`} className="h-1.5" /> : maxCredits ? <Progress value={credits} max={maxCredits} label={`${credits} of ${maxCredits} credits`} className="h-1.5" /> : null}
      {term.workload?.fit && <span className="block text-xs text-fg-secondary">{term.workload.fit}</span>}
    </summary>
    <div className="mt-3 space-y-4">
      {choices}
      <div className="flex flex-wrap gap-1">{actions?.onAddCourse && <Button size="sm" variant="primary" onClick={() => actions.onAddCourse?.(term.term_code)}>Build / change this semester</Button>}
        {scheduleHref && <Link href={scheduleHref} className={buttonVariants({ variant: "ghost", size: "sm" })}><CalendarDays aria-hidden className="size-4" />Compare schedules</Link>}
        {actions && <><Button size="sm" variant="ghost" onClick={() => actions.changes.request({ kind: locked ? "unlock_term" : "lock_term", term_code: term.term_code })}>{locked ? <LockOpen aria-hidden className="size-3" /> : <Lock aria-hidden className="size-3" />}{locked ? "Unlock semester" : "Lock semester"}</Button><Button size="sm" variant="ghost" onClick={() => actions.changes.request({ kind: "lighten_term", term_code: term.term_code })}><Feather aria-hidden className="size-3" />Lighten</Button></>}
      </div>
      {(note || term.workload) && <details className="text-xs text-fg-secondary"><summary className="cursor-pointer py-1">Semester details</summary>{note && <p>{note}</p>}{term.workload && <><p>Workload {Number(term.workload.total)} of {Number(term.workload.budget)} / {Number(term.workload.credit_floor)}-{Number(term.workload.credit_max)} credits / {Number(term.workload.contact_hours)} contact hrs/week</p>{[...term.workload.notes, ...(term.workload.shortfalls ?? [])].map((n) => <p key={n}>{n}</p>)}</>}</details>}
      {groups.map((g) => { const courses = term.courses.filter((c) => courseCategory(c) === g); return courses.length || (g === "Free elective" && electives > 0) ? <section key={g} aria-label={g}><h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-fg-muted">{g}</h3><ul className="grid min-w-0 gap-2 md:grid-cols-2 xl:grid-cols-3">{courses.map((c) => <CourseCard key={c.course} course={c} actions={actions} />)}{g === "Free elective" && electives > 0 && <li className="rounded border border-dashed border-border p-3 text-sm text-fg-secondary">Free elective: {electiveSlotText(electives)} - choose a degree-credit course ({creditsText(electives)} toward the degree total).</li>}</ul></section> : null; })}
    </div>
  </details>;
}

function BlockedList({ courses }: { courses: BlockedCourse[] }) {
  return (
    <ul className="divide-y divide-border rounded border border-border bg-surface-elevated">
      {courses.map((b) => (
        <li key={`${b.issue_code}-${b.course}-${b.requirement_code}`} className="space-y-1 p-3 text-sm">
          <p>
            <span className="code font-medium text-fg">{b.course}</span>{" "}
            <span className="text-fg-secondary [overflow-wrap:anywhere]">{b.title ?? ""}</span>
          </p>
          <ul className="text-fg-secondary">
            {b.reasons.map((r) => <li key={r}>{blockedReasonText(r)}</li>)}
          </ul>
          {b.prerequisite_text && (
            <p className="text-fg-secondary [overflow-wrap:anywhere]">
              <span className="text-fg-muted">Rutgers prerequisite{b.prerequisite_term ? ` (${termLabel(b.prerequisite_term)})` : ""}: </span>
              {b.prerequisite_text}
            </p>
          )}
        </li>
      ))}
    </ul>
  );
}

export function Blockers({ issues, blocked }: { issues: PlanningIssue[]; blocked: BlockedCourse[] }) {
  if (issues.length === 0) return null;
  const unknown = blocked.filter((b) => b.issue_code === "ELIGIBILITY_UNKNOWN");
  const unplaceable = blocked.filter((b) => b.issue_code !== "ELIGIBILITY_UNKNOWN");
  return (
    <section aria-labelledby="blockers-heading" className="space-y-3">
      <h2 id="blockers-heading" className="text-base font-semibold text-fg">What the plan couldn&apos;t do</h2>
      <ul className="space-y-2">
        {issues.map((issue, i) => {
          const sev = PLAN_SEVERITY[issue.severity];
          const Icon = STATUS_ICON[sev.icon];
          return (
            <li key={`${issue.code}-${issue.requirement_code}-${issue.course}-${i}`} className="flex gap-3 rounded border border-border bg-surface-elevated p-3 text-sm">
              <Icon aria-hidden className="mt-0.5 size-4 shrink-0 text-fg-muted" />
              <div className="min-w-0 space-y-0.5">
                <p className="text-xs font-medium uppercase tracking-wide text-fg-muted">{sev.label}</p>
                <p className="text-fg [overflow-wrap:anywhere]">{issue.message}</p>
              </div>
            </li>
          );
        })}
      </ul>
      {unknown.length > 0 && (
        <div className="space-y-2">
          <h3 className="text-sm font-semibold text-fg">Courses that need confirmation</h3>
          <p className="text-sm text-fg-secondary">
            CoursePilot can&apos;t evaluate these prerequisites automatically, so it did not place these
            courses. That is not a finding that you are ineligible - confirm with the department or an
            advisor.
          </p>
          <BlockedList courses={unknown} />
        </div>
      )}
      {unplaceable.length > 0 && (
        <div className="space-y-2">
          <h3 className="text-sm font-semibold text-fg">Courses without usable offering evidence</h3>
          <BlockedList courses={unplaceable} />
        </div>
      )}
    </section>
  );
}

export function PlanView({
  plan,
  onRegenerate,
  regenerating,
  onApplyLight,
  applyingLight = false,
  actions,
  choices,
}: {
  choices?: React.ReactNode;
  plan: SavedPlan;
  onRegenerate: () => void;
  regenerating: boolean;
  /** Correctness fixes item 10: apply the "Consider Light" suggestion to its semester. */
  onApplyLight?: (suggestion: PlanObservation) => void;
  applyingLight?: boolean;
  /** Prompt 11: options, locks and undo (absent on read-only views). */
  actions?: BoardActions;
}) {
  const r = plan.result;
  // Task 3: while Degree Navigator says a requirement CoursePilot counts as met
  // is NOT met, the plan never reads as covering everything or on track.
  const dnNotMet = r.issues.filter((i) => i.code === "DN_SAYS_NOT_MET");
  const status = dnNotMet.length > 0 && r.status !== "partial" ? DN_REVIEW : PLAN_STATUS[r.status];
  const earlier = (r.observations ?? []).find((o) => o.code === "earlier_completion_possible");
  const lighter = (r.observations ?? []).find((o) => o.code === "consider_light_term");
  return (
    <div className="space-y-6">
      <StaleNotice plan={plan} onRegenerate={onRegenerate} regenerating={regenerating} />
      {actions && <UndoBar plan={plan} changes={actions.changes} />}
      {(plan.needs_reselection ?? []).length > 0 && (
        <Alert tone="warning" title="Some of your choices no longer fit">
          <p>CoursePilot kept them and didn&apos;t pick anything else for you. Choose again from each course&apos;s Options:</p>
          <ul className="mt-1 list-disc pl-5">
            {(plan.needs_reselection ?? []).map((r) => <li key={r.course}>{r.message}</li>)}
          </ul>
        </Alert>
      )}
      {dnNotMet.length > 0 && (
        <Alert tone="warning" title={DN_NOT_MET_MESSAGE}>
          <ul className="list-disc pl-5">
            {dnNotMet.map((i) => <li key={i.requirement_code ?? i.message}>{String(i.details.name ?? i.requirement_code)}</li>)}
          </ul>
          <span className="block">This plan counts {dnNotMet.length === 1 ? "it" : "them"} as met (CoursePilot&apos;s audit) and does not guess which course Degree Navigator is missing.</span>
        </Alert>
      )}
      <section aria-labelledby="plan-summary" className="space-y-2 animate-cp-rise rounded-xl border border-border bg-surface-elevated p-4">
        <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
          <div className="space-y-1">
            <h2 id="plan-summary" className="text-sm font-semibold text-fg">
              {r.terms.length} {r.terms.length === 1 ? "semester" : "semesters"} from {termLabel(plan.start_term)}
            </h2>
            <p className="text-sm text-fg-secondary">{status.meaning}</p>
            {r.graduation_target && (
              <p className="text-sm text-fg-secondary">
                Planned to graduate by {termLabel(r.graduation_target.term_code)} ({r.graduation_target.basis}).
              </p>
            )}
            {r.graduation_target?.conflict && (
              <p className="text-sm font-medium text-warning">Needs review: {r.graduation_target.conflict}</p>
            )}
            {r.degree_credits && (
              <p className="tabular text-sm text-fg-secondary">
                Degree credits: {Number(r.degree_credits.total)} of {Number(r.degree_credits.required)}
                {" "}({Number(r.degree_credits.completed) + Number(r.degree_credits.in_progress)} on your record
                {" "}+ {Number(r.degree_credits.planned_courses)} in planned courses
                {" "}+ {Number(r.degree_credits.planned_free_electives)} free electives)
                {r.degree_credits.met ? "." : " - short of the total."}{" "}
                <a href={r.degree_credits.source_url} className="underline underline-offset-2" target="_blank" rel="noreferrer">
                  Source
                </a>
              </p>
            )}
            {r.placement && (
              <p className="text-sm text-fg-secondary">
                Based on your placement into {placementLabel(r.placement)}.{" "}
                <Link href="/profile" className="underline underline-offset-2">Wrong? Change it</Link>
              </p>
            )}
          </div>
          <StateBadge state={status} className="shrink-0" />
        </div>
        <p className="text-xs text-fg-muted">
          Generated {new Date(plan.updated_at).toLocaleString()} by planning engine {r.metadata.planning_engine_version}.{" "}
          {plan.constraints.pace ? `${PACE_LABEL[plan.constraints.pace] ?? plan.constraints.pace} pace, ` : ""}
          {loadText(plan.constraints)} - your CoursePilot planning preferences, not Rutgers rules.
        </p>
      </section>

      {r.declaration && <DeclarationPanel declaration={r.declaration} />}

      {r.school_core?.status === "unknown" && (
        <Alert tone="warning" title="Core status unknown">{r.school_core.message}</Alert>
      )}

      {earlier && (
        <Alert tone="info" title="You may be able to finish the modeled requirements sooner"
          action={<Link href="/plan/personalize" className={buttonVariants({ variant: "secondary", size: "sm" })}>Review pace</Link>}>
          {earlier.message}
        </Alert>
      )}

      {lighter && (
        <Alert tone="info" title={`Consider Light for ${lighter.term_code ? termLabel(lighter.term_code) : "one semester"}`}
          action={onApplyLight ? (
            <Button size="sm" variant="secondary" loading={applyingLight} onClick={() => onApplyLight(lighter)}>
              Use Light for {lighter.term_code ? termLabel(lighter.term_code) : "that semester"}
            </Button>
          ) : (
            <Link href="/plan/personalize" className={buttonVariants({ variant: "secondary", size: "sm" })}>Review that semester</Link>
          )}>
          {lighter.message}
        </Alert>
      )}

      {r.terms.length > 0 && (
        // The board: semesters side by side, scrolling sideways inside itself
        // (never the page); stacked on phones.
        <div className="grid min-w-0 gap-4">
          {r.terms.map((t, index) => (
            <SemesterCard key={t.term_code} term={t} scheduleHref={`/schedule?term=${t.term_code}`}
              actions={actions} open={index === 0} choices={index === 0 ? choices : undefined}
              maxCredits={Number(plan.constraints.max_credits_per_term) || undefined}
              note={(r.observations ?? []).find((o) => o.code === "light_term" && o.term_code === t.term_code)?.message} />
          ))}
        </div>
      )}

      <Blockers issues={r.issues} blocked={plan.blocked_courses} />

      {r.assumptions.length > 0 && (
        <section aria-labelledby="assumptions-heading" className="space-y-1">
          <h2 id="assumptions-heading" className="text-sm font-semibold text-fg">Assumptions</h2>
          <ul className="space-y-1 text-sm text-fg-secondary">
            {r.assumptions.map((a) => <li key={a.code}>{a.message}</li>)}
          </ul>
        </section>
      )}
    </div>
  );
}
