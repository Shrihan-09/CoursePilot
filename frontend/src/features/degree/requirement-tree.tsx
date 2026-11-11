import { ChevronRight } from "lucide-react";
import { createContext, useContext } from "react";

import { StateBadge } from "@/components/academic/status";
import { DN_REVIEW, REQUIREMENT_STATUS, termLabel } from "@/lib/academic/vocabulary";
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

/**
 * Task 3: requirement codes Degree Navigator says are NOT met while
 * CoursePilot says they are. A node at or above one never reads "Complete".
 */
export const DnReviewContext = createContext<ReadonlySet<string>>(new Set());

export function underReview(node: RequirementResult, review: ReadonlySet<string>): boolean {
  return review.has(node.requirement_code) || node.children.some((c) => underReview(c, review));
}

const MET: RequirementStatus[] = ["satisfied", "provisionally_satisfied"];

/** What the node shows: its status, or "Needs review" while DN disagrees. */
export function presentationFor(node: RequirementResult, review: ReadonlySet<string>) {
  return MET.includes(node.status) && underReview(node, review) ? DN_REVIEW : REQUIREMENT_STATUS[node.status];
}

/**
 * Correctness fixes item 3: a choose-one requirement's chosen alternatives -
 * requirement code -> child codes, from the saved plan (the student's choice
 * or the planner's recommendation).
 */
export type AlternativeChoices = Readonly<Record<string, readonly string[]>>;
export const AlternativeChoicesContext = createContext<AlternativeChoices>({});

/** Share of a node that is done, 0..1 - for picking the furthest-along alternative. */
function doneFraction(node: RequirementResult): number {
  if (MET.includes(node.status)) return 1;
  if (node.children.length === 0) {
    if (node.needed_credits !== null && Number(node.needed_credits) > 0) {
      return Math.min(1, Number(node.satisfied_credits) / Number(node.needed_credits));
    }
    if (node.needed_count) return Math.min(1, node.satisfied_count / node.needed_count);
    return 0;
  }
  const parts = node.children.map(doneFraction);
  return parts.reduce((a, b) => a + b, 0) / parts.length;
}

/**
 * The children of a node that COUNT. For an ANY_OF, only its chosen
 * alternatives (else the furthest-along ones, code order breaking ties); an
 * unchosen alternative is never "still needed". Every other node: all children.
 */
export function countedChildren(node: RequirementResult, choices: AlternativeChoices = {}): RequirementResult[] {
  if (node.requirement_type !== "any_of" || node.children.length === 0) return node.children;
  const need = node.needed_count || 1;
  const order = [...node.children].sort((a, b) =>
    doneFraction(b) - doneFraction(a) || a.requirement_code.localeCompare(b.requirement_code));
  const chosen = new Set(choices[node.requirement_code] ?? []);
  const picked = order.filter((c) => chosen.has(c.requirement_code));
  if (picked.length === 0) return order.slice(0, need);
  return [...picked, ...order.filter((c) => !chosen.has(c.requirement_code))].slice(0, need);
}

/** Show a node if it - or anything beneath it that counts - has a status in the filter. */
export function matchesFilter(node: RequirementResult, filter: Filter,
                              review: ReadonlySet<string> = new Set(),
                              choices: AlternativeChoices = {}): boolean {
  if (filter === "all") return true;
  const children = countedChildren(node, choices);
  if (filter === "complete" && MET.includes(node.status) && underReview(node, review)) {
    return children.some((c) => matchesFilter(c, filter, review, choices));
  }
  if (FILTER_STATES[filter].includes(node.status)) return true;
  return children.some((c) => matchesFilter(c, filter, review, choices));
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
  curated_from_prose: "Encoded by Coursenav from the Rutgers catalog text above.",
  synthetic: "A grouping Coursenav added to organise the requirement; not catalog text.",
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
  const review = useContext(DnReviewContext);
  const choices = useContext(AlternativeChoicesContext);
  if (!matchesFilter(node, filter, review, choices)) return null;
  const state = presentationFor(node, review);
  const progress = progressText(node);
  const own = evidence(node.requirement_code);
  // "All" shows every alternative; a filter shows only the ones that count.
  const children = (filter === "all" ? node.children : countedChildren(node, choices))
    .filter((c) => matchesFilter(c, filter, review, choices));
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
              <p className="text-xs font-medium uppercase tracking-wide text-fg-muted">Courses Coursenav used</p>
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

/** Leaf requirements tallied by the engine's status (a count, never a %).
 * A met leaf Degree Navigator says is not met counts under `needs_review`.
 * Correctness fixes item 3: a choose-one requirement (ANY_OF) is ONE unit per
 * alternative it needs - the chosen (else furthest-along) alternative's status;
 * unchosen alternatives are not counted at all. */
export function leafTally(nodes: RequirementResult[], review: ReadonlySet<string> = new Set(),
                          choices: AlternativeChoices = {}):
    Record<RequirementStatus | "needs_review", number> {
  const out: Record<RequirementStatus | "needs_review", number> = {
    satisfied: 0, provisionally_satisfied: 0, partially_satisfied: 0,
    unsatisfied: 0, indeterminate: 0, not_evaluable: 0, needs_review: 0,
  };
  const unit = (n: RequirementResult) => {
    out[MET.includes(n.status) && underReview(n, review) ? "needs_review" : n.status] += 1;
  };
  const walk = (n: RequirementResult) => {
    if (n.children.length === 0) {
      unit(n);
      return;
    }
    if (n.requirement_type === "any_of") {
      if (MET.includes(n.status)) {
        for (let i = 0; i < (n.needed_count || 1); i += 1) unit(n);
      } else {
        countedChildren(n, choices).forEach(unit);
      }
      return;
    }
    n.children.forEach(walk);
  };
  nodes.forEach(walk);
  return out;
}
