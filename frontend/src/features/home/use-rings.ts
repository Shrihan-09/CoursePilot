"use client";

import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import { dnNotMet, useDnProgress } from "@/features/degree/degree-navigator";
import { api } from "@/lib/api";
import { degreeFigures, degreeRing, metricRing, type DegreeFigures, type RingValue } from "@/lib/home";

/**
 * The three rings from the saved plan's degree credits (or, without a plan,
 * the audit + the program's encoded degree total) and modeled progress. The
 * profile comes from the onboarding response, which never carries the GPA.
 */
export function useRings(): { rings: RingValue[] | null; pending: boolean; notMet: { code: string; name: string }[]; figures: DegreeFigures | null } {
  const plan = useQuery({ queryKey: ["plan"], queryFn: api.currentPlan, staleTime: 0 });
  const progress = useQuery({ queryKey: ["modeled-progress"], queryFn: api.progress, staleTime: 0 });
  const audit = useQuery({ queryKey: ["audit"], queryFn: api.audit, staleTime: 0 });
  const onboarding = useQuery({ queryKey: ["onboarding"], queryFn: api.onboarding });
  const dn = useDnProgress();
  const notMet = useMemo(() => dnNotMet(dn.data), [dn.data]);
  const saved = plan.data?.plan ?? null;
  const figures = useMemo(
    () => degreeFigures(saved?.result.degree_credits, audit.data, onboarding.data?.profile?.degree_credit_total?.credits),
    [saved, audit.data, onboarding.data],
  );
  const rings = useMemo(() => {
    if (!progress.data) return null;
    const review = new Set(notMet.map((n) => n.code));
    const metric = (scope: "primary_program" | "school_core") => progress.data.metrics.find((m) => m.scope === scope);
    const flagged = (scope: "primary_program" | "school_core") => (metric(scope)?.requirement_codes ?? []).some((c) => review.has(c));
    return [
      degreeRing(figures, !!saved),
      metricRing("major", "Major requirements", metric("primary_program"), flagged("primary_program")),
      metricRing("core", "Core / gen-ed", metric("school_core"), flagged("school_core")),
    ];
  }, [progress.data, notMet, figures, saved]);
  return { rings, pending: progress.isPending || plan.isPending || audit.isPending, notMet, figures };
}
