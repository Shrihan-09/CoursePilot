import { ChevronRight } from "lucide-react";

import { StateBadge } from "@/components/academic/status";
import { REQUIREMENT_STATUS, termLabel } from "@/lib/academic/vocabulary";
import { cn } from "@/lib/utils";
import type { Allocation, GradeEvaluation, RequirementResult, RequirementStatus } from "@/types/degree";

/**
 * A requirement and its parts, exactly as the Degree Engine returned them.
 *
 * Native <details>/<summary>: keyboard-operable and announced as expandable
 * without any script. Counts, credits and the explanation are the engine's
 * fields, printed - this component adds no number of its own.
 */

export type Filter = "all" | "remaining" | "uncertain" | "complete";

const FILTER_STATES: Record<Exclude<Filter, "all">, RequirementStatus[]> = {
  remaining: ["unsatisfied", "partially_satisfied"],
  uncertain: ["indeterminate", "not_evaluable"],
  complete: ["satisfied", "provisionally_satisfied"],
};

/** Show a node if it - or anything beneath it - has a status in the filter. */
export function matchesFilter(node: RequirementResult, filter: Filter): boolean {
  if (filter === "all") return true;
  if (FILTER_STATES[filter].includes(node.status)) return true;
  return node.children.some((c) => matchesFilter(c, filter));
}

export function progressText(node: RequirementResult): string | null {
  if (node.needed_credits !== null) {
    return `${node.satisfied_credits} of ${node.needed_credits} credits`;
  }
  if (node.needed_distinct_categories !== null) {
    return `${node.distinct_categories} of ${node.needed_distinct_categories} areas`;
  }
  if (node.needed_count !== null && node.requirement_type !== "course") {
    const noun = node.children.length ? "parts" : "courses";
    return `${node.satisfied_count} of ${node.needed_count} ${noun}`;
  }
  return null;
}

const CURATION: Record<string, string> = {
  curated_from_prose: "Encoded by CoursePilot from the Rutgers catalog text above.",
  synthetic: "A grouping CoursePilot added to organise the requirement; not catalog text.",
  unverified: "Not yet verified against the Rutgers catalog.",
};

export interface Evidence {
  allocations: Allocation[];
  grades: GradeEvaluation[];
}

export function evidenceFor(code: string, allocation: Allocation[], grades: GradeEvaluation[]): Evidence {
  return {
    allocations: allocation.filter((a) => a.requirement_code === code),
    grades: grades.filter((g) => g.requirement_code === code),
  };
}

export function RequirementNode({
  node,
  evidence,
  filter,
  depth = 0,
  source,
}: {
  node: RequirementResult;
  evidence: (code: string) => Evidence;
  filter: Filter;
  depth?: number;
  /** Phase 7.4: which source decided this node's status (Degree Navigator or estimate). */
  source?: (code: string) => React.ReactNode;
}) {
  if (!matchesFilter(node, filter)) return null;
  const state = REQUIREMENT_STATUS[node.status];
  const progress = progressText(node);
  const own = evidence(node.requirement_code);
  const children = node.children.filter((c) => matchesFilter(c, filter));
  const headingLevel = Math.min(6, 3 + depth) as 3 | 4 | 5 | 6;
  const H = `h${headingLevel}` as const;

  return (
    <li className="list-none">
      <details
        className={cn(
          "group rounded border border-border bg-surface-elevated",
          state.tone === "warning" && "border-warning/40",
        )}
        data-status={node.status}
      >
        <summary className="flex min-h-11 cursor-pointer list-none items-start gap-2 p-3 [&::-webkit-details-marker]:hidden">
          <ChevronRight
            aria-hidden
            className="mt-0.5 size-4 shrink-0 text-fg-muted transition-transform group-open:rotate-90"
          />
          <div className="min-w-0 flex-1 space-y-1">
            <H className="text-sm font-medium text-fg [overflow-wrap:anywhere]">{node.requirement_name}</H>
            {progress && <p className="tabular text-xs text-fg-muted">{progress}</p>}
          </div>
          <StateBadge state={state} className="shrink-0" />
        </summary>

        <div className="space-y-3 border-t border-border px-3 pb-3 pt-3 sm:pl-9">
          <div className="space-y-1">
            <p className="text-xs font-medium uppercase tracking-wide text-fg-muted">Why</p>
            <p className="text-sm text-fg">{node.reason}</p>
            <p className="text-xs text-fg-muted">{state.meaning}</p>
            {source?.(node.requirement_code)}
          </div>

          {own.allocations.length > 0 && (
            <div className="space-y-1">
              <p className="text-xs font-medium uppercase tracking-wide text-fg-muted">Courses CoursePilot used</p>
              <ul className="divide-y divide-border rounded border border-border">
                {own.allocations.map((a) => (
                  <li key={`${a.course.course_string}-${a.term_code}`} className="flex flex-col gap-0.5 p-2 text-sm sm:flex-row sm:items-baseline sm:gap-3">
                    <span className="code shrink-0 font-medium text-fg">{a.course.course_string}</span>
                    <span className="min-w-0 flex-1 text-fg-secondary [overflow-wrap:anywhere]">
                      {a.course.title ?? "Untitled course"}
                      {a.term_code && <span className="text-fg-muted"> · {termLabel(a.term_code)}</span>}
                      {a.earned_grade && <span className="text-fg-muted"> · Grade {a.earned_grade}</span>}
                      {a.status === "in_progress" && <span className="text-fg-muted"> · In progress</span>}
                    </span>
                    {a.credits_applied && (
                      <span className="tabular shrink-0 text-xs text-fg-muted">{a.credits_applied} cr</span>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {own.grades.length > 0 && (
            <div className="space-y-1">
              <p className="text-xs font-medium uppercase tracking-wide text-fg-muted">Grade conditions</p>
              <ul className="space-y-1 text-sm">
                {own.grades.map((g) => (
                  <li key={`${g.course.course_string}-${g.term_code}-${g.kind}`} className="text-fg-secondary">
                    <span className="code text-fg">{g.course.course_string}</span>: {g.reason}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {node.source_prose && (
            <div className="space-y-1">
              <p className="text-xs font-medium uppercase tracking-wide text-fg-muted">As published by Rutgers</p>
              <blockquote className="border-l-2 border-border-strong pl-3 text-sm text-fg-secondary [overflow-wrap:anywhere]">
                {node.source_prose}
              </blockquote>
            </div>
          )}
          {node.curation_status && CURATION[node.curation_status] && (
            <p className="text-xs text-fg-muted">{CURATION[node.curation_status]}</p>
          )}

          {children.length > 0 && (
            <ul className="space-y-2" aria-label={`Parts of ${node.requirement_name}`}>
              {children.map((child) => (
                <RequirementNode key={child.requirement_code} node={child} evidence={evidence} filter={filter} depth={depth + 1} source={source} />
              ))}
            </ul>
          )}
        </div>
      </details>
    </li>
  );
}

/** Leaf requirements tallied by the engine's status (a count, never a %). */
export function leafTally(nodes: RequirementResult[]): Record<RequirementStatus, number> {
  const out: Record<RequirementStatus, number> = {
    satisfied: 0, provisionally_satisfied: 0, partially_satisfied: 0,
    unsatisfied: 0, indeterminate: 0, not_evaluable: 0,
  };
  const walk = (n: RequirementResult) => {
    if (n.children.length === 0) out[n.status] += 1;
    n.children.forEach(walk);
  };
  nodes.forEach(walk);
  return out;
}
