"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowRight, BadgeCheck, CalendarDays, Clock3, GraduationCap, Map as MapIcon } from "lucide-react";
import Link from "next/link";

import { StateBadge } from "@/components/academic/status";
import { buttonVariants } from "@/components/ui/button";
import { Alert, ErrorState, PageHeader, Skeleton, StatusBadge } from "@/components/ui/feedback";
import { api } from "@/lib/api";
import { AUDIT_STATUS, PLAN_STATUS, termLabel } from "@/lib/academic/vocabulary";
import type { SavedPlan } from "@/types/plan";

/**
 * Home: concise summaries of what the engines stated - program and review
 * status, the audit's overall result and credit figures, the saved plan and
 * its freshness - and one suggested next step. It computes nothing academic;
 * the next step is chosen from those states only (set up -> plan -> schedule).
 */

function nextStep(state: string, plan: SavedPlan | null | undefined): { title: string; body: string; href: string; cta: string } {
  if (state !== "complete") {
    return { title: "Finish setting up", body: "Add your courses so CoursePilot can show your progress.", href: "/onboarding", cta: "Continue setup" };
  }
  if (!plan) {
    return { title: "Build your semester plan", body: "Lay out the courses that complete your remaining requirements.", href: "/plan", cta: "Build a plan" };
  }
  if (plan.freshness.state === "stale") {
    return { title: "Your plan is out of date", body: "Something it was built from has changed. Regenerate it before scheduling.", href: "/plan", cta: "Review plan" };
  }
  const first = plan.result.terms[0];
  if (first) {
    return { title: `Build your ${first.label} schedule`, body: `${first.courses.length} planned courses, ${first.credits} credits.`, href: `/schedule?term=${first.term_code}`, cta: "Build schedule" };
  }
  return { title: "Review your degree", body: "See each requirement and what counts toward it.", href: "/degree", cta: "View degree" };
}

