import { CalendarDays, ChevronRight, RefreshCw } from "lucide-react";
import Link from "next/link";

import { StateBadge, STATUS_ICON } from "@/components/academic/status";
import { Button, buttonVariants } from "@/components/ui/button";
import { Alert } from "@/components/ui/feedback";
import {
  blockedReasonText,
  ELIGIBILITY_STATE,
  OFFERING_KIND,
  PLAN_SEVERITY,
  PLAN_STATUS,
  planReasonText,
  STALE_REASON,
  termLabel,
} from "@/lib/academic/vocabulary";
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

export function SemesterCard({ term, scheduleHref }: { term: PlanTerm; scheduleHref: string | null }) {
  return (
    <section aria-labelledby={`term-${term.term_code}`} className="rounded-lg border border-border bg-surface-elevated">
      <header className="flex flex-col gap-2 border-b border-border p-3 sm:flex-row sm:items-center sm:justify-between sm:p-4">
        <div>
          <h3 id={`term-${term.term_code}`} className="text-base font-semibold text-fg">{term.label}</h3>
          <p className="tabular text-xs text-fg-muted">{term.courses.length} courses · {term.credits} credits</p>
        </div>
        {scheduleHref && (
          <Link href={scheduleHref} className={buttonVariants({ variant: "secondary", size: "sm" })}>
            <CalendarDays aria-hidden className="size-4" /> Build schedule for {term.label}
          </Link>
        )}
      </header>
      <ul className="divide-y divide-border">
        {term.courses.map((c) => (
          <li key={c.course} className="space-y-1.5 p-3 sm:p-4">
            <div className="flex items-baseline justify-between gap-3">
              <p className="min-w-0 text-sm">
                <span className="code font-semibold text-fg">{c.course}</span>{" "}
                <span className="text-fg-secondary [overflow-wrap:anywhere]">{c.title}</span>
              </p>
              <span className="tabular shrink-0 text-xs text-fg-muted">{c.credits} cr</span>
            </div>
            {c.requirements.length > 0 && (
              <p className="text-xs text-fg-muted [overflow-wrap:anywhere]">
                Toward: {[...new Set(c.requirements.map((r) => r.requirement_name))].join(" · ")}
              </p>
            )}
            <WhyHere course={c} />
          </li>
        ))}
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
  return (
    <div className="space-y-6">
      <StaleNotice plan={plan} onRegenerate={onRegenerate} regenerating={regenerating} />
      <section aria-labelledby="plan-summary" className="space-y-2 rounded-lg border border-border bg-surface-elevated p-4">
        <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
          <div className="space-y-1">
            <h2 id="plan-summary" className="text-sm font-semibold text-fg">
              {r.terms.length} {r.terms.length === 1 ? "semester" : "semesters"} from {termLabel(plan.start_term)}
            </h2>
            <p className="text-sm text-fg-secondary">{status.meaning}</p>
          </div>
          <StateBadge state={status} className="shrink-0" />
        </div>
        <p className="text-xs text-fg-muted">
          Generated {new Date(plan.updated_at).toLocaleString()} by planning engine {r.metadata.planning_engine_version}.
          Up to {String(plan.constraints.max_credits_per_term)} credits and {plan.constraints.max_courses_per_term} courses a
          term - CoursePilot settings you chose, not Rutgers rules.
        </p>
      </section>

      {r.terms.length > 0 && (
        <div className="space-y-4">
          {r.terms.map((t) => (
            <SemesterCard key={t.term_code} term={t} scheduleHref={`/schedule?term=${t.term_code}`} />
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
