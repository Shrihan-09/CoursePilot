"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowRight, CalendarCheck, CircleCheck, CircleHelp, Eye, GraduationCap, MessageSquareText, Sparkles } from "lucide-react";
import Link from "next/link";
import { useEffect, useId, useState } from "react";

import { buttonVariants } from "@/components/ui/button";
import { EmptyState, ErrorState, Progress, Skeleton } from "@/components/ui/feedback";
import { useAssistantPanel } from "@/features/assistant/assistant-panel";
import { ProgressRings } from "@/features/home/progress-rings";
import { useRings } from "@/features/home/use-rings";
import { GradesReminderAlert } from "@/features/setup/grades-reminder";
import { api } from "@/lib/api";
import { courseTitle } from "@/lib/academic/course-title";
import { overBudget } from "@/lib/academic/plan-text";
import { graduation, nextUp, plannedCourseCount, registrationWindow, wholeCredits } from "@/lib/home";
import { cn } from "@/lib/utils";
import type { ProgramView } from "@/types/onboarding";

/**
 * Home dashboard (UI polish B2): greeting + rings as the hero, then Next up
 * (the single primary action), This semester, Graduation, Degree and the
 * assistant. It computes nothing academic: rings, credits and statuses are the
 * engines' own numbers. The GPA is never shown here (see Profile).
 */

const RING_HREFS = { degree: "/degree#ring-degree", major: "/degree#ring-major", core: "/degree#ring-core" } as const;
const QUICK_START = ["What Core is left?", "Make my spring lighter"];

function greeting(now: Date): string {
  const h = now.getHours();
  return h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
}

/** Program name with a small "Preview" chip; the warnings are one tap away (full text on Degree). */
function ProgramLine({ program }: { program: ProgramView }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const preview = program.support_status !== "supported" || program.warnings.length > 0;
  return (
    <div className="space-y-2">
      <p className="flex flex-wrap items-center gap-2 text-sm text-fg-secondary">
        <span className="font-medium text-fg [overflow-wrap:anywhere]">{program.program_name}</span>
        <span>{program.degree_type} · Catalog {program.catalog_year}</span>
        {preview && (
          <button type="button" aria-expanded={open} aria-controls={id} onClick={() => setOpen((v) => !v)}
            className="inline-flex min-h-8 items-center gap-1 rounded-full border border-warning/40 bg-warning-subtle px-2.5 text-xs font-semibold text-warning"
            data-testid="preview-chip">
            <Eye aria-hidden className="size-3.5" />Preview
          </button>
        )}
      </p>
      {preview && (
        <div id={id} hidden={!open} className="rounded-lg border border-border bg-surface p-3 text-sm text-fg-secondary">
          <p className="font-medium text-fg">This program&apos;s requirements are a preview.</p>
          <ul className="mt-1 list-disc space-y-0.5 pl-5">
            {program.support_status !== "supported" && <li>Requirements are pending review.</li>}
            {program.warnings.map((w) => <li key={w.code}>{w.message}</li>)}
          </ul>
          <Link href="/degree" className="mt-2 inline-flex min-h-10 items-center font-medium text-primary-text underline">Full details on the Degree page</Link>
        </div>
      )}
    </div>
  );
}

function Card({ title, icon: Icon, children, action, className, testId }: {
  title: string; icon: typeof Sparkles; children: React.ReactNode; action?: React.ReactNode; className?: string; testId?: string;
}) {
  const id = useId();
  return (
    <section aria-labelledby={id} className={cn("flex min-w-0 flex-col gap-3 rounded-xl border border-border bg-surface-elevated p-4 sm:p-5", className)} data-testid={testId}>
      <div className="flex items-center justify-between gap-2">
        <h2 id={id} className="flex items-center gap-2 text-sm font-semibold text-fg"><Icon aria-hidden className="size-4 text-fg-muted" />{title}</h2>
        {action}
      </div>
      {children}
    </section>
  );
}

const linkClass = "inline-flex min-h-10 items-center text-sm font-medium text-primary-text hover:underline";

