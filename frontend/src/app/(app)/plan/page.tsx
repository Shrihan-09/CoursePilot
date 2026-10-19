"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Map as MapIcon, SlidersHorizontal, Sparkles } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { GenerationAnimation } from "@/components/brand/generation";
import { Button, buttonVariants } from "@/components/ui/button";
import { Alert, EmptyState, ErrorState, PageHeader, Skeleton } from "@/components/ui/feedback";
import { Field, Select } from "@/components/ui/field";
import { PlanView } from "@/features/plan/plan-result";
import { ProfileSummary, ReadinessList } from "@/features/planning/profile-summary";
import { ApiError, api } from "@/lib/api";
import { termLabel } from "@/lib/academic/vocabulary";
import type { SavedPlan } from "@/types/plan";

/**
 * Plan: "What should I take, and in what order?"
 *
 * Phase 7.2.1: the SERVER decides whether a plan can be built (readiness) and
 * which starting semesters are valid (always future terms); the load settings
 * come only from the student's confirmed planning preferences. This page
 * never edits a plan: changing anything means asking the engine for a new one.
 */

function StartTermField({ value, onChange, options, latest }: {
  value: string; onChange: (v: string) => void; options: { term_code: string; label: string }[]; latest: string | null;
}) {
  return (
    <Field
      label="Plan starting semester"
      hint={`The first semester CoursePilot plans courses for${latest ? `. Your record runs through ${termLabel(latest)}` : ""}. Plans always start in a future semester.`}
    >
      {({ id, describedBy }) => (
        <Select id={id} aria-describedby={describedBy} value={value} onChange={(e) => onChange(e.target.value)}>
          {options.map((o) => <option key={o.term_code} value={o.term_code}>{o.label}</option>)}
        </Select>
      )}
    </Field>
  );
}

