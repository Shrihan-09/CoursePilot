"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowRight } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Alert, Skeleton } from "@/components/ui/feedback";
import { Field, Radio, Select } from "@/components/ui/field";
import { answersInvalid, QuestionnaireFields, type QuestionnaireValue } from "@/features/planning/questionnaire-fields";
import { termLabel, upcomingTerms } from "@/lib/academic/vocabulary";
import { ApiError, api } from "@/lib/api";
import { NO_COMMITMENTS, type FillerPreference, type WorkloadLevel } from "@/types/personalization";
import type { Pace } from "@/types/plan";

/**
 * Onboarding (Phase 7.3): when to graduate, how intense each semester should
 * be, and how to fill the rest. Saved as the planning profile (the same PUT
 * the preferences page uses); everything else keeps the profile's defaults
 * and stays editable under Planning preferences.
 */
export function WorkloadStep({ onDone }: { onDone: () => void }) {
  const meta = useQuery({ queryKey: ["planning-profile"], queryFn: api.planningProfile });
  const base = meta.data?.profile ?? meta.data?.defaults;
  const [pace, setPace] = useState<Pace | null>(null);
  const [level, setLevel] = useState<WorkloadLevel | null>(null);
  const [filler, setFiller] = useState<FillerPreference | null>(null);
  const [gradTerm, setGradTerm] = useState<string | null | undefined>(undefined);
  const [answers, setAnswers] = useState<QuestionnaireValue | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!meta.data || !base) {
    return meta.isError
      ? <Alert tone="danger">Your planning settings couldn&apos;t be loaded. You can set them later.</Alert>
      : <Skeleton className="h-40" />;
  }
  const chosenPace = pace ?? base.pace;
  const chosenLevel = level ?? base.workload_level ?? "balanced";
  const chosenFiller = filler ?? base.filler_preference ?? "no_preference";
  const chosenGrad = gradTerm === undefined ? (base.expected_graduation_term ?? null) : gradTerm;
  const gradChoices = upcomingTerms(new Date(), 14).filter((t) => t.endsWith("1") || t.endsWith("9"));
  const chosenAnswers: QuestionnaireValue = answers ?? {
    commitments: base.commitments ?? NO_COMMITMENTS, honors: base.honors ?? null,
    blocked_times: base.schedule.blocked_times ?? [], term_overrides: base.term_overrides ?? [],
    credit_min: base.credit_min ?? null, credit_max: base.credit_max ?? null,
    major_min: base.major_courses_min ?? null, major_max: base.major_courses_max ?? null,
    preferred_campuses: base.schedule.preferred_campuses ?? [],
    schedule_shape: { earliest_start: base.schedule.earliest_start, latest_end: base.schedule.latest_end,
                      avoid_days: base.schedule.avoid_days },
    pins: base.pins ?? [],
  };
  const invalid = answersInvalid(chosenAnswers);

  async function save() {
    setSaving(true);
    setError(null);
    try {
      await api.confirmPlanningProfile({
        ...base!, pace: chosenPace, workload_level: chosenLevel, filler_preference: chosenFiller,
        expected_graduation_term: chosenGrad,
        schedule: { ...base!.schedule, ...chosenAnswers.schedule_shape, blocked_times: chosenAnswers.blocked_times,
                    preferred_campuses: chosenAnswers.preferred_campuses },
        commitments: chosenAnswers.commitments, honors: chosenAnswers.honors,
        term_overrides: chosenAnswers.term_overrides, pins: chosenAnswers.pins ?? [],
        credit_min: chosenAnswers.credit_min, credit_max: chosenAnswers.credit_max,
        major_courses_min: chosenAnswers.major_min, major_courses_max: chosenAnswers.major_max,
      });
      onDone();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "That didn't go through. Please try again.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <section aria-labelledby="workload-title" className="space-y-6">
      <div className="space-y-1">
        <h2 id="workload-title" className="text-xl font-semibold tracking-tight text-fg">Your semesters</h2>
        <p className="text-sm text-fg-secondary">Every question is optional - skip any you like. You can change all of this later from the Plan page.</p>
      </div>
      <fieldset className="space-y-2">
        <legend className="text-base font-semibold text-fg">When do you want to graduate?</legend>
        {meta.data.paces.map((p) => (
          <Radio key={p.value} name="pace" label={p.label} description={p.description}
            checked={chosenPace === p.value} onChange={() => setPace(p.value)} />
        ))}
      </fieldset>
      <Field label="Expected graduation term (optional)"
             hint="If you know it, this overrides the choice above. Otherwise On time counts 8 semesters from your first one.">
        {({ id, describedBy }) => (
          <Select id={id} aria-describedby={describedBy} value={chosenGrad ?? ""}
                  onChange={(e) => setGradTerm(e.target.value || null)}>
            <option value="">Not sure</option>
            {gradChoices.map((t) => <option key={t} value={t}>{termLabel(t)}</option>)}
          </Select>
        )}
      </Field>
      <fieldset className="space-y-2">
        <legend className="text-base font-semibold text-fg">How intense should each semester be?</legend>
        {(meta.data.workload_levels ?? []).map((w) => (
          <Radio key={w.value} name="workload_level" label={w.label} description={w.description}
            checked={chosenLevel === w.value} onChange={() => setLevel(w.value)} />
        ))}
        {meta.data.workload_weight && <p className="text-xs text-fg-muted">{meta.data.workload_weight}</p>}
      </fieldset>
      <fieldset className="space-y-2">
        <legend className="text-base font-semibold text-fg">For the rest of a semester&apos;s credits</legend>
        {(meta.data.filler_preferences ?? []).map((f) => (
          <Radio key={f.value} name="filler_preference" label={f.label} description={f.description}
            checked={chosenFiller === f.value} onChange={() => setFiller(f.value)} />
        ))}
      </fieldset>
      <QuestionnaireFields meta={meta.data} value={chosenAnswers} onChange={setAnswers}
        terms={upcomingTerms(new Date(), 8)} level={chosenLevel} pace={chosenPace} />
      {(error || invalid) && <Alert tone="danger">{error ?? invalid}</Alert>}
      <div className="flex justify-between">
        <Button variant="ghost" onClick={onDone}>Skip</Button>
        <Button variant="primary" loading={saving} disabled={Boolean(invalid)} onClick={save}>
          Save <ArrowRight aria-hidden className="size-4" />
        </Button>
      </div>
    </section>
  );
}
