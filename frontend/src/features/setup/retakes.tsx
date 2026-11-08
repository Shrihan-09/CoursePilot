"use client";

import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, RotateCcw } from "lucide-react";
import { useEffect } from "react";

import { Alert, Skeleton } from "@/components/ui/feedback";
import { api } from "@/lib/api";
import { RETAKE_OPTION_LABEL, retakeChoice, unlocksLine, withRetakePin } from "@/lib/setup";
import { cn } from "@/lib/utils";
import type { Retake, RetakesResponse } from "@/types/onboarding";
import type { CoursePin } from "@/types/personalization";

import { ChoiceCard } from "./choices";

/**
 * 10a follow-up: each retake the planner detects, and WHEN to take it. The
 * choice is saved as a pin of that course in that term (`retake: true`); a
 * winter or summer choice opens that term for this course only. Everything
 * shown - terms, offering evidence, impact - comes from the server.
 */
export function RetakeChooser({ pins, onChange }: { pins: CoursePin[]; onChange: (pins: CoursePin[]) => void }) {
  const q = useQuery({ queryKey: ["retakes"], queryFn: api.retakes });
  // No choice saved yet: the default becomes the pin, so the plan matches what is shown.
  useEffect(() => {
    if (!q.data) return;
    let next = pins;
    for (const r of q.data.retakes) {
      if (next.some((p) => p.course === r.course && p.retake)) continue;
      const term = r.options.find((o) => o.key === r.default && o.evidence !== "none")?.term_code;
      if (term) next = withRetakePin(next, r.course, term);
    }
    if (next !== pins) onChange(next);
  }, [q.data]); // eslint-disable-line react-hooks/exhaustive-deps

  if (q.isPending) return <Skeleton className="h-32" />;
  if (q.isError) return <Alert tone="warning">Retakes couldn&apos;t be checked right now. Your plan still adds any it needs.</Alert>;
  return <RetakeList data={q.data} pins={pins} onChange={onChange} />;
}

export function RetakeList({ data, pins, onChange }: { data: RetakesResponse; pins: CoursePin[]; onChange: (pins: CoursePin[]) => void }) {
  if (data.retakes.length === 0) {
    return (
      <Alert tone="info" title="No retakes needed">
        Nothing on your record needs to be taken again. If that changes, CoursePilot adds the retake to your plan.
      </Alert>
    );
  }
  const seasonal = data.retakes.some((r) => r.options.some((o) => o.key === "winter" || o.key === "summer"));
  return (
    <div className="space-y-5">
      {data.retakes.map((r) => <RetakeCard key={r.course} retake={r} pins={pins} onChange={onChange} />)}
      {seasonal && <p className="text-xs text-fg-muted">{data.cost_note}</p>}
    </div>
  );
}

export function RetakeCard({ retake, pins, onChange }: { retake: Retake; pins: CoursePin[]; onChange: (pins: CoursePin[]) => void }) {
  const chosen = retakeChoice(retake, pins);
  const line = unlocksLine(retake.unlocks);
  return (
    <fieldset className="space-y-3 rounded-lg border border-border bg-surface-elevated p-4" data-retake={retake.course}>
      <legend className="sr-only">When do you want to retake {retake.course}?</legend>
      <div className="flex items-start gap-3">
        <RotateCcw aria-hidden className="mt-0.5 size-5 shrink-0 text-primary" />
        <div className="min-w-0 space-y-0.5">
          <p className="text-sm font-semibold text-fg [overflow-wrap:anywhere]">
            {retake.title} (<span className="code">{retake.course}</span>): retake needed.
          </p>
          <p className="text-sm text-fg-secondary">Why: {retake.reason}.</p>
          {line && <p className="code text-xs text-fg-muted [overflow-wrap:anywhere]">{line}</p>}
        </div>
      </div>
      <p aria-hidden className="text-sm font-semibold text-fg">When do you want to retake it?</p>
      <div className="grid gap-2 sm:grid-cols-2">
        {retake.options.map((o) => {
          const asapTerm = retake.options.find((x) => x.key === "asap")?.term_code;
          // Next semester IS the earliest offering: one pin can't tell them apart, so ASAP carries it.
          const sameAsAsap = o.key === "next_semester" && o.term_code !== null && o.term_code === asapTerm;
          const off = o.evidence === "none" || !o.term_code || sameAsAsap;
          const selected = chosen === o.key && !off;
          // The chosen option shows its whole impact; the others show only a delay warning.
          const impacts = selected ? o.impacts : o.impacts.filter((i) => i.delayed);
          return (
            <ChoiceCard key={o.key} name={`retake-${retake.course}`} checked={selected} disabled={off}
              onSelect={() => o.term_code && onChange(withRetakePin(pins, retake.course, o.term_code))}
              title={RETAKE_OPTION_LABEL[o.key]}
              detail={o.term_code ? o.label : undefined} body={sameAsAsap ? "Same semester as ASAP." : o.evidence_label}>
              {!off && impacts.length > 0 && (
                <ul className="space-y-1 pt-1">
                  {impacts.map((i) => (
                    <li key={i.course} className={cn("flex items-start gap-1.5 text-xs", i.delayed ? "text-warning" : "text-success")}>
                      {i.delayed ? <AlertTriangle aria-hidden className="mt-0.5 size-3.5 shrink-0" />
                        : <CheckCircle2 aria-hidden className="mt-0.5 size-3.5 shrink-0" />}
                      <span className="text-fg-secondary">{i.message}.</span>
                    </li>
                  ))}
                </ul>
              )}
            </ChoiceCard>
          );
        })}
      </div>
    </fieldset>
  );
}
