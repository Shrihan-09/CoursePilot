"use client";

import { ArrowRight, Info } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";

import { StateBadge, STATUS_ICON } from "@/components/academic/status";
import { buttonVariants } from "@/components/ui/button";
import { Alert, Progress } from "@/components/ui/feedback";
import { Tabs } from "@/components/ui/overlay";
import {
  AUDIT_STATUS,
  FINDING_SEVERITY,
  REQUIREMENT_STATUS,
  RULE_STATUS,
} from "@/lib/academic/vocabulary";
import type { DegreeAudit, RequirementStatus } from "@/types/degree";
import type { ProgramView } from "@/types/onboarding";

import { evidenceFor, leafTally, matchesFilter, RequirementNode, type Filter } from "./requirement-tree";

/**
 * Pending-review is shown prominently whenever EITHER source says so - the
 * program's lifecycle (profile) or the engine's own finding. The UI can add
 * the warning; it can never remove one.
 */
export function PendingReviewNotice({ program, audit }: { program: Pick<ProgramView, "support_status" | "program_name">; audit: Pick<DegreeAudit, "findings"> }) {
  const pending =
    program.support_status !== "supported" ||
    audit.findings.some((f) => f.code === "program_pending_review");
  if (!pending) return null;
  return (
    <Alert tone="warning" title="This program's requirements are pending human review">
      CoursePilot has encoded {program.program_name} from the Rutgers catalog, but a person has not
      yet verified the encoding. Treat these results as a guide, not an official audit - confirm with
      your advisor or Degree Navigator.
    </Alert>
  );
}

const TALLY_ORDER: RequirementStatus[] = [
  "satisfied", "provisionally_satisfied", "partially_satisfied", "unsatisfied", "indeterminate", "not_evaluable",
];

export function DegreeSummary({ audit }: { audit: DegreeAudit }) {
  const state = AUDIT_STATUS[audit.status];
  const tally = leafTally(audit.requirements);
  const required = audit.credits_required_min;
  const applicable = Number(audit.credits_applicable_to_degree);
  return (
    <section aria-labelledby="summary-heading" className="space-y-4 animate-cp-rise rounded-xl border border-border bg-surface-elevated p-4 sm:p-5">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0 space-y-1">
          <h2 id="summary-heading" className="text-sm font-semibold text-fg">Overall</h2>
          <p className="text-sm text-fg-secondary">{state.meaning}</p>
        </div>
        <StateBadge state={state} className="shrink-0" />
      </div>

      {required !== null && (
        <div className="space-y-1.5">
          <p className="text-sm text-fg">
            <span className="tabular font-semibold">{audit.credits_applicable_to_degree}</span> of{" "}
            <span className="tabular">{required}</span> program credits counted
          </p>
          <Progress value={applicable} max={Number(required)} label="Program credits counted, completed courses only" />
          <p className="text-xs text-fg-muted">
            Completed credits the Degree Engine counts toward this program&apos;s modeled minimum ({required}). It is
            not requirement completion, and not the total credits needed to graduate: school-wide and Rutgers
            graduation requirements are not included unless they appear below.
            {audit.credits_remaining !== null && <> {audit.credits_remaining} credits remain toward this minimum.</>}
          </p>
        </div>
      )}

      <dl className="grid grid-cols-2 gap-px overflow-hidden rounded border border-border bg-border sm:grid-cols-3">
        {[
          ["Credits completed", audit.credits_completed],
          ["Credits in progress", audit.credits_in_progress],
          ["Credits not counted", audit.credits_excluded],
        ].map(([label, value]) => (
          <div key={label} className="bg-surface-elevated p-3">
            <dt className="text-xs text-fg-muted">{label}</dt>
            <dd className="tabular text-lg font-semibold text-fg">{value}</dd>
          </div>
        ))}
      </dl>

      <div className="space-y-1.5">
        <p className="text-xs font-medium uppercase tracking-wide text-fg-muted">Requirements, by status</p>
        <ul className="flex flex-wrap gap-x-4 gap-y-1.5 text-sm">
          {TALLY_ORDER.filter((s) => tally[s] > 0).map((s) => {
            const p = REQUIREMENT_STATUS[s];
            const Icon = STATUS_ICON[p.icon];
            return (
              <li key={s} className="flex items-center gap-1.5 text-fg-secondary">
                <Icon aria-hidden className="size-4 shrink-0" />
                <span className="tabular font-medium text-fg">{tally[s]}</span> {p.label.toLowerCase()}
              </li>
            );
          })}
        </ul>
        <p className="text-xs text-fg-muted">Counts of individual requirements; they differ in size, so this is not a percentage of the degree.</p>
      </div>
    </section>
  );
}

const FILTERS: { value: Filter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "remaining", label: "Still needed" },
  { value: "uncertain", label: "Can't determine" },
  { value: "complete", label: "Complete" },
];

