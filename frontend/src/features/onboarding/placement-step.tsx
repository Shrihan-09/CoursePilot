"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Field, Input } from "@/components/ui/field";
import { PLACEMENT_POLICY_URL } from "@/lib/academic/vocabulary";
import { ApiError, api } from "@/lib/api";

const COURSE = /^\d{2}:640:\d{3}$/;

/**
 * Optional: the math course the student's placement result places them into.
 * Placement only meets prerequisites - it never earns credit or satisfies a
 * degree requirement - and plans show it, so a wrong entry is easy to spot.
 */
export function PlacementStep({ initial, onDone, editing = false }: {
  initial?: string | null;
  onDone: () => void;
  /** On the profile page: no "Skip", the button always saves. */
  editing?: boolean;
}) {
  const [value, setValue] = useState(initial ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const trimmed = value.trim();
  const malformed = trimmed !== "" && !COURSE.test(trimmed);

  async function save() {
    setSaving(true);
    setError(null);
    try {
      await api.setPlacement(trimmed === "" ? null : trimmed);
      onDone();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "That didn't go through. Please try again.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <section aria-labelledby="placement-title" className="space-y-5">
      <div className="space-y-1">
        <h2 id="placement-title" className="text-xl font-semibold tracking-tight text-fg">
          Math placement <span className="text-sm font-normal text-fg-muted">(optional)</span>
        </h2>
        <p className="text-sm text-fg-secondary">
          If you took the Rutgers math placement test, enter the course it places you into. Rutgers
          lets placement meet a math course&apos;s prerequisites (
          <a className="underline underline-offset-2" href={PLACEMENT_POLICY_URL} target="_blank" rel="noreferrer">
            Mathematics Department placement policy
          </a>
          ), so your plan won&apos;t add the courses below it. It never counts as credit.
        </p>
      </div>
      <Field label="Placed into" hint="A math course code, e.g. 01:640:151. Leave empty if you don't know."
             error={malformed ? "Use the full course code, e.g. 01:640:151." : error ?? undefined}>
        {({ id, describedBy, invalid }) => (
          <Input id={id} aria-describedby={describedBy} aria-invalid={invalid} inputMode="numeric"
                 placeholder="01:640:151" value={value} onChange={(e) => setValue(e.target.value)} />
        )}
      </Field>
      <div className="flex justify-between">
        {editing ? <span /> : <Button variant="ghost" onClick={onDone}>Skip</Button>}
        <Button variant="primary" loading={saving} disabled={malformed} onClick={save}>
          {editing ? "Save" : trimmed === "" ? "Continue" : "Save placement"}
          {!editing && <ArrowRight aria-hidden className="size-4" />}
        </Button>
      </div>
    </section>
  );
}

/** Profile page: view and change the stated placement. */
export function PlacementEditor() {
  const client = useQueryClient();
  const profile = useQuery({ queryKey: ["profile"], queryFn: api.profile });
  if (!profile.data) return null;
  return (
    <div className="rounded-xl border border-border bg-surface-elevated p-4">
      <PlacementStep editing initial={profile.data.math_placement}
                     onDone={() => client.invalidateQueries({ queryKey: ["profile"] })} />
    </div>
  );
}
