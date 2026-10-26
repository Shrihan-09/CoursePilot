import { CalendarDays, ChevronRight, RefreshCw } from "lucide-react";
import Link from "next/link";

import { StateBadge, STATUS_ICON } from "@/components/academic/status";
import { Button, buttonVariants } from "@/components/ui/button";
import { Alert, Progress } from "@/components/ui/feedback";
import {
  blockedReasonText,
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
import type { BlockedCourse, PlannedCourse, PlanningIssue, PlanTerm, SavedPlan } from "@/types/plan";

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
            <StateBadge state={eligibility} />
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
      </div>
    </details>
  );
}

/** One planned course as a board card: code + credits chip, title, # requirement tags. */
function CourseCard({ course: c, index }: { course: PlannedCourse; index: number }) {
  const tone = courseTone(c.course);
  const toward = [...new Set(c.requirements.map((r) => r.requirement_name))];
  return (
    <li
      className={cn("cp-lift animate-cp-rise space-y-2 rounded-lg border border-border bg-surface-elevated p-3", tone)}
      style={{ animationDelay: `${Math.min(index, 8) * 45}ms` }}
    >
      <div className="flex items-start justify-between gap-2">
        <span className="code text-sm font-semibold text-fg">{c.course}</span>
        <span className="tabular shrink-0 rounded-md bg-surface-hover px-1.5 py-0.5 text-2xs font-medium text-fg-secondary">
          {c.credits} cr
        </span>
      </div>
      <p className="text-sm leading-snug text-fg [overflow-wrap:anywhere]">
        {c.title}
        {topicsText(c) && <span className="text-fg-secondary"> — {topicsText(c)}</span>}
      </p>
      {offeringLabel(c.offering) && (
        <p className={cn("text-xs font-medium",
          c.offering.kind === "confirmed_in_term" ? "text-success" : "text-warning")}>
          {offeringLabel(c.offering)}
        </p>
      )}
      {c.role && (
        <p className="text-xs text-fg-secondary">
          <span className="font-medium text-fg">{ROLE_LABEL[c.role] ?? c.role}</span>
          {c.role_reason ? <span> — {c.role_reason}</span> : null}
        </p>
      )}
      {c.workload && (
        <p className="tabular text-2xs text-fg-muted" title={workloadBreakdown(c.workload)}>
          Workload {Number(c.workload.weight)} ({workloadBreakdown(c.workload)})
          {c.workload.contact_hours ? ` · ${Number(c.workload.contact_hours)} contact hrs/week` : ""}
          {` · ${deliveryText(c.workload.delivery)}`}
        </p>
      )}
      {toward.length > 0 && (
        <p className="flex flex-wrap justify-end gap-x-2 gap-y-0.5 text-xs text-fg-secondary">
          <span className="sr-only">Toward: </span>
          {toward.map((name) => (
            <span key={name} className="[overflow-wrap:anywhere]">
              <span aria-hidden className="font-semibold text-[rgb(var(--tone))]"># </span>
              {name}
            </span>
          ))}
        </p>
      )}
      {(c.notes ?? []).map((note) => (
        <p key={note} className={cn("text-xs", note.startsWith("NEEDS REVIEW") ? "font-medium text-warning" : "text-fg-secondary")}>
          {note}
        </p>
      ))}
      <WhyHere course={c} />
    </li>
  );
}

export function SemesterCard({
  term,
  scheduleHref,
  note,
  maxCredits,
}: {
  term: PlanTerm;
  scheduleHref: string | null;
  note?: string;
  /** The student's hard maximum: the column's load bar is measured against it. */
  maxCredits?: number;
}) {
  const credits = Number(term.credits);
  return (
    <section
      aria-labelledby={`term-${term.term_code}`}
      className="flex w-full shrink-0 snap-start flex-col gap-3 rounded-xl bg-surface p-3 md:w-[19rem]"
    >
      <header className="space-y-2 px-1 pt-1">
        <div>
          <h3 id={`term-${term.term_code}`} className="text-lg font-bold tracking-tight text-fg">{term.label}</h3>
          <p className="tabular text-xs text-fg-muted">{term.courses.length} courses · {term.credits} credits</p>
          {term.workload && (
            <p className="tabular text-xs text-fg-secondary">
              {WORKLOAD_LEVEL_LABEL[term.workload.level] ?? term.workload.level}: workload {Number(term.workload.total)} of{" "}
              {Number(term.workload.budget)} · {Number(term.workload.contact_hours)} contact hrs/week ·{" "}
              {Number(term.workload.credit_floor)}-{Number(term.workload.credit_max)} credits
            </p>
          )}
          {term.workload?.notes.map((n) => (
            <p key={n} className="text-xs text-warning">{n}</p>
          ))}
        </div>
        {maxCredits ? (
          <Progress value={Math.min(credits, maxCredits)} max={maxCredits}
            label={`${term.label}: ${term.credits} of at most ${maxCredits} credits`} className="h-1" />
        ) : null}
      </header>
      {scheduleHref && (
        <Link href={scheduleHref}
          className="group flex min-h-10 items-center gap-2 rounded-lg border border-border bg-surface-elevated px-3 text-sm text-fg-secondary transition-colors hover:border-border-strong hover:text-fg">
          <CalendarDays aria-hidden className="size-4 text-fg-muted transition-colors group-hover:text-primary" />
          <span className="flex-1">Build schedule for {term.label}</span>
          <span className="tabular rounded-md bg-surface-hover px-1.5 py-0.5 text-2xs font-medium">{term.credits} cr</span>
        </Link>
      )}
      {note && <p className="px-1 text-xs text-fg-secondary">{note}</p>}
      <ul className="space-y-2">
        {term.courses.map((c, i) => <CourseCard key={c.course} course={c} index={i} />)}
      </ul>
    </section>
  );
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
}: {
  plan: SavedPlan;
  onRegenerate: () => void;
  regenerating: boolean;
}) {
  const r = plan.result;
  const status = PLAN_STATUS[r.status];
  const earlier = (r.observations ?? []).find((o) => o.code === "earlier_completion_possible");
  return (
    <div className="space-y-6">
      <StaleNotice plan={plan} onRegenerate={onRegenerate} regenerating={regenerating} />
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
          {plan.constraints.target_credits_per_term ? `aiming for ${String(plan.constraints.target_credits_per_term)} credits, ` : ""}
          never more than {String(plan.constraints.max_credits_per_term)} credits or {plan.constraints.max_courses_per_term} courses a
          semester - your CoursePilot planning preferences, not Rutgers rules.
        </p>
      </section>

      {earlier && (
        <Alert tone="info" title="You may be able to finish the modeled requirements sooner"
          action={<Link href="/plan/personalize" className={buttonVariants({ variant: "secondary", size: "sm" })}>Review pace</Link>}>
          {earlier.message}
        </Alert>
      )}

      {r.terms.length > 0 && (
        // The board: semesters side by side, scrolling sideways inside itself
        // (never the page); stacked on phones.
        <div className="-mx-1 flex snap-x flex-col gap-4 px-1 pb-2 md:flex-row md:items-start md:overflow-x-auto">
          {r.terms.map((t) => (
            <SemesterCard key={t.term_code} term={t} scheduleHref={`/schedule?term=${t.term_code}`}
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
