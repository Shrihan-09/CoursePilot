"use client";

import { useQuery } from "@tanstack/react-query";
import { Plus, Trash2 } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Checkbox, Field, Input, Radio, Select } from "@/components/ui/field";
import { api } from "@/lib/api";
import { DAY_NAME } from "@/lib/academic/schedule-time";
import { GOAL_KIND } from "@/lib/academic/vocabulary";
import type { AdditionalGoal, PlanningProfileInput, PlanningProfileResponse } from "@/types/personalization";
import type { Pace } from "@/types/plan";
import type { DayCode } from "@/types/schedule";

/**
 * Planning preferences, as a plain guided form (Phase 7.2.1).
 *
 * Deliberately NOT an AI interview: no model is involved. It asks only for
 * what CoursePilot cannot already know (the record is never re-asked), and
 * every field maps 1:1 to the server's PlanningProfileInput, which validates
 * it again. A future assistant would propose this same structure for the
 * student to confirm here.
 */

const WEEKDAYS: DayCode[] = ["M", "T", "W", "H", "F", "S"];

export function PreferencesForm({
  meta,
  initial,
  busy,
  submitLabel,
  onSubmit,
}: {
  meta: PlanningProfileResponse;
  initial: PlanningProfileInput;
  busy: boolean;
  submitLabel: string;
  onSubmit: (value: PlanningProfileInput) => void;
}) {
  const programs = useQuery({ queryKey: ["programs"], queryFn: api.programs });
  const [pace, setPace] = useState<Pace>(initial.pace);
  const [target, setTarget] = useState(String(initial.target_credits_per_term));
  const [max, setMax] = useState(String(initial.max_credits_per_term));
  const [courses, setCourses] = useState(String(initial.max_courses_per_term));
  const [terms, setTerms] = useState(initial.max_terms ? String(initial.max_terms) : "");
  const [summer, setSummer] = useState(initial.include_summer);
  const [winter, setWinter] = useState(initial.include_winter);
  const [early, setEarly] = useState(initial.schedule.earliest_start ?? "");
  const [late, setLate] = useState(initial.schedule.latest_end ?? "");
  const [avoid, setAvoid] = useState<DayCode[]>(initial.schedule.avoid_days);
  const [goals, setGoals] = useState<AdditionalGoal[]>(initial.additional_goals);
  const [goalKind, setGoalKind] = useState<AdditionalGoal["kind"]>("second_major");
  const [goalProgram, setGoalProgram] = useState("");

  const t = Number(target), m = Number(max), c = Number(courses), n = terms === "" ? null : Number(terms);
  const errors = {
    target: t >= 1 && t <= 24 ? undefined : "Between 1 and 24.",
    max: m >= 1 && m <= 24 ? (t > m ? "The maximum can't be below your target." : undefined) : "Between 1 and 24.",
    courses: Number.isInteger(c) && c >= 1 && c <= 8 ? undefined : "A whole number from 1 to 8.",
    terms: n === null || (Number.isInteger(n) && n >= 1 && n <= 12) ? undefined : "A whole number from 1 to 12, or leave empty.",
    window: early && late && early >= late ? "The earliest time must be before the latest." : undefined,
  };
  const valid = !Object.values(errors).some(Boolean);
  const programChoices = (programs.data?.programs ?? []).filter((p) => p.versions.some((v) => v.support_status !== "unavailable"));

  return (
    <form
      className="space-y-6"
      onSubmit={(e) => {
        e.preventDefault();
        if (!valid) return;
        onSubmit({
          pace, target_credits_per_term: t, max_credits_per_term: m, max_courses_per_term: c, max_terms: n,
          include_summer: summer, include_winter: winter,
          schedule: { earliest_start: early || null, latest_end: late || null, avoid_days: avoid },
          additional_goals: goals,
        });
      }}
    >
      <fieldset className="space-y-2">
        <legend className="text-base font-semibold text-fg">How would you like to pace your semesters?</legend>
        <p className="text-sm text-fg-muted">Balanced is the default. CoursePilot never assumes you want to finish early.</p>
        <div className="space-y-1">
          {meta.paces.map((p) => (
            <Radio key={p.value} name="pace" label={p.label} description={p.description}
              checked={pace === p.value} onChange={() => setPace(p.value)} />
          ))}
        </div>
      </fieldset>

      <fieldset className="space-y-3">
        <legend className="text-base font-semibold text-fg">Semester load</legend>
        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="Aim for (credits)" hint="CoursePilot aims near this; it isn't a minimum." error={errors.target}>
            {({ id, describedBy, invalid }) => <Input id={id} aria-describedby={describedBy} aria-invalid={invalid} type="number" inputMode="decimal" min={1} max={24} step={0.5} value={target} onChange={(e) => setTarget(e.target.value)} />}
          </Field>
          <Field label="Never more than (credits)" hint="A hard limit for every semester." error={errors.max}>
            {({ id, describedBy, invalid }) => <Input id={id} aria-describedby={describedBy} aria-invalid={invalid} type="number" inputMode="decimal" min={1} max={24} step={0.5} value={max} onChange={(e) => setMax(e.target.value)} />}
          </Field>
          <Field label="Courses per semester, at most" error={errors.courses}>
            {({ id, invalid }) => <Input id={id} aria-invalid={invalid} type="number" inputMode="numeric" min={1} max={8} value={courses} onChange={(e) => setCourses(e.target.value)} />}
          </Field>
        </div>
        <p className="text-xs text-fg-muted">{meta.disclaimer}</p>
      </fieldset>

      <fieldset className="space-y-1">
        <legend className="text-base font-semibold text-fg">Extra sessions</legend>
        <Checkbox label="I'd consider summer courses" checked={summer} onChange={(e) => setSummer(e.target.checked)} />
        <Checkbox label="I'd consider winter courses" checked={winter} onChange={(e) => setWinter(e.target.checked)} />
      </fieldset>

      <fieldset className="space-y-3">
        <legend className="text-base font-semibold text-fg">Class times</legend>
        <p className="text-sm text-fg-muted">
          Used as limits when you build a schedule: a section outside them isn&apos;t used. You can change them each time.
        </p>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="No classes before" error={errors.window}>
            {({ id }) => <Input id={id} type="time" value={early} onChange={(e) => setEarly(e.target.value)} />}
          </Field>
          <Field label="No classes after">
            {({ id }) => <Input id={id} type="time" value={late} onChange={(e) => setLate(e.target.value)} />}
          </Field>
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-3">
          {WEEKDAYS.map((d) => (
            <Checkbox key={d} label={`No classes ${DAY_NAME[d]}`} checked={avoid.includes(d)}
              onChange={(e) => setAvoid(e.target.checked ? [...avoid, d] : avoid.filter((x) => x !== d))} />
          ))}
        </div>
      </fieldset>

      <fieldset className="space-y-3">
        <legend className="text-base font-semibold text-fg">Anything else you&apos;re considering?</legend>
        <p className="text-sm text-fg-muted">
          Recorded as something you&apos;re exploring. Your plan doesn&apos;t include it: CoursePilot can&apos;t combine
          programs yet. You can compare a major in Degree → What-if.
        </p>
        {goals.length > 0 && (
          <ul className="divide-y divide-border rounded border border-border">
            {goals.map((g, i) => (
              <li key={`${g.kind}-${g.label}-${i}`} className="flex items-center justify-between gap-2 p-2 text-sm">
                <span className="min-w-0 [overflow-wrap:anywhere]">{GOAL_KIND[g.kind]}: {g.label} <span className="text-fg-muted">- exploring</span></span>
                <Button type="button" variant="ghost" size="sm" aria-label={`Remove ${g.label}`} onClick={() => setGoals(goals.filter((_, j) => j !== i))}>
                  <Trash2 aria-hidden className="size-4" />
                </Button>
              </li>
            ))}
          </ul>
        )}
        {goals.length < 4 && (
          <div className="grid gap-3 sm:grid-cols-[12rem_1fr_auto] sm:items-end">
            <Field label="Kind">
              {({ id }) => (
                <Select id={id} value={goalKind} onChange={(e) => setGoalKind(e.target.value as AdditionalGoal["kind"])}>
                  {Object.entries(GOAL_KIND).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                </Select>
              )}
            </Field>
            <Field label="Program" hint={goalKind === "second_major" ? undefined : "Minors and certificates aren't in CoursePilot's data yet; describe it in words."}>
              {({ id, describedBy }) => goalKind === "second_major" ? (
                <Select id={id} aria-describedby={describedBy} value={goalProgram} onChange={(e) => setGoalProgram(e.target.value)}>
                  <option value="">Choose a program</option>
                  {programChoices.map((p) => <option key={p.program_key} value={p.program_key}>{p.name} ({p.degree_type})</option>)}
                </Select>
              ) : (
                <Input id={id} aria-describedby={describedBy} value={goalProgram} maxLength={120} placeholder="e.g. Mathematics minor" onChange={(e) => setGoalProgram(e.target.value)} />
              )}
            </Field>
            <Button
              type="button"
              variant="secondary"
              disabled={!goalProgram.trim()}
              onClick={() => {
                const program = programChoices.find((p) => p.program_key === goalProgram);
                setGoals([...goals, program
                  ? { kind: goalKind, program_key: program.program_key, label: program.name, status: "exploring" }
                  : { kind: goalKind, program_key: null, label: goalProgram.trim(), status: "exploring" }]);
                setGoalProgram("");
              }}
            >
              <Plus aria-hidden className="size-4" /> Add
            </Button>
          </div>
        )}
      </fieldset>

      <details className="rounded border border-border">
        <summary className="min-h-11 cursor-pointer p-3 text-sm font-medium text-fg">More settings</summary>
        <div className="border-t border-border p-3">
          <Field label="Plan at most this many semesters" hint="Leave empty to let your pace decide (8, or 12 for Lighter)." error={errors.terms}>
            {({ id, describedBy, invalid }) => <Input id={id} aria-describedby={describedBy} aria-invalid={invalid} type="number" inputMode="numeric" min={1} max={12} value={terms} onChange={(e) => setTerms(e.target.value)} />}
          </Field>
        </div>
      </details>

      <Button type="submit" variant="primary" disabled={!valid} loading={busy}>{submitLabel}</Button>
    </form>
  );
}
