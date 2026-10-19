"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { FlaskConical } from "lucide-react";
import { useMemo, useState } from "react";

import { StateBadge } from "@/components/academic/status";
import { Button } from "@/components/ui/button";
import { Alert, ErrorState, Skeleton } from "@/components/ui/feedback";
import { Field, Select } from "@/components/ui/field";
import { ApiError, api } from "@/lib/api";
import { AUDIT_STATUS, REQUIREMENT_STATUS } from "@/lib/academic/vocabulary";
import type { CourseComparison, ProgramOutcome, ScenarioAudit, ScenarioComparison } from "@/types/degree";

import { evidenceFor, RequirementNode } from "./requirement-tree";

/**
 * "What if I switched to another program?" - the SAME Degree Engine run
 * against a program the student does not have. Nothing is saved and the
 * student's real program is never touched (the API refuses to write; see
 * backend/app/services/scenarios.py). The banner below never leaves the
 * screen while a scenario is shown.
 */
export function ScenarioBanner({ programName }: { programName: string }) {
  return (
    <div
      role="note"
      className="flex items-start gap-3 rounded border-2 border-dashed border-primary/50 bg-primary-subtle p-3 text-sm"
    >
      <FlaskConical aria-hidden className="mt-0.5 size-4 shrink-0 text-primary-text" />
      <div className="space-y-0.5">
        <p className="font-semibold text-fg">What-if scenario: {programName}</p>
        <p className="text-fg-secondary">
          Hypothetical. Your program is not changed, and nothing is saved.
        </p>
      </div>
    </div>
  );
}

function OutcomeColumn({ title, outcome }: { title: string; outcome: ProgramOutcome }) {
  return (
    <div className="space-y-2 bg-surface-elevated p-3">
      <p className="text-xs font-medium uppercase tracking-wide text-fg-muted">{title}</p>
      <p className="text-sm font-medium text-fg [overflow-wrap:anywhere]">{outcome.program_name}</p>
      <StateBadge state={AUDIT_STATUS[outcome.status]} />
      <dl className="space-y-1 text-sm">
        <div className="flex justify-between gap-2"><dt className="text-fg-muted">Requirements complete</dt><dd className="tabular text-fg">{outcome.leaf_satisfied} of {outcome.leaf_requirements}</dd></div>
        <div className="flex justify-between gap-2"><dt className="text-fg-muted">Complete once in-progress finish</dt><dd className="tabular text-fg">{outcome.leaf_provisionally_satisfied}</dd></div>
        <div className="flex justify-between gap-2"><dt className="text-fg-muted">Credits counted</dt><dd className="tabular text-fg">{outcome.credits_applicable_to_degree}{outcome.credits_required_min ? ` of ${outcome.credits_required_min}` : ""}</dd></div>
      </dl>
    </div>
  );
}