export default function HomePage() {
  const status = useQuery({ queryKey: ["onboarding"], queryFn: api.onboarding });
  const audit = useQuery({ queryKey: ["audit"], queryFn: api.audit, staleTime: 0 });
  const plan = useQuery({ queryKey: ["plan"], queryFn: api.currentPlan, staleTime: 0 });
  const readiness = useQuery({ queryKey: ["readiness"], queryFn: api.readiness, staleTime: 0 });
  const saved = plan.data?.plan ?? null;
  const first = saved?.result.terms[0];
  const choices = useQuery({ queryKey: ["plan-choices"], queryFn: api.planChoices, staleTime: 30_000, enabled: !!saved });
  const dnImports = useQuery({ queryKey: ["dn-imports"], queryFn: api.dnImports, staleTime: 60_000 });
  const schedule = useQuery({ queryKey: ["registration-schedule", first?.term_code], queryFn: () => api.registrationSchedule(first!.term_code),
    enabled: !!first, retry: false, staleTime: 3_600_000 });
  const { rings, pending: ringsPending, notMet, figures } = useRings();
  const panel = useAssistantPanel();
  const [now, setNow] = useState<Date | null>(null);
  useEffect(() => setNow(new Date()), []);

  if (status.isPending) {
    return (
      <div className="space-y-6" aria-busy="true" aria-label="Loading">
        <Skeleton className="h-10 w-64" />
        <Skeleton className="h-64" />
        <div className="grid gap-4 md:grid-cols-2">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-40" />)}</div>
      </div>
    );
  }
  if (status.isError || !status.data.profile) {
    return <ErrorState onRetry={() => status.refetch()}>Your home page couldn&apos;t be loaded.</ErrorState>;
  }
  const { profile, history_counts: c, state } = status.data;
  const program = profile.program;
  const pendingChoices = choices.data ? choices.data.questions.filter((q) => q.kind !== "already_set").length : null;
  const hasDn = dnImports.data ? dnImports.data.imports.some((i) => i.state === "confirmed" && (i.confirmed_scope ?? "primary") === "primary") : null;
  const earned = saved?.result.degree_credits ? Number(saved.result.degree_credits.completed) : null;
  const reg = now ? registrationWindow(schedule.data, earned, now) : null;
  const step = nextUp({ onboardingState: state, readiness: readiness.data?.state, plan: saved, choicesPending: pendingChoices, hasDnAudit: hasDn, registration: reg });
  const grad = graduation(saved, notMet.length);
  const creditLine = [
    figures ? `Degree: ${wholeCredits(figures.earned + figures.inProgress)} / ${wholeCredits(figures.total)}` : "Degree: —",
    audit.data?.credits_required_min ? `Major: ${wholeCredits(audit.data.credits_applicable_to_degree)} / ${wholeCredits(audit.data.credits_required_min)}` : null,
  ].filter(Boolean).join(" · ");
  const record: [string, number][] = [["Completed", c.completed ?? 0], ["In progress", c.in_progress ?? 0], ["Planned", plannedCourseCount(saved)]];

  return (
    <div className="space-y-6">
      <GradesReminderAlert />

      {/* Hero: greeting, program, rings, record */}
      <section aria-labelledby="home-title" className="cp-glow animate-cp-rise space-y-5 rounded-xl border bg-surface-elevated p-4 sm:p-6">
        <div className="space-y-1">
          <p className="text-xs font-medium uppercase tracking-wide text-fg-muted">Home</p>
          <h1 id="home-title" className="text-[1.75rem] font-bold leading-tight tracking-tight text-fg">{now ? greeting(now) : "Welcome back"}</h1>
          <ProgramLine program={program} />
        </div>
        {ringsPending ? (
          <div className="flex flex-col items-center gap-5 sm:flex-row"><Skeleton className="size-[200px] shrink-0 rounded-full" /><div className="w-full space-y-2">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-12" />)}</div></div>
        ) : rings ? (
          <ProgressRings rings={rings} hrefs={RING_HREFS} />
        ) : (
          <p className="text-sm text-fg-muted">Progress rings are unavailable right now.</p>
        )}
        {creditLine && <p className="tabular text-sm font-medium text-fg-secondary" data-testid="credit-line">{creditLine}</p>}
        <dl className="grid grid-cols-3 gap-px overflow-hidden rounded-lg border border-border bg-border" aria-label="Your record">
          {record.map(([label, n]) => (
            <div key={label} className="bg-surface-elevated px-3 py-2.5">
              <dt className="text-xs text-fg-muted">{label}</dt>
              <dd className="tabular text-xl font-semibold text-fg">{n}</dd>
            </div>
          ))}
        </dl>
      </section>

      <div className="grid gap-4 md:grid-cols-2">
        <Card title="Next up" icon={Sparkles} className="border-primary/30 md:col-span-2" testId="next-up">
          {!plan.isSuccess ? <Skeleton className="h-14" /> : <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="min-w-0 space-y-0.5">
              <p className="text-base font-semibold text-fg">{step.title}</p>
              <p className="text-sm text-fg-secondary">{step.body}</p>
            </div>
            <Link href={step.href} className={cn(buttonVariants({ variant: "primary", size: "md" }), "shrink-0")} data-kind={step.kind}>
              {step.cta} <ArrowRight aria-hidden className="size-4" />
            </Link>
          </div>}
        </Card>

        <Card title={first ? first.label : "This semester"} icon={CalendarCheck} testId="this-semester"
          action={saved ? <Link href="/plan" className={linkClass}>View plan</Link> : undefined}>
          {plan.isPending ? <Skeleton className="h-28" /> : plan.isError ? (
            <p className="text-sm text-fg-muted">Your plan is unavailable right now.</p>
          ) : !first ? (
            <EmptyState title="No semester planned yet" action={<Link href="/plan" className={linkClass}>Build a plan</Link>} />
          ) : (
            <>
              <ul className="divide-y divide-border">
                {first.courses.map((course) => (
                  <li key={course.course} className="flex items-baseline justify-between gap-3 py-2 text-sm">
                    <span className="min-w-0 [overflow-wrap:anywhere]">
                      <span className="font-medium text-fg">{courseTitle(course.title, course.course)}</span>{" "}
                      <span className="code text-2xs text-fg-muted">{course.course}</span>
                    </span>
                    <span className="tabular shrink-0 text-xs text-fg-secondary">{wholeCredits(course.credits)} cr</span>
                  </li>
                ))}
              </ul>
              <div className="space-y-1">
                <p className="tabular flex justify-between text-xs text-fg-secondary">
                  <span>{wholeCredits(Number(first.credits) + Number(first.free_elective_credits ?? 0))} credits</span>
                  {first.workload && <span className={overBudget(first.workload) !== null ? "font-semibold text-warning" : undefined}>Workload {wholeCredits(first.workload.total)} of {wholeCredits(first.workload.budget)}{overBudget(first.workload) !== null && <> · over budget by {overBudget(first.workload)}</>}</span>}
                </p>
                {first.workload && <Progress value={Number(first.workload.total)} max={Number(first.workload.budget) || 1} label={`Workload ${first.workload.total} of ${first.workload.budget}`} />}
              </div>
            </>
          )}
        </Card>

        <Card title="Graduation" icon={GraduationCap} testId="graduation">
          {plan.isPending ? <Skeleton className="h-20" /> : (
            <>
              <p className="text-2xl font-bold tracking-tight text-fg">{grad.term ?? "Not planned yet"}</p>
              {saved && (
                <p className={cn("inline-flex w-fit items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-semibold",
                  grad.onTrack ? "border-success/30 bg-success-subtle text-success" : "border-warning/40 bg-warning-subtle text-warning")}>
                  {grad.onTrack ? <CircleCheck aria-hidden className="size-3.5" /> : <CircleHelp aria-hidden className="size-3.5" />}
                  {grad.onTrack ? "On track" : "Needs review"}
                </p>
              )}
              <p className="text-sm text-fg-secondary">{grad.why}</p>
            </>
          )}
        </Card>

        <Card title="Degree" icon={GraduationCap} action={<Link href="/degree" className={linkClass}>View degree</Link>}>
          {audit.isPending ? <Skeleton className="h-16" /> : audit.isError ? (
            <p className="text-sm text-fg-muted">Degree progress is unavailable right now.</p>
          ) : (
            <p className="text-sm text-fg-secondary">
              <span className="tabular font-semibold text-fg">{wholeCredits(audit.data.credits_applicable_to_degree)}</span> of{" "}
              <span className="tabular">{wholeCredits(audit.data.credits_required_min)}</span> major credits counted
              {Number(audit.data.credits_in_progress) > 0 && <>, <span className="tabular">{wholeCredits(audit.data.credits_in_progress)}</span> in progress</>}.
            </p>
          )}
          <div className="text-sm">
            <Link href="/profile#modeled_overall" className={linkClass}>View modeled progress</Link>
            <p className="text-xs text-fg-muted">How much of each requirement group is done, with what counts and what can&apos;t be determined.</p>
          </div>
        </Card>

        <Card title="Ask the assistant" icon={MessageSquareText}>
          <p className="text-sm text-fg-secondary">Questions about your plan or progress. Changes are previews until you confirm them.</p>
          <div className="flex flex-wrap gap-2">
            {QUICK_START.map((q) => (
              <button key={q} type="button" onClick={() => panel?.open({ draft: q })}
                className="assistant-glow inline-flex min-h-10 items-center gap-1.5 rounded-full border bg-surface-elevated px-3 text-sm font-medium">
                <Sparkles aria-hidden className="size-3.5" />{q}
              </button>
            ))}
          </div>
        </Card>
      </div>
    </div>
  );
}
