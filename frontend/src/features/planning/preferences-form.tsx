"use client";

import { useQuery } from "@tanstack/react-query";
import { Plus, Trash2 } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Checkbox, Field, Input, Radio, Select } from "@/components/ui/field";
import { api } from "@/lib/api";
import { DAY_NAME } from "@/lib/academic/schedule-time";
import { GOAL_KIND, termLabel, upcomingTerms } from "@/lib/academic/vocabulary";
import type {
  AdditionalGoal,
  CoursePin,
  FillerPreference,
  PlanningProfileInput,
  PlanningProfileResponse,
  Strategy,
  WorkloadLevel,
} from "@/types/personalization";
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
  const [strategy, setStrategy] = useState<Strategy>(initial.strategy ?? "balanced");
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
  const [level, setLevel] = useState<WorkloadLevel | null>(initial.workload_level ?? null);
  const [filler, setFiller] = useState<FillerPreference>(initial.filler_preference ?? "no_preference");
  const [pins, setPins] = useState<CoursePin[]>(initial.pins ?? []);
  const [gradTerm, setGradTerm] = useState(initial.expected_graduation_term ?? "");
  const termsAhead = upcomingTerms(new Date(), 8, { summer: initial.include_summer, winter: initial.include_winter });
  const [pinCourse, setPinCourse] = useState("");
  const [pinTerm, setPinTerm] = useState(termsAhead[0] ?? "");
  const pinValid = /^\d{2}:\d{3}:\d{3}$/.test(pinCourse.trim());

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
          pace, strategy, target_credits_per_term: t, max_credits_per_term: m, max_courses_per_term: c, max_terms: n,
          include_summer: summer, include_winter: winter,
          schedule: { ...initial.schedule, earliest_start: early || null, latest_end: late || null, avoid_days: avoid },
          additional_goals: goals,
          workload_level: level, filler_preference: filler, pins,
          expected_graduation_term: gradTerm || null,
        });
      }}
    >
      <fieldset className="space-y-2">
        <legend className="text-base font-semibold text-fg">When do you want to graduate?</legend>
        <p className="text-sm text-fg-muted">This sets how many semesters your plan has, counted from your first semester. On time is the default; CoursePilot never assumes you want to finish early.</p>
        <Field label="Expected graduation term (optional)" hint="Overrides the choice below when set.">
          {({ id, describedBy }) => (
            <Select id={id} aria-describedby={describedBy} value={gradTerm} onChange={(e) => setGradTerm(e.target.value)}>
              <option value="">Not sure</option>
              {upcomingTerms(new Date(), 14).filter((t) => t.endsWith("1") || t.endsWith("9"))
                .map((t) => <option key={t} value={t}>{termLabel(t)}</option>)}
            </Select>
          )}
        </Field>
        <div className="space-y-1">
          {meta.paces.map((p) => (
            <Radio key={p.value} name="pace" label={p.label} description={p.description}
              checked={pace === p.value} onChange={() => setPace(p.value)} />
          ))}
        </div>
      </fieldset>

      {(meta.workload_levels?.length ?? 0) > 0 && (
        <fieldset className="space-y-2">
          <legend className="text-base font-semibold text-fg">How intense should each semester be?</legend>
          <p className="text-sm text-fg-muted">
            Each level sets a credit range and a workload budget per semester. Courses you must take (a retake,
            or one you pin) always stay - if they go over the budget, your plan says so.
          </p>
          <div className="space-y-1">
            {meta.workload_levels!.map((w) => (
              <Radio key={w.value} name="workload_level" label={w.label} description={w.description}
                checked={level === w.value} onChange={() => setLevel(w.value)} />
            ))}
          </div>
          {meta.workload_weight && <p className="text-xs text-fg-muted">{meta.workload_weight}</p>}
        </fieldset>
      )}

      {(meta.filler_preferences?.length ?? 0) > 0 && level && (
        <fieldset className="space-y-2">
          <legend className="text-base font-semibold text-fg">For the rest of a semester&apos;s credits</legend>
          <p className="text-sm text-fg-muted">After the courses you need, CoursePilot fills up to your credit range with courses that complete a requirement.</p>
          <div className="space-y-1">
            {meta.filler_preferences!.map((f) => (
              <Radio key={f.value} name="filler_preference" label={f.label} description={f.description}
                checked={filler === f.value} onChange={() => setFiller(f.value)} />
            ))}
          </div>
        </fieldset>
      )}

      {level && (
        <fieldset className="space-y-3">
          <legend className="text-base font-semibold text-fg">Courses you want in a specific semester</legend>
          <p className="text-sm text-fg-muted">
            A pinned course is placed in that semester or not at all. If it can&apos;t be (prerequisites, not offered,
            restricted), your plan explains why.
          </p>
          {pins.length > 0 && (
            <ul className="divide-y divide-border rounded border border-border">
              {pins.map((p, i) => (
                <li key={`${p.course}-${p.term_code}`} className="flex items-center justify-between gap-2 p-2 text-sm">
                  <span className="code">{p.course} <span className="text-fg-muted">in {termLabel(p.term_code)}</span></span>
                  <Button type="button" variant="ghost" size="sm" aria-label={`Unpin ${p.course}`} onClick={() => setPins(pins.filter((_, j) => j !== i))}>
                    <Trash2 aria-hidden className="size-4" />
                  </Button>
                </li>
              ))}
            </ul>
          )}
          <div className="grid gap-3 sm:grid-cols-[1fr_12rem_auto] sm:items-end">
            <Field label="Course" hint="e.g. 01:640:152">
              {({ id, describedBy }) => <Input id={id} aria-describedby={describedBy} value={pinCourse} placeholder="01:640:152" onChange={(e) => setPinCourse(e.target.value)} />}
            </Field>
            <Field label="Semester">
              {({ id }) => (
                <Select id={id} value={pinTerm} onChange={(e) => setPinTerm(e.target.value)}>
                  {termsAhead.map((t) => <option key={t} value={t}>{termLabel(t)}</option>)}
                </Select>
              )}
            </Field>
            <Button type="button" variant="secondary" disabled={!pinValid || pins.length >= 24}
              onClick={() => {
                const pin = { course: pinCourse.trim(), term_code: pinTerm };
                if (!pins.some((p) => p.course === pin.course)) setPins([...pins, pin]);
                setPinCourse("");
              }}>
              <Plus aria-hidden className="size-4" /> Pin
            </Button>
          </div>
        </fieldset>
      )}

      {(meta.strategies?.length ?? 0) > 0 && (
        <fieldset className="space-y-2">
          <legend className="text-base font-semibold text-fg">When several courses fit, which should come first?</legend>
          <p className="text-sm text-fg-muted">
            This only changes the order of courses CoursePilot has already checked you can take. It never skips a
            prerequisite, goes over your maximum, or plans a course Rutgers isn&apos;t offering.
          </p>
          <div className="space-y-1">
            {meta.strategies!.map((s) => (
              <Radio key={s.value} name="strategy" label={s.label} description={s.description}
                checked={strategy === s.value} onChange={() => setStrategy(s.value)} />
            ))}
          </div>
        </fieldset>
      )}

      <fieldset className="space-y-3">
        <legend className="text-base font-semibold text-fg">Semester load</legend>
        {level && <p className="text-sm text-fg-muted">Your workload level sets the credit range; the course limit below still applies.</p>}
        <div className="grid gap-4 sm:grid-cols-3">
          {!level && <Field label="Aim for (credits)" hint="CoursePilot aims near this; it isn't a minimum." error={errors.target}>
            {({ id, describedBy, invalid }) => <Input id={id} aria-describedby={describedBy} aria-invalid={invalid} type="number" inputMode="decimal" min={1} max={24} step={0.5} value={target} onChange={(e) => setTarget(e.target.value)} />}
          </Field>}
          {!level && <Field label="Never more than (credits)" hint="A hard limit for every semester." error={errors.max}>
            {({ id, describedBy, invalid }) => <Input id={id} aria-describedby={describedBy} aria-invalid={invalid} type="number" inputMode="decimal" min={1} max={24} step={0.5} value={max} onChange={(e) => setMax(e.target.value)} />}
          </Field>}
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
          <Field label="Plan at most this many semesters" hint="Leave empty to let your graduation target decide." error={errors.terms}>
            {({ id, describedBy, invalid }) => <Input id={id} aria-describedby={describedBy} aria-invalid={invalid} type="number" inputMode="numeric" min={1} max={12} value={terms} onChange={(e) => setTerms(e.target.value)} />}
          </Field>
        </div>
      </details>

      <Button type="submit" variant="primary" disabled={!valid} loading={busy}>{submitLabel}</Button>
    </form>
  );
}
