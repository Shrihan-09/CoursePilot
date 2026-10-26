"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { ApiError, api } from "@/lib/api";

/**
 * Phase 7.6: "I haven't declared my major yet." The program on the profile is
 * then the major the student intends to declare; plans say "planning toward
 * X (not yet declared)" and show what declaring takes, cited.
 */
export function DeclarationEditor() {
  const client = useQueryClient();
  const profile = useQuery({ queryKey: ["profile"], queryFn: api.profile });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!profile.data) return null;
  const undeclared = profile.data.declaration_status === "undeclared";
  const program = profile.data.program.program_name;

  async function toggle(next: boolean) {
    setSaving(true);
    setError(null);
    try {
      await api.setDeclaration(next ? "undeclared" : "declared");
      await client.invalidateQueries({ queryKey: ["profile"] });
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "That didn't go through. Please try again.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-2 rounded-xl border border-border bg-surface-elevated p-4">
      <h2 className="text-sm font-semibold text-fg">Declared major</h2>
      <label className="flex min-h-11 items-center gap-2 text-sm text-fg">
        <input type="checkbox" checked={undeclared} disabled={saving}
               onChange={(e) => toggle(e.target.checked)} />
        I haven&apos;t declared {program} yet - plan toward it as my intended major
      </label>
      <p className="text-xs text-fg-secondary">
        Your plan will say it is planning toward a major you have not declared, list the official
        requirements to declare it, and treat sections open only to declared majors as closed to you.
      </p>
      {error && <p className="text-xs text-danger">{error}</p>}
    </div>
  );
}
