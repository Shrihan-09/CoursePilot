"use client";

import { Plus, Trash2 } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Alert } from "@/components/ui/feedback";
import { Checkbox, Field, Input, Radio, Select } from "@/components/ui/field";
import { DAY_NAME } from "@/lib/academic/schedule-time";
import {
  BLOCK_KIND,
  CAMPUSES,
  JOB_BANDS,
  MAJOR_FOCUS,
  blockedTimeText,
  commitmentSummary,
  paceLevelConflict,
} from "@/lib/academic/questionnaire";
import { termLabel } from "@/lib/academic/vocabulary";
import {
  NO_COMMITMENTS,
  type BlockKind,
  type BlockedTime,
  type Commitments,
  type CoursePin,
  type PlanningProfileResponse,
  type TermAnswers,
  type WorkloadLevel,
} from "@/types/personalization";
import type { Pace } from "@/types/plan";
import type { DayCode } from "@/types/schedule";

/**
 * Task 2 planning questionnaire: commitments, honors, fixed weekly times and
 * per-semester answers. Used in onboarding and in Planning preferences (opened
 * from the Plan page). Every question can be skipped: a skipped answer is the
 * default (no commitments, not in an honors program, the level's range).
 *
 * Only commitment TYPES and hours are asked - never the name of an employer,
 * chapter, club or team.
 */

export interface QuestionnaireValue {
  commitments: Commitments;
  honors: boolean | null;
  blocked_times: BlockedTime[];
  term_overrides: TermAnswers[];
  credit_min: number | null;
  credit_max: number | null;
  major_min: number | null;
  major_max: number | null;
  preferred_campuses: string[];
  /** Asked here only where no fuller form has it (onboarding). */
  schedule_shape?: { earliest_start: string | null; latest_end: string | null; avoid_days: DayCode[] };
  pins?: CoursePin[];
}

/** Every answer cleared: what a skipped questionnaire means. */
export function clearedAnswers(v: QuestionnaireValue): QuestionnaireValue {
  return {
    commitments: { ...NO_COMMITMENTS }, honors: null, blocked_times: [], term_overrides: [],
    credit_min: null, credit_max: null, major_min: null, major_max: null, preferred_campuses: [],
    ...(v.schedule_shape ? { schedule_shape: { earliest_start: null, latest_end: null, avoid_days: [] } } : {}),
    ...(v.pins ? { pins: [] } : {}),
  };
}

export function answersInvalid(v: QuestionnaireValue): string | null {
  const bad = (lo: number | null | undefined, hi: number | null | undefined) =>
    lo != null && hi != null && (lo > hi || lo < 1 || hi > 24);
  if (bad(v.credit_min, v.credit_max)) return "Check your credit range.";
  if (v.term_overrides.some((o) => (o.major_courses_min !== null && o.major_courses_max !== null
    && o.major_courses_min > o.major_courses_max) || bad(o.credit_min, o.credit_max))) return "Check the semester answers.";
  return null;
}

const DAYS: DayCode[] = ["M", "T", "W", "H", "F", "S", "U"];

function Hours({ label, value, onChange }: { label: string; value: number; onChange: (v: number) => void }) {
  return (
    <Field label={label} hint="Hours a week; 0 if none.">
      {({ id, describedBy }) => (
        <Input id={id} aria-describedby={describedBy} type="number" inputMode="numeric" min={0} max={40}
          value={String(value)} onChange={(e) => onChange(Math.max(0, Math.min(40, Math.trunc(Number(e.target.value) || 0))))} />
      )}
    </Field>
  );
}

