"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowRight, BadgeCheck, CalendarDays, Clock3, GraduationCap, Map } from "lucide-react";
import Link from "next/link";

import { buttonVariants } from "@/components/ui/button";
import { Alert, ErrorState, PageHeader, Skeleton, StatusBadge } from "@/components/ui/feedback";
import { api } from "@/lib/api";

/**
 * Home presents what the API states - program, its review status and
 * warnings, record counts - and computes nothing academic itself.
 */
export default function HomePage() {
  const status = useQuery({ queryKey: ["onboarding"], queryFn: api.onboarding });

  if (status.isPending) {
    return (
      <div className="space-y-6" aria-label="Loading">
        <Skeleton className="h-10 w-64" />
        <Skeleton className="h-36" />
        <div className="grid gap-4 sm:grid-cols-3">
          {[0, 1, 2].map((i) => <Skeleton key={i} className="h-24" />)}
        </div>
      </div>
    );
  }
  if (status.isError || !status.data.profile) {
    return <ErrorState onRetry={() => status.refetch()}>Your home page couldn&apos;t be loaded.</ErrorState>;
  }
  const { profile, history_counts: c, state } = status.data;
  const program = profile.program;

  return (
    <div className="space-y-8">
      <PageHeader eyebrow="Home" title="Your plan at a glance" />

      {state !== "complete" && (
        <Alert
          tone="info"
          title="Finish setting up"
          action={<Link href="/onboarding" className={buttonVariants({ variant: "secondary", size: "sm" })}>Continue</Link>}
        >
          Add your courses so CoursePilot can show your progress.
        </Alert>
      )}

      <section aria-labelledby="program-heading" className="space-y-4 rounded-lg border border-border bg-surface-elevated p-4 sm:p-5">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0 space-y-1">
            <p className="text-xs font-medium uppercase tracking-wide text-fg-muted">Program</p>
            <h2 id="program-heading" className="text-lg font-semibold text-fg [overflow-wrap:anywhere]">
              {program.program_name}
            </h2>
            <p className="text-sm text-fg-secondary">
              {program.degree_type} · {program.school_code} · Catalog {program.catalog_year}
            </p>
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

      <section aria-labelledby="record-heading" className="space-y-3">
        <h2 id="record-heading" className="text-sm font-semibold text-fg">Your record</h2>
        <dl className="grid grid-cols-3 gap-px overflow-hidden rounded-lg border border-border bg-border">
          {(
            [["Completed", c.completed ?? 0], ["In progress", c.in_progress ?? 0], ["Planned", c.planned ?? 0]] as const
          ).map(([label, n]) => (
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

      <section aria-labelledby="next-heading" className="space-y-3">
        <h2 id="next-heading" className="text-sm font-semibold text-fg">Coming to CoursePilot</h2>
        <ul className="divide-y divide-border overflow-hidden rounded-lg border border-border bg-surface-elevated">
          {[
            [GraduationCap, "Degree progress", "Requirement-by-requirement progress, with the reasoning behind each answer."],
            [Map, "Semester plans", "Multi-semester plans built by CoursePilot's planning engine."],
            [CalendarDays, "Schedules", "Conflict-free section schedules for registration."],
          ].map(([Icon, title, body]) => {
            const I = Icon as typeof Map;
            return (
              <li key={title as string} className="flex items-start gap-3 p-3 sm:p-4">
                <I aria-hidden className="mt-0.5 size-4 shrink-0 text-fg-muted" />
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium text-fg">{title as string}</p>
                  <p className="text-sm text-fg-muted">{body as string}</p>
                </div>
                <ArrowRight aria-hidden className="mt-1 size-4 text-fg-muted/50" />
              </li>
            );
          })}
        </ul>
      </section>
    </div>
  );
}
