"use client";

import { useQuery } from "@tanstack/react-query";
import { BadgeCheck, Clock3 } from "lucide-react";
import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Alert, ErrorState, Skeleton, StatusBadge } from "@/components/ui/feedback";
import { SearchInput, Select } from "@/components/ui/field";
import { ApiError, api } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { Program } from "@/types/onboarding";

/**
 * Choose program + catalog year. Every evaluable program is offered; one that
 * is still pending human review is selectable (it may be the student's real
 * major) and SAYS so - the API attaches the same warnings to the profile.
 */
export function ProgramStep({ onCreated }: { onCreated: () => void }) {
  const programs = useQuery({ queryKey: ["programs"], queryFn: api.programs });
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<{ key: string; year: string } | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  const list = useMemo(() => {
    const all = (programs.data?.programs ?? []).filter((p) =>
      p.versions.some((v) => v.support_status !== "unavailable"));
    const q = query.trim().toLowerCase();
    const filtered = q
      ? all.filter((p) =>
          `${p.name} ${p.program_code} ${p.degree_type} ${p.school_name} ${p.variant}`
            .toLowerCase().includes(q))
      : all;
    return filtered.sort((a, b) => a.name.localeCompare(b.name));
  }, [programs.data, query]);

  const chosen = list.find((p) => p.program_key === selected?.key)
    ?? programs.data?.programs.find((p) => p.program_key === selected?.key);

  async function submit() {
    if (!selected) return;
    setSubmitting(true);
    setError(null);
    try {
      await api.createProfile(selected.key, selected.year);
      onCreated();
    } catch (e) {
      setError(e instanceof ApiError ? e : null);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section aria-labelledby="program-title" className="space-y-5">
      <div className="space-y-1">
        <h2 id="program-title" className="text-xl font-semibold tracking-tight text-fg">
          What are you studying?
        </h2>
        <p className="text-sm text-fg-secondary">
          Choose your program and the catalog year whose rules apply to you. You can change this
          later in Settings.
        </p>
      </div>

      <SearchInput
        placeholder="Search programs"
        aria-label="Search programs"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />

      {programs.isPending && (
        <div className="space-y-2" aria-label="Loading programs">
          {[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-16" />)}
        </div>
      )}
      {programs.isError && (
        <ErrorState onRetry={() => programs.refetch()}>Programs couldn't be loaded.</ErrorState>
      )}
      {programs.isSuccess && list.length === 0 && (
        <p className="py-6 text-center text-sm text-fg-muted">No program matches “{query}”.</p>
      )}

      <div className="space-y-2" role="radiogroup" aria-label="Programs">
        {list.map((p) => (
          <ProgramOption
            key={p.program_key}
            program={p}
            selected={selected?.key === p.program_key}
            onSelect={(year) => setSelected({ key: p.program_key, year })}
          />
        ))}
      </div>

      {chosen && selected && (
        <div className="sticky bottom-3 z-10 space-y-3 rounded-lg border border-border bg-surface-elevated p-4 shadow-elevated sm:bottom-4">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium text-fg">{chosen.name}</p>
              <p className="text-xs text-fg-muted">
                {chosen.school_name} · {chosen.degree_type}
                {chosen.variant ? ` · ${chosen.variant}` : ""}
              </p>
            </div>
            <label className="space-y-1 text-xs text-fg-muted sm:w-44">
              Catalog year
              <Select
                value={selected.year}
                onChange={(e) => setSelected({ ...selected, year: e.target.value })}
              >
                {chosen.versions
                  .filter((v) => v.support_status !== "unavailable")
                  .map((v) => (
                    <option key={v.catalog_year} value={v.catalog_year}>
                      {v.catalog_year}
                    </option>
                  ))}
              </Select>
            </label>
            <Button variant="primary" onClick={submit} loading={submitting} className="sm:w-auto">
              Continue
            </Button>
          </div>
          {error && (
            <Alert tone="danger" title={error.message}>
              {error.issues.map((i) => i.message).join(" ")}
            </Alert>
          )}
        </div>
      )}
    </section>
  );
}

function ProgramOption({
  program,
  selected,
  onSelect,
}: {
  program: Program;
  selected: boolean;
  onSelect: (year: string) => void;
}) {
  const versions = program.versions.filter((v) => v.support_status !== "unavailable");
  const latest = versions[0];
  if (!latest) return null;
  const supported = versions.every((v) => v.support_status === "supported");
  return (
    <div>
      <button
        type="button"
        role="radio"
        aria-checked={selected}
        onClick={() => onSelect(latest.catalog_year)}
        className={cn(
          "flex w-full items-start gap-3 rounded-lg border p-3 text-left transition-colors sm:p-4",
          selected
            ? "border-primary bg-primary-subtle"
            : "border-border bg-surface-elevated hover:border-border-strong hover:bg-surface-hover",
        )}
      >
        <span
          aria-hidden
          className={cn(
            "mt-1 flex size-4 shrink-0 items-center justify-center rounded-full border",
            selected ? "border-primary" : "border-border-strong",
          )}
        >
          {selected && <span className="size-2 rounded-full bg-primary" />}
        </span>
        <span className="min-w-0 flex-1 space-y-1">
          <span className="block text-sm font-medium text-fg [overflow-wrap:anywhere]">
            {program.name}
            {program.variant && <span className="text-fg-secondary"> - {program.variant}</span>}
          </span>
          <span className="block text-xs text-fg-muted">
            {program.school_name} · {program.degree_type} ·{" "}
            {versions.map((v) => v.catalog_year).join(", ")}
          </span>
        </span>
        {supported ? (
          <StatusBadge tone="success" icon={BadgeCheck} label="Reviewed" className="shrink-0" />
        ) : (
          <StatusBadge tone="warning" icon={Clock3} label="Pending review" className="shrink-0" />
        )}
      </button>
    </div>
  );
}
