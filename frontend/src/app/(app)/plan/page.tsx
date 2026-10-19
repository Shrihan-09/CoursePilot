"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Map as MapIcon, SlidersHorizontal } from "lucide-react";
import { useState } from "react";

import { GenerationAnimation } from "@/components/brand/generation";
import { Button } from "@/components/ui/button";
import { Alert, EmptyState, ErrorState, PageHeader, Skeleton } from "@/components/ui/feedback";
import { PlanView } from "@/features/plan/plan-result";
import { PlanSettings } from "@/features/plan/plan-settings";
import { ApiError, api } from "@/lib/api";
import type { PlanningConstraints, SavedPlan } from "@/types/plan";

/**
 * Plan: "What should I take, and in what order?" - the Planning Engine's
 * answer, saved for the student (POST /student/plans) and read back with its
 * freshness (GET /student/plans/current). This page never edits a plan:
 * changing anything means asking the engine for a new one.
 */
export default function PlanPage() {
  const client = useQueryClient();
  const context = useQuery({ queryKey: ["planning-context"], queryFn: api.planningContext });
  const current = useQuery({ queryKey: ["plan"], queryFn: api.currentPlan, staleTime: 0 });
  const [editing, setEditing] = useState(false);
  const [resolved, setResolved] = useState(true);

  const generate = useMutation({
    mutationFn: ({ start, constraints }: { start: string; constraints: PlanningConstraints }) =>
      api.savePlan(start, constraints),
    onMutate: () => setResolved(false),
    onSuccess: (plan: SavedPlan) => {
      client.setQueryData(["plan"], { plan });
      setEditing(false);
    },
    onError: () => setResolved(true),
  });

  if (context.isPending || current.isPending) {
    return (
      <div className="space-y-6" aria-label="Loading your plan">
        <Skeleton className="h-10 w-64" />
        <Skeleton className="h-28" />
        {[0, 1].map((i) => <Skeleton key={i} className="h-48" />)}
      </div>
    );
  }
  if (context.isError || current.isError) {
    const err = (context.error ?? current.error) as unknown;
    return (
      <ErrorState title="Your plan couldn't be loaded" onRetry={() => { void context.refetch(); void current.refetch(); }}
        requestId={err instanceof ApiError ? err.requestId : null} />
    );
  }

  const plan = current.data.plan;
  const run = (start: string, constraints: PlanningConstraints) => generate.mutate({ start, constraints });

  // Substantial generation (1-8 s): the branded state, with ONE honest stage -
  // the engine reports no intermediate progress, so none is invented.
  if (generate.isPending || !resolved) {
    return (
      <div className="py-10">
        <GenerationAnimation
          status={generate.isPending ? "generating" : "complete"}
          title="CoursePilot is building your plan"
          caption="The planning engine checks your remaining requirements, prerequisites and Rutgers offering data. It doesn't report step-by-step progress, so none is shown."
          stages={[{ key: "engine", label: "Planning engine running", state: generate.isPending ? "active" : "done" }]}
          onResolved={() => setResolved(true)}
        />
      </div>
    );
  }

  const error = generate.error instanceof ApiError ? generate.error : null;

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Plan"
        title="Your semester plan"
        description="Built by CoursePilot's planning engine from your record, your program and Rutgers data. Planned courses count only once you pass them."
        actions={plan && !editing ? (
          <Button size="sm" variant="secondary" onClick={() => setEditing(true)}>
            <SlidersHorizontal aria-hidden className="size-4" /> Change settings
          </Button>
        ) : undefined}
      />

      {error && (
        <Alert tone="danger" title="The plan could not be generated">
          {error.status >= 500 ? "The planning engine hit a problem. Your saved plan, if any, is unchanged." : error.message}
        </Alert>
      )}

      {!context.data.earliest_start_term ? (
        <EmptyState icon={MapIcon} title="No term to start from">
          CoursePilot has no recorded term for you and no Rutgers term data, so it can&apos;t choose a starting semester.
        </EmptyState>
      ) : !plan || editing ? (
        <>
          {!plan && (
            <EmptyState icon={MapIcon} title="No plan yet">
              Choose your settings and CoursePilot will lay out the courses that complete your remaining
              requirements, in an order their prerequisites allow.
            </EmptyState>
          )}
          <PlanSettings
            context={context.data}
            initial={plan ? { start_term: plan.start_term, constraints: plan.constraints } : undefined}
            submitLabel={plan ? "Regenerate plan" : "Generate plan"}
            busy={generate.isPending}
            onSubmit={run}
            onCancel={plan ? () => setEditing(false) : undefined}
          />
        </>
      ) : (
        <PlanView
          plan={plan}
          regenerating={generate.isPending}
          onRegenerate={() => run(
            plan.start_term <= (context.data.latest_recorded_term ?? "") ? context.data.earliest_start_term! : plan.start_term,
            plan.constraints,
          )}
        />
      )}
    </div>
  );
}
