"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Field, Select } from "@/components/ui/field";
import { termLabel } from "@/lib/academic/vocabulary";
import { ApiError, api } from "@/lib/api";
import { firstTermChoices } from "@/lib/setup";

/** Prompt 10a: the first Rutgers semester the student stated in setup, editable. Saved on change. */
export function FirstTermEditor() {
  const client = useQueryClient();
  const status = useQuery({ queryKey: ["onboarding"], queryFn: api.onboarding });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!status.data?.profile) return null;
  const current = status.data.profile.first_term ?? "";

  async function save(next: string) {
    setSaving(true);
    setError(null);
    try {
      await api.saveSetup(status.data!.setup?.completed_steps ?? [], next || null);
      await client.invalidateQueries({ queryKey: ["onboarding"] });
      await client.invalidateQueries({ queryKey: ["profile"] });
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "That didn't go through. Please try again.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="rounded-xl border border-border bg-surface-elevated p-4">
      <Field label="First semester at Rutgers" error={error ?? undefined}
        hint="Shown on your profile. Your plan counts from the courses on your record.">
        {({ id, describedBy, invalid }) => (
          <Select id={id} aria-describedby={describedBy} aria-invalid={invalid} disabled={saving}
            value={current} onChange={(e) => save(e.target.value)}>
            <option value="">Not saying</option>
            {firstTermChoices(new Date()).map((t) => <option key={t} value={t}>{termLabel(t)}</option>)}
          </Select>
        )}
      </Field>
    </div>
  );
}