function CourseList({ title, courses, empty }: { title: string; courses: CourseComparison[]; empty: string }) {
  return (
    <div className="space-y-1.5">
      <h4 className="text-sm font-semibold text-fg">{title} <span className="tabular font-normal text-fg-muted">({courses.length})</span></h4>
      {courses.length === 0 ? (
        <p className="text-sm text-fg-muted">{empty}</p>
      ) : (
        <ul className="divide-y divide-border rounded border border-border">
          {courses.map((c) => (
            <li key={c.course_string} className="flex flex-col gap-0.5 p-2 text-sm sm:flex-row sm:gap-3">
              <span className="code font-medium text-fg">{c.course_string}</span>
              <span className="flex-1 text-fg-secondary [overflow-wrap:anywhere]">{c.title ?? "Untitled course"}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function ScenarioResult({ compare, scenario }: { compare: ScenarioComparison; scenario: ScenarioAudit }) {
  const c = compare.comparison;
  const evidence = useMemo(
    () => (code: string) => evidenceFor(code, scenario.audit.allocation, scenario.audit.grade_evaluations),
    [scenario],
  );
  const pending = scenario.target.support_status !== "supported";
  const newlyNeeded = c.requirements_only_target.filter((r) => r.status !== "satisfied");
  return (
    <div className="space-y-5">
      <ScenarioBanner programName={`${scenario.target.program_name} (${scenario.target.catalog_year})`} />
      {pending && (
        <Alert tone="warning" title="This program's requirements are pending human review">
          Its encoding has not been verified by a person yet, so this comparison is a guide only.
        </Alert>
      )}
      {compare.assumptions.length > 0 && (
        <ul className="space-y-1 text-sm text-fg-secondary">
          {compare.assumptions.map((a) => <li key={a.code}>· {a.message}</li>)}
        </ul>
      )}

      <section aria-labelledby="compare-heading" className="space-y-2">
        <h3 id="compare-heading" className="text-sm font-semibold text-fg">Side by side</h3>
        <div className="grid gap-px overflow-hidden rounded border border-border bg-border sm:grid-cols-2">
          <OutcomeColumn title="My current program" outcome={c.current} />
          <OutcomeColumn title="What-if" outcome={c.target} />
        </div>
      </section>

      <section aria-labelledby="courses-heading" className="space-y-4">
        <h3 id="courses-heading" className="text-sm font-semibold text-fg">Your courses</h3>
        <CourseList title="Count toward the what-if program but not your current one" courses={c.courses_applied_only_target} empty="None." />
        <CourseList title="Count toward your current program but not the what-if" courses={c.courses_applied_only_current} empty="None." />
        <CourseList title="Count toward both" courses={c.courses_applied_in_both} empty="None." />
      </section>

      <section aria-labelledby="needed-heading" className="space-y-2">
        <h3 id="needed-heading" className="text-sm font-semibold text-fg">Requirements only the what-if program has</h3>
        {newlyNeeded.length === 0 ? (
          <p className="text-sm text-fg-muted">None that are still open.</p>
        ) : (
          <ul className="divide-y divide-border rounded border border-border">
            {newlyNeeded.map((r) => (
              <li key={r.code} className="flex flex-col gap-1 p-2 text-sm sm:flex-row sm:items-center sm:justify-between">
                <span className="text-fg [overflow-wrap:anywhere]">{r.name}</span>
                <StateBadge state={REQUIREMENT_STATUS[r.status]} />
              </li>
            ))}
          </ul>
        )}
      </section>

      {c.notes.length > 0 && (
        <div className="space-y-1 text-xs text-fg-muted">{c.notes.map((n) => <p key={n}>{n}</p>)}</div>
      )}

      <details className="rounded border border-border">
        <summary className="min-h-11 cursor-pointer p-3 text-sm font-medium text-fg">All what-if requirements</summary>
        <ul className="space-y-2 border-t border-border p-3">
          {scenario.audit.requirements.map((r) => (
            <RequirementNode key={r.requirement_code} node={r} evidence={evidence} filter="all" />
          ))}
        </ul>
      </details>
    </div>
  );
}

export function WhatIf({ currentKey }: { currentKey: string }) {
  const programs = useQuery({ queryKey: ["programs"], queryFn: api.programs });
  const [programKey, setProgramKey] = useState("");
  const [year, setYear] = useState("");

  const choices = useMemo(
    () => (programs.data?.programs ?? [])
      .map((p) => ({ ...p, versions: p.versions.filter((v) => v.support_status !== "unavailable") }))
      .filter((p) => p.versions.length > 0 && p.program_key !== currentKey),
    [programs.data, currentKey],
  );
  const selected = choices.find((p) => p.program_key === programKey);

  const run = useMutation({
    mutationFn: async () => {
      const yr = year || selected?.versions[0]?.catalog_year || null;
      const [compare, scenario] = await Promise.all([
        api.scenarioCompare(programKey, yr),
        api.scenarioAudit(programKey, yr),
      ]);
      return { compare, scenario };
    },
  });

  if (programs.isPending) return <Skeleton className="h-40" />;
  if (programs.isError) return <ErrorState onRetry={() => programs.refetch()}>Programs couldn&apos;t be loaded.</ErrorState>;

  return (
    <div className="space-y-5">
      <p className="text-sm text-fg-secondary">
        See how your record would count toward another program CoursePilot supports. This runs the same
        Degree Engine; it does not change your program.
      </p>
      <form
        className="grid gap-3 rounded-lg border border-border bg-surface-elevated p-4 sm:grid-cols-[1fr_12rem_auto] sm:items-end"
        onSubmit={(e) => {
          e.preventDefault();
          if (programKey) run.mutate();
        }}
      >
        <Field label="Program">
          {({ id }) => (
            <Select id={id} value={programKey} onChange={(e) => { setProgramKey(e.target.value); setYear(""); run.reset(); }}>
              <option value="">Choose a program</option>
              {choices.map((p) => (
                <option key={p.program_key} value={p.program_key}>
                  {p.name} ({p.degree_type}){p.versions.every((v) => v.support_status === "pending_review") ? " - pending review" : ""}
                </option>
              ))}
            </Select>
          )}
        </Field>
        <Field label="Catalog year">
          {({ id }) => (
            <Select id={id} value={year} disabled={!selected} onChange={(e) => { setYear(e.target.value); run.reset(); }}>
              {(selected?.versions ?? []).map((v) => (
                <option key={v.catalog_year} value={v.catalog_year}>{v.catalog_year}</option>
              ))}
            </Select>
          )}
        </Field>
        <Button type="submit" variant="primary" disabled={!programKey} loading={run.isPending}>
          Compare
        </Button>
      </form>

      {run.isError && (
        <Alert tone="danger" title="The what-if could not be run">
          {run.error instanceof ApiError ? run.error.message : "Please try again."}
        </Alert>
      )}
      {run.isPending && <Skeleton className="h-64" />}
      {run.data && <ScenarioResult compare={run.data.compare} scenario={run.data.scenario} />}
    </div>
  );
}
