"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Checkbox, Field, Input, Select } from "@/components/ui/field";
import { startTermChoices } from "@/lib/academic/terms";
import { termLabel } from "@/lib/academic/vocabulary";
import type { PlanningConstraints, PlanningContext } from "@/types/plan";

/**
 * Only settings the Planning Engine honours (PlanningConstraints): start
 * term, credit and course caps, horizon, summer/winter. Nothing here is a
 * preference the engine would silently ignore.
 */
export function PlanSettings({
  context,
  initial,
  submitLabel,
  busy,
  onSubmit,
  onCancel,
}: {
  context: PlanningContext;
  initial?: { start_term: string; constraints: PlanningConstraints };
  submitLabel: string;
  busy: boolean;
  onSubmit: (start: string, constraints: PlanningConstraints) => void;
  onCancel?: () => void;
}) {
  const d = initial?.constraints ?? context.default_constraints;
  const [credits, setCredits] = useState(String(d.max_credits_per_term));
  const [courses, setCourses] = useState(String(d.max_courses_per_term));
  const [terms, setTerms] = useState(String(d.max_terms));
  const [summer, setSummer] = useState(d.include_summer);
  const [winter, setWinter] = useState(d.include_winter);
  const first = context.earliest_start_term;
  const choices = first ? startTermChoices(first, 6, { summer, winter }) : [];
  const [start, setStart] = useState(initial?.start_term && choices.includes(initial.start_term) ? initial.start_term : (choices[0] ?? ""));

  const creditsN = Number(credits), coursesN = Number(courses), termsN = Number(terms);
  const errors = {
    credits: creditsN >= 1 && creditsN <= 24 ? undefined : "Between 1 and 24.",
    courses: Number.isInteger(coursesN) && coursesN >= 1 && coursesN <= 8 ? undefined : "A whole number from 1 to 8.",
    terms: Number.isInteger(termsN) && termsN >= 1 && termsN <= 12 ? undefined : "A whole number from 1 to 12.",
  };
  const valid = Boolean(start) && !errors.credits && !errors.courses && !errors.terms;

  return (
    <form
      className="space-y-4 rounded-lg border border-border bg-surface-elevated p-4 sm:p-5"
      onSubmit={(e) => {
        e.preventDefault();
        if (!valid) return;
        onSubmit(start, {
          max_credits_per_term: creditsN, max_courses_per_term: coursesN, max_terms: termsN,
          include_summer: summer, include_winter: winter,
        });
      }}
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="First semester" hint={context.latest_recorded_term ? `Your record runs through ${termLabel(context.latest_recorded_term)}.` : undefined}>
          {({ id, describedBy }) => (
            <Select id={id} aria-describedby={describedBy} value={start} onChange={(e) => setStart(e.target.value)}>
              {choices.map((t) => <option key={t} value={t}>{termLabel(t)}</option>)}
            </Select>
          )}
        </Field>
        <Field label="Semesters to plan, at most" error={errors.terms}>
          {({ id, invalid }) => (
            <Input id={id} type="number" inputMode="numeric" min={1} max={12} value={terms} aria-invalid={invalid} onChange={(e) => setTerms(e.target.value)} />
          )}
        </Field>
        <Field label="Credits per semester, at most" error={errors.credits}>
          {({ id, invalid }) => (
            <Input id={id} type="number" inputMode="decimal" min={1} max={24} step={0.5} value={credits} aria-invalid={invalid} onChange={(e) => setCredits(e.target.value)} />
          )}
        </Field>
        <Field label="Courses per semester, at most" error={errors.courses}>
          {({ id, invalid }) => (
            <Input id={id} type="number" inputMode="numeric" min={1} max={8} value={courses} aria-invalid={invalid} onChange={(e) => setCourses(e.target.value)} />
          )}
        </Field>
      </div>
      <fieldset className="space-y-1">
        <legend className="text-sm font-medium text-fg">Extra sessions</legend>
        <Checkbox label="Include summer sessions" checked={summer} onChange={(e) => setSummer(e.target.checked)} />
        <Checkbox label="Include winter sessions" checked={winter} onChange={(e) => setWinter(e.target.checked)} />
      </fieldset>
      <p className="text-xs text-fg-muted">
        These limits are CoursePilot planning settings, not Rutgers credit-load policy. Check your school&apos;s
        rules for overloads.
      </p>
      <div className="flex flex-wrap gap-2">
        <Button type="submit" variant="primary" disabled={!valid} loading={busy}>{submitLabel}</Button>
        {onCancel && <Button type="button" variant="ghost" onClick={onCancel}>Cancel</Button>}
      </div>
    </form>
  );
}