export function CommitmentFields({ meta, value, onChange, idPrefix }: {
  meta: PlanningProfileResponse; value: Commitments; onChange: (c: Commitments) => void; idPrefix: string;
}) {
  const set = (patch: Partial<Commitments>) => onChange({ ...value, ...patch });
  return (
    <div className="space-y-3">
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Do you have a job?">
          {({ id }) => (
            <Select id={id} value={String(value.job_hours_per_week)}
              onChange={(e) => set({ job_hours_per_week: Number(e.target.value) })}>
              {JOB_BANDS.map((b) => <option key={b.hours} value={b.hours}>{b.label}</option>)}
              {!JOB_BANDS.some((b) => b.hours === value.job_hours_per_week) && (
                <option value={value.job_hours_per_week}>{value.job_hours_per_week} hours a week</option>
              )}
            </Select>
          )}
        </Field>
        <Field label="Greek life">
          {({ id }) => (
            <Select id={id} value={value.greek_life} onChange={(e) => set({ greek_life: e.target.value as Commitments["greek_life"] })}>
              {(meta.greek_life ?? []).map((g) => (
                <option key={g.value} value={g.value}>{g.label}{g.hours ? ` (about ${g.hours} hrs/week)` : ""}</option>
              ))}
            </Select>
          )}
        </Field>
      </div>
      <fieldset className="space-y-1">
        <legend className="text-sm font-medium text-fg">Clubs and professional organizations</legend>
        <div className="flex flex-wrap gap-x-4">
          {(meta.clubs ?? []).map((c) => (
            <Radio key={c.value} name={`${idPrefix}-clubs`} label={c.hours ? `${c.label} (about ${c.hours} hrs/week)` : c.label}
              checked={value.clubs === c.value} onChange={() => set({ clubs: c.value })} />
          ))}
        </div>
      </fieldset>
      <div className="grid gap-4 sm:grid-cols-3">
        <Hours label="Athletics" value={value.athletics_hours_per_week} onChange={(v) => set({ athletics_hours_per_week: v })} />
        <Hours label="Research" value={value.research_hours_per_week} onChange={(v) => set({ research_hours_per_week: v })} />
        <Hours label="Recruiting" value={value.recruiting_hours_per_week} onChange={(v) => set({ recruiting_hours_per_week: v })} />
      </div>
      <p className="text-xs text-fg-secondary">{commitmentSummary(value)}.</p>
    </div>
  );
}

