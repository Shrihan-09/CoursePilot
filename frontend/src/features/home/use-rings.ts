"use client";

import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import { dnNotMet, useDnProgress } from "@/features/degree/degree-navigator";
import { api } from "@/lib/api";
import { degreeRing, metricRing, type RingValue } from "@/lib/home";

/** The three rings from the saved plan's degree credits and modeled progress (no new math). */
export function useRings(): { rings: RingValue[] | null; pending: boolean; notMet: { code: string; name: string }[] } {
  const plan = useQuery({ queryKey: ["plan"], queryFn: api.currentPlan, staleTime: 0 });
  const progress = useQuery({ queryKey: ["modeled-progress"], queryFn: api.progress, staleTime: 0 });
  const dn = useDnProgress();
  const notMet = useMemo(() => dnNotMet(dn.data), [dn.data]);
  const rings = useMemo(() => {
    if (!progress.data) return null;
    const review = new Set(notMet.map((n) => n.code));
    const metric = (scope: "primary_program" | "school_core") => progress.data.metrics.find((m) => m.scope === scope);
    const flagged = (scope: "primary_program" | "school_core") => (metric(scope)?.requirement_codes ?? []).some((c) => review.has(c));
    return [
      degreeRing(plan.data?.plan?.result.degree_credits),
      metricRing("major", "Major requirements", metric("primary_program"), flagged("primary_program")),
      metricRing("core", "Core / gen-ed", metric("school_core"), flagged("school_core")),
    ];
  }, [plan.data, progress.data, notMet]);
  return { rings, pending: progress.isPending || plan.isPending, notMet };
}
