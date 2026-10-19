"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { CalendarDays, Plus, X } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { StateBadge } from "@/components/academic/status";
import { Button, buttonVariants } from "@/components/ui/button";
import { Alert, EmptyState, ErrorState, PageHeader, Skeleton } from "@/components/ui/feedback";
import { Checkbox, Field, Input, Radio, Select } from "@/components/ui/field";
import { Tabs } from "@/components/ui/overlay";
import { Comparison, IssueList, OptionDetails, RankingNote } from "@/features/schedule/option-view";
import { WeekCalendar } from "@/features/schedule/week-calendar";
import { ApiError, api } from "@/lib/api";
import { DAY_NAME } from "@/lib/academic/schedule-time";
import { SCHEDULE_STATUS, termLabel } from "@/lib/academic/vocabulary";
import type { DayCode, SchedulePreferences } from "@/types/schedule";

/**
 * Schedule: "Which actual Rutgers sections can I take together?" - answered
 * by POST /student/schedules/generate, the deterministic Schedule Engine.
 *
 * Courses come from a semester of the SAVED plan (by reference: the plan on
 * the server is the source of truth) or are chosen explicitly. A planned
 * term with no Rutgers section data is said to be unpublished; it is never
 * quietly swapped for another term.
 */

const COURSE = /^\d{2}:\d{3}:\d{3}$/;
const WEEKDAYS: DayCode[] = ["M", "T", "W", "H", "F", "S"];

const DEFAULT_PREFS: SchedulePreferences = {
  earliest_start: null, latest_end: null, avoid_days: [], minimum_minutes_between_classes: 0,
  preferred_campuses: [], availability: "prefer_open",
};

function Settings({ prefs, onChange }: { prefs: SchedulePreferences; onChange: (p: SchedulePreferences) => void }) {
  return (
    <details className="rounded border border-border">
      <summary className="min-h-11 cursor-pointer p-3 text-sm font-medium text-fg">Schedule settings</summary>
      <div className="space-y-4 border-t border-border p-3">
        <p className="text-xs text-fg-muted">
          Hard limits: a section outside them is never used. These are your CoursePilot settings, not Rutgers rules.
        </p>
        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="No classes before">
            {({ id }) => <Input id={id} type="time" value={prefs.earliest_start ?? ""} onChange={(e) => onChange({ ...prefs, earliest_start: e.target.value || null })} />}
          </Field>
          <Field label="No classes after">
            {({ id }) => <Input id={id} type="time" value={prefs.latest_end ?? ""} onChange={(e) => onChange({ ...prefs, latest_end: e.target.value || null })} />}
          </Field>
          <Field label="Minutes between classes, at least">
            {({ id }) => (
              <Input id={id} type="number" min={0} max={120} inputMode="numeric" value={prefs.minimum_minutes_between_classes}
                onChange={(e) => onChange({ ...prefs, minimum_minutes_between_classes: Math.max(0, Math.min(120, Number(e.target.value) || 0)) })} />
            )}
          </Field>
        </div>
        <fieldset>
          <legend className="text-sm font-medium text-fg">Days without classes</legend>
          <div className="grid grid-cols-2 sm:grid-cols-3">
            {WEEKDAYS.map((d) => (
              <Checkbox key={d} label={DAY_NAME[d]} checked={prefs.avoid_days.includes(d)}
                onChange={(e) => onChange({ ...prefs, avoid_days: e.target.checked ? [...prefs.avoid_days, d] : prefs.avoid_days.filter((x) => x !== d) })} />
            ))}
          </div>
        </fieldset>
        <fieldset>
          <legend className="text-sm font-medium text-fg">Closed sections</legend>
          <Radio name="availability" label="Allow closed sections, list open ones first" checked={prefs.availability === "prefer_open"} onChange={() => onChange({ ...prefs, availability: "prefer_open" })} />
          <Radio name="availability" label="Only sections open now" description="Uses CoursePilot's latest Rutgers check; seats change constantly." checked={prefs.availability === "require_open"} onChange={() => onChange({ ...prefs, availability: "require_open" })} />
        </fieldset>
      </div>
    </details>
  );
}