export function BlockedTimes({ value, onChange }: { value: BlockedTime[]; onChange: (v: BlockedTime[]) => void }) {
  const [kind, setKind] = useState<BlockKind>("job");
  const [days, setDays] = useState<DayCode[]>([]);
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const valid = days.length > 0 && Boolean(start) && Boolean(end) && start < end;
  return (
    <div className="space-y-3">
      {value.length > 0 && (
        <ul className="divide-y divide-border rounded border border-border">
          {value.map((b, i) => (
            <li key={`${b.kind}-${b.days.join("")}-${b.start}-${i}`} className="flex items-center justify-between gap-2 p-2 text-sm">
              <span>{blockedTimeText(b)}</span>
              <Button type="button" variant="ghost" size="sm" aria-label={`Remove ${blockedTimeText(b)}`}
                onClick={() => onChange(value.filter((_, j) => j !== i))}>
                <Trash2 aria-hidden className="size-4" />
              </Button>
            </li>
          ))}
        </ul>
      )}
      <div className="grid gap-3 sm:grid-cols-3">
        <Field label="What">
          {({ id }) => (
            <Select id={id} value={kind} onChange={(e) => setKind(e.target.value as BlockKind)}>
              {Object.entries(BLOCK_KIND).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </Select>
          )}
        </Field>
        <Field label="From">{({ id }) => <Input id={id} type="time" value={start} onChange={(e) => setStart(e.target.value)} />}</Field>
        <Field label="To" error={start && end && start >= end ? "Must end after it starts." : undefined}>
          {({ id, invalid }) => <Input id={id} aria-invalid={invalid} type="time" value={end} onChange={(e) => setEnd(e.target.value)} />}
        </Field>
      </div>
      <div className="flex flex-wrap gap-x-4">
        {DAYS.map((d) => (
          <Checkbox key={d} label={DAY_NAME[d]} checked={days.includes(d)}
            onChange={(e) => setDays(e.target.checked ? [...days, d] : days.filter((x) => x !== d))} />
        ))}
      </div>
      <Button type="button" variant="secondary" disabled={!valid || value.length >= 20}
        onClick={() => { onChange([...value, { kind, days: DAYS.filter((d) => days.includes(d)), start, end }]); setDays([]); setStart(""); setEnd(""); }}>
        <Plus aria-hidden className="size-4" /> Add time
      </Button>
    </div>
  );
}

export function TermOverrides({ meta, value, onChange, terms, level }: {
  meta: PlanningProfileResponse; value: TermAnswers[]; onChange: (v: TermAnswers[]) => void; terms: string[]; level: WorkloadLevel | null;
}) {
  const free = terms.filter((t) => !value.some((o) => o.term_code === t));
  const [term, setTerm] = useState(free[0] ?? "");
  const defaults = meta.workload_levels?.find((w) => w.value === (level ?? "balanced"));
  const update = (i: number, patch: Partial<TermAnswers>) => onChange(value.map((o, j) => (j === i ? { ...o, ...patch } : o)));
  return (
    <div className="space-y-3">
      {value.map((o, i) => {
        const lo = o.major_courses_min, hi = o.major_courses_max;
        const rangeError = lo !== null && hi !== null && lo > hi ? "The minimum can't be above the maximum." : undefined;
        return (
          <div key={o.term_code} className="space-y-3 rounded border border-border p-3">
            <div className="flex items-center justify-between gap-2">
              <p className="text-sm font-semibold text-fg">{termLabel(o.term_code)}</p>
              <Button type="button" variant="ghost" size="sm" aria-label={`Remove answers for ${termLabel(o.term_code)}`}
                onClick={() => onChange(value.filter((_, j) => j !== i))}>
                <Trash2 aria-hidden className="size-4" />
              </Button>
            </div>
            <Checkbox label="Set an exact number of major-related courses for this semester"
              checked={lo !== null}
              onChange={(e) => update(i, e.target.checked
                ? { major_courses_min: defaults?.major_min ?? 2, major_courses_max: defaults?.major_max ?? 3 }
                : { major_courses_min: null, major_courses_max: null })} />
            {lo !== null && hi !== null && (
              <div className="grid gap-3 sm:grid-cols-2">
                <Field label="At least" error={rangeError}>
                  {({ id, invalid }) => <Input id={id} aria-invalid={invalid} type="number" min={0} max={8} value={String(lo)}
                    onChange={(e) => update(i, { major_courses_min: Math.max(0, Math.min(8, Math.trunc(Number(e.target.value) || 0))) })} />}
                </Field>
                <Field label="At most">
                  {({ id }) => <Input id={id} type="number" min={0} max={8} value={String(hi)}
                    onChange={(e) => update(i, { major_courses_max: Math.max(0, Math.min(8, Math.trunc(Number(e.target.value) || 0))) })} />}
                </Field>
              </div>
            )}
            <Checkbox label="My commitments are different this semester" checked={o.commitments !== null}
              onChange={(e) => update(i, { commitments: e.target.checked ? { ...NO_COMMITMENTS } : null })} />
            {o.commitments && (
              <CommitmentFields meta={meta} idPrefix={`term-${o.term_code}`} value={o.commitments}
                onChange={(c) => update(i, { commitments: c })} />
            )}
            <Checkbox label="My fixed weekly times are different this semester" checked={o.blocked_times !== null}
              onChange={(e) => update(i, { blocked_times: e.target.checked ? [] : null })} />
            {o.blocked_times && <BlockedTimes value={o.blocked_times} onChange={(b) => update(i, { blocked_times: b })} />}
          </div>
        );
      })}
      {free.length > 0 && value.length < 16 && (
        <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
          <div className="max-w-xs flex-1">
            <Field label="Semester">
              {({ id }) => (
                <Select id={id} value={free.includes(term) ? term : free[0]} onChange={(e) => setTerm(e.target.value)}>
                  {free.map((t) => <option key={t} value={t}>{termLabel(t)}</option>)}
                </Select>
              )}
            </Field>
          </div>
          <Button type="button" variant="secondary"
            onClick={() => onChange([...value, { term_code: free.includes(term) ? term : free[0]!, major_courses_min: null,
              major_courses_max: null, commitments: null, blocked_times: null }])}>
            <Plus aria-hidden className="size-4" /> Answer for this semester
          </Button>
        </div>
      )}
    </div>
  );
}

export function PinFields({ value, onChange, terms }: { value: CoursePin[]; onChange: (v: CoursePin[]) => void; terms: string[] }) {
  const [course, setCourse] = useState("");
  const [term, setTerm] = useState(terms[0] ?? "");
  const valid = /^\d{2}:\d{3}:\d{3}$/.test(course.trim());
  return (
    <div className="space-y-3">
      {value.length > 0 && (
        <ul className="divide-y divide-border rounded border border-border">
          {value.map((p, i) => (
            <li key={`${p.course}-${p.term_code}`} className="flex items-center justify-between gap-2 p-2 text-sm">
              <span className="code">{p.course} <span className="text-fg-muted">in {termLabel(p.term_code)}</span></span>
              <Button type="button" variant="ghost" size="sm" aria-label={`Unpin ${p.course}`} onClick={() => onChange(value.filter((_, j) => j !== i))}>
                <Trash2 aria-hidden className="size-4" />
              </Button>
            </li>
          ))}
        </ul>
      )}
      <div className="grid gap-3 sm:grid-cols-[1fr_12rem_auto] sm:items-end">
        <Field label="Course" hint="e.g. 01:640:152">
          {({ id, describedBy }) => <Input id={id} aria-describedby={describedBy} value={course} placeholder="01:640:152" onChange={(e) => setCourse(e.target.value)} />}
        </Field>
        <Field label="Semester">
          {({ id }) => (
            <Select id={id} value={term} onChange={(e) => setTerm(e.target.value)}>
              {terms.map((t) => <option key={t} value={t}>{termLabel(t)}</option>)}
            </Select>
          )}
        </Field>
        <Button type="button" variant="secondary" disabled={!valid || value.length >= 24}
          onClick={() => { const pin = { course: course.trim(), term_code: term }; if (!value.some((p) => p.course === pin.course)) onChange([...value, pin]); setCourse(""); }}>
          <Plus aria-hidden className="size-4" /> Add
        </Button>
      </div>
    </div>
  );
}

export function CreditRange({ min, max, levelText, onChange, name }: {
  min: number | null; max: number | null; levelText: string; name: string;
  onChange: (min: number | null, max: number | null) => void;
}) {
  return (
    <div className="space-y-2">
      <Radio name={name} label={`Use my level's range (${levelText})`} checked={min === null || max === null} onChange={() => onChange(null, null)} />
      <Radio name={name} label="Set my own" checked={min !== null && max !== null} onChange={() => onChange(15, 18)} />
      {min !== null && max !== null && (
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="At least (a hard minimum)" error={min > max ? "The minimum can't be above the maximum." : undefined}>
            {({ id, invalid }) => <Input id={id} aria-invalid={invalid} type="number" min={1} max={24} value={String(min)}
              onChange={(e) => onChange(Math.max(1, Math.min(24, Math.trunc(Number(e.target.value) || 1))), max)} />}
          </Field>
          <Field label="At most">
            {({ id }) => <Input id={id} type="number" min={1} max={24} value={String(max)}
              onChange={(e) => onChange(min, Math.max(1, Math.min(24, Math.trunc(Number(e.target.value) || 1))))} />}
          </Field>
        </div>
      )}
    </div>
  );
}

type SectionProps = { meta: PlanningProfileResponse; value: QuestionnaireValue; onChange: (v: QuestionnaireValue) => void };
type ValueProps = Omit<SectionProps, "meta">;

/** Credits and major-related courses per semester (Intensity). */
export function RangeQuestions({ meta, value, onChange, level, pace }: SectionProps & { level: WorkloadLevel | null; pace: Pace | null }) {
  const conflict = paceLevelConflict(pace, level);
  const range = meta.workload_levels?.find((w) => w.value === (level ?? "balanced"));
  const levelMajor = range ? (range.major_min === range.major_max ? `${range.major_min}` : `${range.major_min}-${range.major_max}`) : "";
  const focus = MAJOR_FOCUS.find((f) => f.min === value.major_min && f.max === value.major_max) ?? MAJOR_FOCUS[0]!;
  return (
    <div className="space-y-6">
      {conflict && <Alert tone="warning">{conflict}</Alert>}
      <fieldset className="space-y-2">
        <legend className="text-base font-semibold text-fg">How many credits a semester? (optional)</legend>
        <p className="text-sm text-fg-muted">The minimum is kept every semester; if a semester can&apos;t reach it, the plan says why. {meta.disclaimer}</p>
        <CreditRange name="credits" min={value.credit_min} max={value.credit_max}
          levelText={range ? `${Number(range.credit_floor)}-${Number(range.credit_max)}` : "the level's"}
          onChange={(lo, hi) => onChange({ ...value, credit_min: lo, credit_max: hi })} />
      </fieldset>
      <fieldset className="space-y-1">
        <legend className="text-base font-semibold text-fg">How many major courses a semester? (optional)</legend>
        <p className="text-sm text-fg-muted">{meta.major_related_text}</p>
        <div className="flex flex-wrap gap-x-4">
          {MAJOR_FOCUS.map((f) => (
            <Radio key={f.key} name="major-focus" label={f.key === "level" ? `${f.label} (${levelMajor})` : f.label}
              checked={focus.key === f.key} onChange={() => onChange({ ...value, major_min: f.min, major_max: f.max })} />
          ))}
        </div>
      </fieldset>
    </div>
  );
}

/** Pins (Musts). Shown only where the value carries pins. */
export function PinQuestion({ value, onChange, terms }: ValueProps & { terms: string[] }) {
  if (!value.pins) return null;
  return (
    <fieldset className="space-y-2">
      <legend className="text-base font-semibold text-fg">Any course you need to retake or must take in a semester? (optional)</legend>
      <p className="text-sm text-fg-muted">Retakes from your record are added for you. A course you add here goes in that semester or not at all, and your plan says why.</p>
      <PinFields value={value.pins} terms={terms} onChange={(p) => onChange({ ...value, pins: p })} />
    </fieldset>
  );
}

/** Commitments and honors (Your week). */
export function WeekQuestions({ meta, value, onChange }: SectionProps) {
  return (
    <div className="space-y-6">
      <fieldset className="space-y-2">
        <legend className="text-base font-semibold text-fg">What else takes your week? (optional)</legend>
        {meta.commitments_text && <p className="text-sm text-fg-muted">{meta.commitments_text}</p>}
        <CommitmentFields meta={meta} idPrefix="usual" value={value.commitments}
          onChange={(c) => onChange({ ...value, commitments: c })} />
      </fieldset>
      <HonorsQuestion value={value} onChange={onChange} />
    </div>
  );
}

export function HonorsQuestion({ value, onChange }: ValueProps) {
  return (
    <fieldset className="space-y-1">
      <legend className="text-base font-semibold text-fg">Are you in an honors program? (optional)</legend>
      <p className="text-sm text-fg-muted">Courses open only to honors programs are planned only if you are in one. Coursenav can&apos;t verify membership, so they still need your confirmation.</p>
      <Radio name="honors" label="Yes" checked={value.honors === true} onChange={() => onChange({ ...value, honors: true })} />
      <Radio name="honors" label="No" checked={value.honors === false} onChange={() => onChange({ ...value, honors: false })} />
      <Radio name="honors" label="Skip (treated as no)" checked={value.honors === null} onChange={() => onChange({ ...value, honors: null })} />
    </fieldset>
  );
}

/** Times off, campuses and fixed weekly times (Schedule shape). */
export function ShapeQuestions({ value, onChange }: ValueProps) {
  const shape = value.schedule_shape;
  return (
    <div className="space-y-6">
      {shape && (
        <fieldset className="space-y-1">
          <legend className="text-base font-semibold text-fg">When do you NOT want class? (optional)</legend>
          <Checkbox label="Before 10 AM" checked={shape.earliest_start === "10:00"}
            onChange={(e) => onChange({ ...value, schedule_shape: { ...shape, earliest_start: e.target.checked ? "10:00" : null } })} />
          <Checkbox label="Fridays" checked={shape.avoid_days.includes("F")}
            onChange={(e) => onChange({ ...value, schedule_shape: { ...shape, avoid_days: e.target.checked ? [...shape.avoid_days, "F"] : shape.avoid_days.filter((d) => d !== "F") } })} />
          <Checkbox label="Evenings (after 6 PM)" checked={shape.latest_end === "18:00"}
            onChange={(e) => onChange({ ...value, schedule_shape: { ...shape, latest_end: e.target.checked ? "18:00" : null } })} />
        </fieldset>
      )}
      <CampusQuestion value={value} onChange={onChange} />
      <BlockedTimesQuestion value={value} onChange={onChange} />
    </div>
  );
}

export function BlockedTimesQuestion({ value, onChange }: ValueProps) {
  return (
    <fieldset className="space-y-2">
      <legend className="text-base font-semibold text-fg">Fixed weekly times (optional)</legend>
      <p className="text-sm text-fg-muted">Job shifts, club meetings and other set times. When you build a schedule, no class is placed during them.</p>
      <BlockedTimes value={value.blocked_times} onChange={(b) => onChange({ ...value, blocked_times: b })} />
    </fieldset>
  );
}

export function CampusQuestion({ value, onChange }: ValueProps) {
  return (
    <fieldset className="space-y-1">
      <legend className="text-base font-semibold text-fg">Preferred campuses (optional)</legend>
      <p className="text-sm text-fg-muted">Schedules on these campuses are listed first; no section is ruled out.</p>
      <div className="grid grid-cols-2 sm:grid-cols-3">
        {CAMPUSES.map((c) => (
          <Checkbox key={c.code} label={c.label} checked={value.preferred_campuses.includes(c.code)}
            onChange={(e) => onChange({ ...value, preferred_campuses: e.target.checked
              ? [...value.preferred_campuses, c.code] : value.preferred_campuses.filter((x) => x !== c.code) })} />
        ))}
      </div>
    </fieldset>
  );
}

/** One semester that's different. */
export function TermOverrideQuestion({ meta, value, onChange, terms, level }: SectionProps & { terms: string[]; level: WorkloadLevel | null }) {
  return (
    <fieldset className="space-y-2">
      <legend className="text-base font-semibold text-fg">A semester that&apos;s different (optional)</legend>
      <p className="text-sm text-fg-muted">Change the number of major-related courses, your commitments or your fixed times for one semester only.</p>
      <TermOverrides meta={meta} value={value.term_overrides} terms={terms} level={level}
        onChange={(t) => onChange({ ...value, term_overrides: t })} />
    </fieldset>
  );
}

export function ClearAnswers({ value, onChange }: ValueProps) {
  return (
    <div>
      <Button type="button" variant="ghost" size="sm" onClick={() => onChange(clearedAnswers(value))}>
        <Trash2 aria-hidden className="size-4" /> Clear my answers
      </Button>
      <p className="text-xs text-fg-muted">Sets every questionnaire answer back to its default. Saved when you save.</p>
    </div>
  );
}

/** The whole questionnaire in one column. */
export function QuestionnaireFields({ meta, value, onChange, terms, level, pace }: SectionProps & {
  /** Semesters the student may give separate answers for. */
  terms: string[];
  level: WorkloadLevel | null;
  pace: Pace | null;
}) {
  return (
    <div className="space-y-6">
      <RangeQuestions meta={meta} value={value} onChange={onChange} level={level} pace={pace} />
      <PinQuestion value={value} onChange={onChange} terms={terms} />
      <WeekQuestions meta={meta} value={value} onChange={onChange} />
      <ShapeQuestions value={value} onChange={onChange} />
      <TermOverrideQuestion meta={meta} value={value} onChange={onChange} terms={terms} level={level} />
      <ClearAnswers value={value} onChange={onChange} />
    </div>
  );
}