export function DegreeView({ audit, program }: { audit: DegreeAudit; program: ProgramView }) {
  const [filter, setFilter] = useState<Filter>("all");
  const evidence = useMemo(() => {
    const cache = new Map<string, ReturnType<typeof evidenceFor>>();
    return (code: string) => {
      if (!cache.has(code)) cache.set(code, evidenceFor(code, audit.allocation, audit.grade_evaluations));
      return cache.get(code)!;
    };
  }, [audit]);
  // A program with a single root (the usual shape) is shown as its groups, so
  // the first screen is the requirements rather than one collapsed wrapper.
  const only = audit.requirements.length === 1 ? audit.requirements[0] : undefined;
  const groups = only && only.children.length ? only.children : audit.requirements;
  const findings = audit.findings.filter((f) => f.code !== "program_pending_review");

  return (
    <div className="space-y-6">
      <PendingReviewNotice program={program} audit={audit} />
      <DegreeSummary audit={audit} />

      {findings.length > 0 && (
        <section aria-labelledby="findings-heading" className="space-y-2">
          <h2 id="findings-heading" className="text-sm font-semibold text-fg">Things to know</h2>
          {findings.map((f, i) => (
            <Alert key={`${f.code}-${f.requirement_code}-${i}`} tone={FINDING_SEVERITY[f.severity].tone} title={FINDING_SEVERITY[f.severity].label}>
              {f.message}
              {f.remediation && <span className="block text-fg-muted">{f.remediation}</span>}
            </Alert>
          ))}
        </section>
      )}

      <section aria-labelledby="requirements-heading" className="space-y-3">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
          <h2 id="requirements-heading" className="text-base font-semibold text-fg">Requirements</h2>
          <Link href="/plan" className={buttonVariants({ variant: "secondary", size: "sm" })}>
            Plan remaining requirements <ArrowRight aria-hidden className="size-4" />
          </Link>
        </div>
        <Tabs label="Show requirements" value={filter} onChange={setFilter} items={FILTERS} />
        {groups.some((g) => matchesFilter(g, filter)) ? (
          <ul className="space-y-2">
            {groups.map((g) => (
              <RequirementNode key={g.requirement_code} node={g} evidence={evidence} filter={filter} />
            ))}
          </ul>
        ) : (
          <p className="rounded border border-dashed border-border-strong p-4 text-sm text-fg-muted">
            No requirement has this status.
          </p>
        )}
      </section>

      {audit.rules.length > 0 && (
        <section aria-labelledby="rules-heading" className="space-y-2">
          <h2 id="rules-heading" className="text-base font-semibold text-fg">Program rules</h2>
          <ul className="divide-y divide-border rounded border border-border bg-surface-elevated">
            {audit.rules.map((r) => (
              <li key={r.rule_code} className="space-y-1 p-3">
                <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                  <p className="text-sm font-medium text-fg [overflow-wrap:anywhere]">{r.rule_name}</p>
                  <StateBadge state={RULE_STATUS[r.status]} className="shrink-0" />
                </div>
                <p className="text-sm text-fg-secondary">{r.reason}</p>
              </li>
            ))}
          </ul>
        </section>
      )}

      {(audit.unallocated_courses.length > 0 || audit.excluded_courses.length > 0) && (
        <section aria-labelledby="other-heading" className="space-y-2">
          <h2 id="other-heading" className="text-base font-semibold text-fg">Courses not counted toward a requirement</h2>
          <ul className="divide-y divide-border rounded border border-border bg-surface-elevated">
            {audit.unallocated_courses.map((c) => (
              <li key={`u-${c.course_string}-${c.supplement_code}`} className="flex flex-col gap-0.5 p-3 text-sm sm:flex-row sm:gap-3">
                <span className="code font-medium text-fg">{c.course_string}</span>
                <span className="flex-1 text-fg-secondary [overflow-wrap:anywhere]">{c.title ?? "Untitled course"}</span>
                <span className="text-xs text-fg-muted">Not used by any requirement</span>
              </li>
            ))}
            {audit.excluded_courses.map((c) => (
              <li key={`x-${c.course_string}-${c.supplement_code}`} className="flex flex-col gap-0.5 p-3 text-sm sm:flex-row sm:gap-3">
                <span className="code font-medium text-fg">{c.course_string}</span>
                <span className="flex-1 text-fg-secondary [overflow-wrap:anywhere]">{c.title ?? "Untitled course"}</span>
                <span className="text-xs text-fg-muted">Excluded from the degree by a program rule</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {audit.disclaimers.length > 0 && (
        <footer className="flex gap-2 text-xs text-fg-muted">
          <Info aria-hidden className="mt-0.5 size-3.5 shrink-0" />
          <div className="space-y-1">{audit.disclaimers.map((d) => <p key={d}>{d}</p>)}</div>
        </footer>
      )}
    </div>
  );
}
