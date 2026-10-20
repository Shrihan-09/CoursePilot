"use client";
import { RequirementNode, evidenceFor } from "@/features/degree/requirement-tree";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Alert, ErrorState, Progress, Skeleton } from "@/components/ui/feedback";

/** Minimal contract proof. The backend owns all ratios and percentages. */
export function ProgressSummary() {
  const query = useQuery({ queryKey: ["modeled-progress"], queryFn: api.progress, staleTime: 0 });
  if (query.isPending) return <Skeleton className="h-40" />;
  if (query.isError) return <ErrorState onRetry={() => query.refetch()}>Modeled progress could not be loaded.</ErrorState>;
  const data = query.data;
  return (
    <section className="space-y-4" aria-label="Modeled requirement progress">
      <Alert tone="info">These percentages describe modeled obligations. They do not establish eligibility to graduate.</Alert>
      {data.metrics.map((metric, i) => (
        <section id={metric.scope} key={metric.scope} style={{ animationDelay: `${i * 70}ms` }} className="cp-lift space-y-2 animate-cp-rise rounded-xl border border-border bg-surface-elevated p-4">
          <h2 className="text-base font-semibold text-fg">{metric.label}</h2>
          {metric.availability === "unavailable" ? <p className="text-sm text-fg-muted">This requirement scope is not available. No percentage is assumed.</p> : <>
            <p className="tabular text-lg font-semibold text-fg">{metric.completed.percent}% completed</p>
            <Progress value={Number(metric.completed.percent)} max={100} label={`${metric.label}: ${metric.completed.percent}% completed`} />
            <p className="text-sm text-fg-secondary">With courses in progress: {metric.projected_current.percent}% conditional.</p>
            {metric.projected_plan && <p className="text-sm text-fg-secondary">With the saved plan: {metric.projected_plan.percent}% conditional.</p>}
            {metric.unknown_obligations > 0 && <p className="text-sm text-warning">Cannot determine {metric.unknown_obligations} obligation(s); completed progress is a confirmed lower bound.</p>}
            <details className="text-sm text-fg-secondary">
              <summary className="min-h-11 cursor-pointer py-2">How this percentage is measured</summary>
              <p>{metric.denominator_definition}</p>
              <p className="tabular">{metric.denominator_units} obligation units. Exact completed percentage: {metric.completed.exact_percent?.numerator}/{metric.completed.exact_percent?.denominator}%.</p>
              <p>{metric.explanation}</p>
            </details>
            <details className="rounded border border-border p-3">
              <summary className="min-h-11 cursor-pointer text-sm font-medium text-primary-text">Inspect what&apos;s counted in {metric.label}</summary>
              <ul className="space-y-2">{metric.requirement_results.map((node) => <RequirementNode key={node.requirement_code} node={node} evidence={(code) => evidenceFor(code, metric.allocations, [])} filter="all" />)}</ul>
            </details>
          </>}
        </section>
      ))}
      {data.projection_state === "stale" && <Alert tone="warning">The saved plan is out of date. Its projected progress is withheld until you regenerate it.</Alert>}
      {data.projection_state === "incomparable" && <Alert tone="warning">A projection changes allocation in a way that cannot be shown as additive segments. Review the detailed audit.</Alert>}
      <details className="rounded border border-border p-3 text-sm text-fg-secondary">
        <summary className="min-h-11 cursor-pointer">Coverage and limitations</summary>
        <ul className="list-disc space-y-1 pl-5">{data.coverage_limitations.map((text) => <li key={text}>{text}</li>)}</ul>
      </details>
    </section>
  );
}
