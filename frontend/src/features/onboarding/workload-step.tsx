"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowRight } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Alert, Skeleton } from "@/components/ui/feedback";
import { Radio } from "@/components/ui/field";
import { ApiError, api } from "@/lib/api";
import type { FillerPreference, WorkloadLevel } from "@/types/personalization";
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

  async function save() {
    setSaving(true);
    setError(null);
    try {
      await api.confirmPlanningProfile({
        ...base!, pace: chosenPace, workload_level: chosenLevel, filler_preference: chosenFiller,
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
        <p className="text-sm text-fg-secondary">You can change all of this later in Planning preferences.</p>
      </div>
      <fieldset className="space-y-2">
        <legend className="text-base font-semibold text-fg">When do you want to graduate?</legend>
        {meta.data.paces.map((p) => (
          <Radio key={p.value} name="pace" label={p.label} description={p.description}
            checked={chosenPace === p.value} onChange={() => setPace(p.value)} />
        ))}
      </fieldset>
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
      {error && <Alert tone="danger">{error}</Alert>}
      <div className="flex justify-between">
        <Button variant="ghost" onClick={onDone}>Skip</Button>
        <Button variant="primary" loading={saving} onClick={save}>
          Save <ArrowRight aria-hidden className="size-4" />
        </Button>
      </div>
    </section>
  );
}