export default function PlanPage() {
  const client = useQueryClient();
  const readiness = useQuery({ queryKey: ["readiness"], queryFn: api.readiness, staleTime: 0 });
  const context = useQuery({ queryKey: ["planning-context"], queryFn: api.planningContext, staleTime: 0 });
  const current = useQuery({ queryKey: ["plan"], queryFn: api.currentPlan, staleTime: 0 });
  const prefs = useQuery({ queryKey: ["planning-profile"], queryFn: api.planningProfile, staleTime: 0 });
  const profile = useQuery({ queryKey: ["profile"], queryFn: api.profile });
  const [start, setStart] = useState<string | null>(null);
  const [resolved, setResolved] = useState(true);

  const generate = useMutation({
    mutationFn: (startTerm?: string) => api.savePlan(startTerm),
    onMutate: () => setResolved(false),
    onSuccess: (plan: SavedPlan) => {
      client.setQueryData(["plan"], { plan });
      void client.invalidateQueries({ queryKey: ["readiness"] });
    },
    onError: () => setResolved(true),
  });

  if (readiness.isPending || context.isPending || current.isPending || prefs.isPending) {
    return (
      <div className="space-y-6" aria-label="Loading your plan">
        <Skeleton className="h-10 w-64" />
        <Skeleton className="h-28" />
        {[0, 1].map((i) => <Skeleton key={i} className="h-48" />)}
      </div>
    );
  }
  if (readiness.isError || context.isError || current.isError || prefs.isError) {
    const err = (readiness.error ?? context.error ?? current.error ?? prefs.error) as unknown;
    return <ErrorState title="Your plan couldn't be loaded" requestId={err instanceof ApiError ? err.requestId : null}
      onRetry={() => { void readiness.refetch(); void context.refetch(); void current.refetch(); void prefs.refetch(); }} />;
  }

  // Substantial generation (1-8 s): one honest stage - the engine reports no progress.
  if (generate.isPending || !resolved) {
    return (
      <div className="py-10">
        <GenerationAnimation
          status={generate.isPending ? "generating" : "complete"}
          title="CoursePilot is building your plan"
          caption="The planning engine checks your remaining requirements, prerequisites and Rutgers offering data, then balances your semesters within your preferences. It doesn't report step-by-step progress, so none is shown."
          stages={[{ key: "engine", label: "Planning engine running", state: generate.isPending ? "active" : "done" }]}
          onResolved={() => setResolved(true)}
        />
      </div>
    );
  }

  const ctx = context.data;
  const plan = current.data.plan;
  const ready = readiness.data;
  const confirmed = prefs.data.profile;
  const startTerm = start ?? ctx.earliest_start_term;
  const error = generate.error instanceof ApiError ? generate.error : null;
  const editPrefs = (
    <Link href="/plan/personalize" className={buttonVariants({ variant: "secondary", size: "sm" })}>
      <SlidersHorizontal aria-hidden className="size-4" /> {confirmed ? "Edit preferences" : "Personalize"}
    </Link>
  );

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Plan"
        title="Your semester plan"
        description="Built by CoursePilot's planning engine from your confirmed record, your program, Rutgers data and your planning preferences. Planned courses count only once you pass them."
        actions={confirmed ? editPrefs : undefined}
      />

      {error && (
        <Alert tone="danger" title="The plan could not be generated">
          {error.status >= 500 ? "The planning engine hit a problem. Your saved plan, if any, is unchanged." : error.message}
        </Alert>
      )}

      {(ready.state === "needs_program" || ready.state === "needs_academic_history") ? (
        <EmptyState icon={MapIcon} title="Finish setting up first"
          action={<Link href="/onboarding" className={buttonVariants({ variant: "primary", size: "sm" })}>Continue setup</Link>}>
          CoursePilot plans from your confirmed academic history.
        </EmptyState>
      ) : ready.state === "needs_planning_profile" ? (
        <section aria-labelledby="personalize-heading" className="space-y-4 rounded-lg border border-border bg-surface-elevated p-4 sm:p-5">
          <div className="flex items-start gap-3">
            <Sparkles aria-hidden className="mt-0.5 size-5 shrink-0 text-primary-text" />
            <div className="space-y-1">
              <h2 id="personalize-heading" className="text-base font-semibold text-fg">Personalize your plan</h2>
              <p className="text-sm text-fg-secondary">
                Tell CoursePilot how you&apos;d like your semesters to look: your pace, how many credits to aim for,
                and whether summer or winter courses are an option. It takes about a minute.
              </p>
            </div>
          </div>
          <ReadinessList readiness={ready} />
          <Link href="/plan/personalize" className={buttonVariants({ variant: "primary", size: "sm" })}>Personalize my plan</Link>
        </section>
      ) : !plan ? (
        <section aria-labelledby="ready-heading" className="space-y-5 rounded-lg border border-border bg-surface-elevated p-4 sm:p-5">
          <h2 id="ready-heading" className="text-base font-semibold text-fg">Ready to build your plan</h2>
          <ReadinessList readiness={ready} />
          {confirmed && (
            <details className="rounded border border-border">
              <summary className="min-h-11 cursor-pointer p-3 text-sm font-medium text-fg">How CoursePilot will plan for you</summary>
              <div className="border-t border-border p-3">
                <ProfileSummary profile={confirmed} programName={profile.data?.program.program_name} />
              </div>
            </details>
          )}
          <div className="max-w-sm">
            <StartTermField value={startTerm} onChange={setStart} options={ctx.start_options} latest={ctx.latest_recorded_term} />
          </div>
          <Button variant="primary" onClick={() => generate.mutate(startTerm)}>Build my plan</Button>
        </section>
      ) : (
        <>
          <PlanView
            plan={plan}
            regenerating={generate.isPending}
            onRegenerate={() => generate.mutate(
              plan.freshness.reasons.includes("start_term_passed") ? undefined : plan.start_term)}
          />
          <details className="rounded-lg border border-border bg-surface-elevated">
            <summary className="min-h-11 cursor-pointer p-3 text-sm font-medium text-fg">Rebuild from a different semester</summary>
            <div className="flex flex-col gap-3 border-t border-border p-3 sm:flex-row sm:items-end">
              <div className="max-w-sm flex-1">
                <StartTermField value={start ?? ctx.earliest_start_term} onChange={setStart}
                  options={ctx.start_options}
                  latest={ctx.latest_recorded_term} />
              </div>
              <Button onClick={() => generate.mutate(start ?? ctx.earliest_start_term)}>Rebuild plan</Button>
            </div>
          </details>
        </>
      )}
    </div>
  );
}