function CourseChips({ courses, onRemove }: { courses: string[]; onRemove?: (c: string) => void }) {
  return (
    <ul className="flex flex-wrap gap-2" aria-label="Courses">
      {courses.map((c) => (
        <li key={c} className="code inline-flex h-10 items-center gap-1 rounded border border-border-strong bg-surface px-2 text-sm text-fg">
          {c}
          {onRemove && (
            <button type="button" onClick={() => onRemove(c)} aria-label={`Remove ${c}`} className="-mr-1 grid size-8 place-items-center rounded text-fg-muted hover:bg-surface-hover">
              <X aria-hidden className="size-3.5" />
            </button>
          )}
        </li>
      ))}
    </ul>
  );
}

function SchedulePageInner() {
  const params = useSearchParams();
  const context = useQuery({ queryKey: ["planning-context"], queryFn: api.planningContext });
  const current = useQuery({ queryKey: ["plan"], queryFn: api.currentPlan, staleTime: 0 });
  const plan = current.data?.plan ?? null;

  const [source, setSource] = useState<"plan" | "custom">("plan");
  const [planTerm, setPlanTerm] = useState(params.get("term") ?? "");
  const [customTerm, setCustomTerm] = useState("");
  const [customCourses, setCustomCourses] = useState<string[]>([]);
  const [draft, setDraft] = useState("");
  const [prefs, setPrefs] = useState<SchedulePreferences>(DEFAULT_PREFS);
  const [rank, setRank] = useState(1);

  useEffect(() => {
    if (current.isSuccess && !plan) setSource("custom");
    if (plan && !planTerm && plan.result.terms[0]) setPlanTerm(plan.result.terms[0].term_code);
  }, [current.isSuccess, plan, planTerm]);

  const generate = useMutation({ mutationFn: api.schedules, onSuccess: () => setRank(1) });

  if (context.isPending || current.isPending) {
    return <div className="space-y-6" aria-label="Loading"><Skeleton className="h-10 w-64" /><Skeleton className="h-40" /></div>;
  }
  if (context.isError || current.isError) {
    return <ErrorState title="Schedules couldn't be loaded" onRetry={() => { void context.refetch(); void current.refetch(); }} />;
  }

  const loaded = context.data.loaded_terms;
  const isLoaded = (t: string) => loaded.some((l) => l.term_code === t);
  const semester = plan?.result.terms.find((t) => t.term_code === planTerm) ?? null;
  const term = source === "plan" ? planTerm : customTerm;
  const courses = source === "plan" ? (semester?.courses.map((c) => c.course) ?? []) : customCourses;
  const canGenerate = Boolean(term) && courses.length > 0 && courses.length <= 8;
  const result = generate.data;
  const option = result?.options.find((o) => o.rank === rank) ?? result?.options[0];

  const addCourse = () => {
    const code = draft.trim();
    if (COURSE.test(code) && !customCourses.includes(code) && customCourses.length < 8) {
      setCustomCourses([...customCourses, code]);
      setDraft("");
      generate.reset();
    }
  };

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Schedule"
        title="Build a schedule"
        description="Real Rutgers sections for a term, combined by CoursePilot's schedule engine without time conflicts."
      />

      <Tabs
        label="Courses from"
        value={source}
        onChange={(v) => { setSource(v); generate.reset(); }}
        items={[{ value: "plan", label: "From my plan" }, { value: "custom", label: "Choose courses" }]}
      />

      {source === "plan" ? (
        !plan ? (
          <EmptyState icon={CalendarDays} title="No saved plan" action={<Link href="/plan" className={buttonVariants({ variant: "primary", size: "sm" })}>Build a plan</Link>}>
            Build a plan first, or choose courses yourself.
          </EmptyState>
        ) : (
          <div className="space-y-4 rounded-lg border border-border bg-surface-elevated p-4">
            {plan.freshness.state === "stale" && (
              <Alert tone="warning" title="Your plan is out of date" action={<Link href="/plan" className={buttonVariants({ size: "sm" })}>Review plan</Link>}>
                Its courses may no longer be right. Regenerate it before relying on this schedule.
              </Alert>
            )}
            <Field label="Semester">
              {({ id }) => (
                <Select id={id} value={planTerm} onChange={(e) => { setPlanTerm(e.target.value); generate.reset(); }}>
                  {plan.result.terms.map((t) => <option key={t.term_code} value={t.term_code}>{t.label}</option>)}
                </Select>
              )}
            </Field>
            {semester && <CourseChips courses={semester.courses.map((c) => c.course)} />}
            {semester && !isLoaded(semester.term_code) && (
              <Alert
                tone="warning"
                title={`Rutgers hasn't published ${semester.label} sections in CoursePilot's data yet`}
                action={
                  <Button size="sm" variant="secondary" onClick={() => { setSource("custom"); setCustomCourses(semester.courses.map((c) => c.course).slice(0, 8)); setCustomTerm(""); generate.reset(); }}>
                    Explore another term
                  </Button>
                }
              >
                A real schedule for {semester.label} isn&apos;t possible until those sections exist. You can explore
                these courses against a published term instead - you&apos;ll choose which.
              </Alert>
            )}
          </div>
        )
      ) : (
        <div className="space-y-4 rounded-lg border border-border bg-surface-elevated p-4">
          <Field label="Term" hint="Only terms with Rutgers section data are listed.">
            {({ id, describedBy }) => (
              <Select id={id} aria-describedby={describedBy} value={customTerm} onChange={(e) => { setCustomTerm(e.target.value); generate.reset(); }}>
                <option value="">Choose a term</option>
                {[...loaded].reverse().map((t) => (
                  <option key={t.term_code} value={t.term_code}>{t.label}{t.coverage === "partial" ? " (partial data)" : ""}</option>
                ))}
              </Select>
            )}
          </Field>
          <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); addCourse(); }}>
            <div className="flex-1">
              <Field label="Add a course" hint="Rutgers course code, like 01:198:111. Up to 8.">
                {({ id, describedBy }) => (
                  <Input id={id} aria-describedby={describedBy} value={draft} placeholder="01:198:111" inputMode="numeric" onChange={(e) => setDraft(e.target.value)} />
                )}
              </Field>
            </div>
            <Button type="submit" className="mt-7" disabled={!COURSE.test(draft.trim()) || customCourses.length >= 8} aria-label="Add course">
              <Plus aria-hidden className="size-4" />
            </Button>
          </form>
          {customCourses.length > 0 && (
            <CourseChips courses={customCourses} onRemove={(c) => { setCustomCourses(customCourses.filter((x) => x !== c)); generate.reset(); }} />
          )}
        </div>
      )}

      <Settings prefs={prefs} onChange={(p) => { setPrefs(p); generate.reset(); }} />

      <Button
        variant="primary"
        disabled={!canGenerate}
        loading={generate.isPending}
        onClick={() => generate.mutate({ term, courses, preferences: prefs, max_results: 10 })}
      >
        Find schedules{term ? ` for ${termLabel(term)}` : ""}
      </Button>

      {generate.isError && (
        <Alert tone="danger" title="Schedules could not be generated">
          {generate.error instanceof ApiError && generate.error.status < 500
            ? generate.error.message
            : "The schedule engine hit a problem. Please try again."}
        </Alert>
      )}

      {generate.isPending && <Skeleton className="h-72" />}

      {result && (
        <section aria-labelledby="results-heading" className="space-y-4">
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
            <h2 id="results-heading" className="text-base font-semibold text-fg">
              {result.options.length ? `${result.options.length} ${result.options.length === 1 ? "option" : "options"} for ${termLabel(result.term_code)}` : termLabel(result.term_code)}
            </h2>
            <StateBadge state={SCHEDULE_STATUS[result.status]} />
          </div>
          {result.status !== "options_found" && (
            <p className="text-sm text-fg-secondary">{SCHEDULE_STATUS[result.status].meaning}</p>
          )}
          <IssueList issues={result.issues} />

          {option && (
            <>
              <RankingNote objective={result.metadata.ranking_objective} />
              {result.options.length > 1 && <Comparison options={result.options} selected={option.rank} onSelect={setRank} />}
              <h3 className="text-base font-semibold text-fg">Option {option.rank}</h3>
              <WeekCalendar key={option.rank} option={option} />
              <OptionDetails option={option} result={result} />
            </>
          )}
        </section>
      )}
    </div>
  );
}

export default function SchedulePage() {
  return (
    <Suspense fallback={<Skeleton className="h-40" />}>
      <SchedulePageInner />
    </Suspense>
  );
}