export default function HomePage() {
  const status = useQuery({ queryKey: ["onboarding"], queryFn: api.onboarding });
  const audit = useQuery({ queryKey: ["audit"], queryFn: api.audit, staleTime: 0 });
  const plan = useQuery({ queryKey: ["plan"], queryFn: api.currentPlan, staleTime: 0 });

  if (status.isPending) {
    return (
      <div className="space-y-6" aria-label="Loading">
        <Skeleton className="h-10 w-64" />
        <Skeleton className="h-24" />
        <div className="grid gap-4 sm:grid-cols-2">{[0, 1].map((i) => <Skeleton key={i} className="h-40" />)}</div>
      </div>
    );
  }
  if (status.isError || !status.data.profile) {
    return <ErrorState onRetry={() => status.refetch()}>Your home page couldn&apos;t be loaded.</ErrorState>;
  }
  const { profile, history_counts: c, state } = status.data;
  const program = profile.program;
  const saved = plan.data?.plan;
  const step = nextStep(state, plan.isSuccess ? saved : undefined);

  return (
    <div className="space-y-8">
      <PageHeader eyebrow="Home" title="Your plan at a glance" />

      {plan.isSuccess && (
        <section aria-labelledby="next-heading" className="flex flex-col gap-3 rounded-lg border border-primary/30 bg-primary-subtle p-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="space-y-0.5">
            <p className="text-xs font-medium uppercase tracking-wide text-primary-text">Next step</p>
            <h2 id="next-heading" className="text-base font-semibold text-fg">{step.title}</h2>
            <p className="text-sm text-fg-secondary">{step.body}</p>
          </div>
          <Link href={step.href} className={buttonVariants({ variant: "primary", size: "sm" })}>
            {step.cta} <ArrowRight aria-hidden className="size-4" />
          </Link>
        </section>
      )}

      <section aria-labelledby="program-heading" className="space-y-4 rounded-lg border border-border bg-surface-elevated p-4 sm:p-5">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0 space-y-1">
            <p className="text-xs font-medium uppercase tracking-wide text-fg-muted">Program</p>
            <h2 id="program-heading" className="text-lg font-semibold text-fg [overflow-wrap:anywhere]">{program.program_name}</h2>
            <p className="text-sm text-fg-secondary">{program.degree_type} · {program.school_code} · Catalog {program.catalog_year}</p>
          </div>
          {program.support_status === "supported" ? (
            <StatusBadge tone="success" icon={BadgeCheck} label="Requirements reviewed" />
          ) : (
            <StatusBadge tone="warning" icon={Clock3} label="Requirements pending review" />
          )}
        </div>
        {program.warnings.map((w) => (
          <Alert key={w.code} tone={w.severity === "warning" ? "warning" : "info"}>{w.message}</Alert>
        ))}
      </section>

      <div className="grid gap-4 lg:grid-cols-2">
        <section aria-labelledby="degree-heading" className="space-y-3 rounded-lg border border-border bg-surface-elevated p-4">
          <div className="flex items-center justify-between gap-2">
            <h2 id="degree-heading" className="flex items-center gap-2 text-sm font-semibold text-fg"><GraduationCap aria-hidden className="size-4 text-fg-muted" /> Degree</h2>
            <Link href="/degree" className="inline-flex min-h-10 items-center text-sm font-medium text-primary-text hover:underline">View degree</Link>
          </div>
          {audit.isPending ? <Skeleton className="h-16" /> : audit.isError ? (
            <p className="text-sm text-fg-muted">Degree progress is unavailable right now.</p>
          ) : (
            <>
              <StateBadge state={AUDIT_STATUS[audit.data.status]} />
              {audit.data.credits_required_min !== null && (
                <p className="text-sm text-fg-secondary">
                  <span className="tabular font-semibold text-fg">{audit.data.credits_applicable_to_degree}</span> of{" "}
                  <span className="tabular">{audit.data.credits_required_min}</span> degree credits counted
                  {Number(audit.data.credits_in_progress) > 0 && <>, <span className="tabular">{audit.data.credits_in_progress}</span> in progress</>}
                </p>
              )}
            </>
          )}
        </section>

        <section aria-labelledby="plan-heading" className="space-y-3 rounded-lg border border-border bg-surface-elevated p-4">
          <div className="flex items-center justify-between gap-2">
            <h2 id="plan-heading" className="flex items-center gap-2 text-sm font-semibold text-fg"><MapIcon aria-hidden className="size-4 text-fg-muted" /> Plan</h2>
            <Link href="/plan" className="inline-flex min-h-10 items-center text-sm font-medium text-primary-text hover:underline">{saved ? "View plan" : "Build a plan"}</Link>
          </div>
          {plan.isPending ? <Skeleton className="h-16" /> : plan.isError ? (
            <p className="text-sm text-fg-muted">Your plan is unavailable right now.</p>
          ) : !saved ? (
            <p className="text-sm text-fg-muted">No plan yet.</p>
          ) : (
            <>
              <div className="flex flex-wrap gap-2">
                <StateBadge state={PLAN_STATUS[saved.result.status]} />
                {saved.freshness.state === "stale" && <StatusBadge tone="warning" icon={Clock3} label="Out of date" />}
              </div>
              <p className="text-sm text-fg-secondary">
                {saved.result.terms.length} semesters from {termLabel(saved.start_term)}
                {saved.result.terms[0] && <> · next: {saved.result.terms[0].label}, {saved.result.terms[0].courses.length} courses</>}
              </p>
              {saved.result.terms[0] && (
                <Link href={`/schedule?term=${saved.result.terms[0].term_code}`} className={buttonVariants({ variant: "secondary", size: "sm" })}>
                  <CalendarDays aria-hidden className="size-4" /> Build {saved.result.terms[0].label} schedule
                </Link>
              )}
            </>
          )}
        </section>
      </div>

      <section aria-labelledby="record-heading" className="space-y-3">
        <h2 id="record-heading" className="text-sm font-semibold text-fg">Your record</h2>
        <dl className="grid grid-cols-3 gap-px overflow-hidden rounded-lg border border-border bg-border">
          {([["Completed", c.completed ?? 0], ["In progress", c.in_progress ?? 0], ["Planned", c.planned ?? 0]] as const).map(([label, n]) => (
            <div key={label} className="bg-surface-elevated p-3 sm:p-4">
              <dt className="text-xs text-fg-muted">{label}</dt>
              <dd className="tabular text-2xl font-semibold text-fg">{n}</dd>
            </div>
          ))}
        </dl>
        {profile.transcript_reported_gpa && (
          <p className="text-sm text-fg-secondary">
            Cumulative GPA <span className="tabular font-medium text-fg">{profile.transcript_reported_gpa.value}</span>{" "}
            <span className="text-fg-muted">- as stated on the transcript you uploaded, not calculated by CoursePilot.</span>
          </p>
        )}
      </section>

      <p className="text-xs text-fg-muted">
        Coming later: course search, notifications and the CoursePilot assistant.
      </p>
    </div>
  );
}
