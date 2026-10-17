"use client";

import { Check, Circle, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";

import { Mark } from "@/components/brand/mark";
import { cn } from "@/lib/utils";

/**
 * "CoursePilot is building your plan" (Phase 7.1 motion language).
 *
 * For SUBSTANTIAL generation only - a semester or multi-semester plan, a
 * schedule, a significant replan. Never for ordinary loading (skeletons) and
 * never to delay startup.
 *
 * The cap is the anchor. A simplified plan sheet begins BEHIND it, emerges, and
 * travels along the logo's own arrow vector - measured from the source artwork
 * (tail at lower-left, tip at upper-right) - as if CoursePilot made it and is
 * sending it into the interface. On completion the sheet resolves FORWARD into
 * the result (`onResolved`), never into a spinner.
 *
 * Honest about what is working: stages are passed in by the caller and should
 * mirror REAL backend stages (the deterministic engines' steps); the default
 * caption never claims an AI is deciding anything.
 *
 * prefers-reduced-motion (or `reducedMotion`): no flight - the static mark and
 * the same stage list carry the same information.
 */

export type StageState = "pending" | "active" | "done";
export interface GenerationStage {
  key: string;
  label: string;
  state: StageState;
}

export function GenerationAnimation({
  status,
  stages,
  title = "CoursePilot is building your plan",
  caption = "Computed by CoursePilot's deterministic planning engine from your record and Rutgers data.",
  onResolved,
  reducedMotion,
  className,
}: {
  status: "generating" | "complete";
  stages: GenerationStage[];
  title?: string;
  caption?: string;
  onResolved?: () => void;
  reducedMotion?: boolean;
  className?: string;
}) {
  const [systemReduced, setSystemReduced] = useState(false);
  useEffect(() => {
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    setSystemReduced(media.matches);
    const update = () => setSystemReduced(media.matches);
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  const reduced = reducedMotion ?? systemReduced;

  useEffect(() => {
    if (status !== "complete" || !onResolved) return;
    const id = window.setTimeout(onResolved, reduced ? 0 : 650);
    return () => window.clearTimeout(id);
  }, [status, onResolved, reduced]);

  const done = stages.filter((s) => s.state === "done").length;

  return (
    <div
      className={cn("flex flex-col items-center gap-6 text-center", className)}
      role="status"
      aria-live="polite"
      aria-busy={status === "generating"}
    >
      <div
        className={cn("cp-gen", reduced && "cp-gen--static", status === "complete" && "cp-gen--resolve")}
        aria-hidden
      >
        {!reduced && (
          <>
            <svg className="cp-gen__trail" viewBox="0 0 100 100" preserveAspectRatio="none">
              {/* The arrow's sweep, lower-left -> upper-right (source artwork). */}
              <path d="M 22 86 C 38 74, 52 62, 84 24" />
            </svg>
            <div className="cp-gen__sheet">
              <PlanSheet />
            </div>
          </>
        )}
        <div className="cp-gen__mark">
          <Mark size={512} decorative priority className="h-full w-full" />
        </div>
      </div>

      <div className="max-w-sm space-y-1">
        <p className="text-base font-semibold text-fg">
          {status === "complete" ? "Ready" : title}
        </p>
        <p className="text-sm text-fg-muted">{caption}</p>
      </div>

      {stages.length > 0 && (
        <ol className="w-full max-w-xs space-y-1.5 text-left" aria-label="Progress">
          {stages.map((stage) => (
            <li key={stage.key} className="flex items-center gap-2 text-sm">
              {stage.state === "done" && <Check aria-hidden className="size-4 text-success" />}
              {stage.state === "active" &&
                (reduced ? (
                  <Circle aria-hidden className="size-4 fill-primary text-primary" />
                ) : (
                  <Loader2 aria-hidden className="size-4 animate-spin text-primary" />
                ))}
              {stage.state === "pending" && <Circle aria-hidden className="size-4 text-fg-muted" />}
              <span className={cn(stage.state === "pending" ? "text-fg-muted" : "text-fg")}>
                {stage.label}
              </span>
              <span className="sr-only">
                {stage.state === "done" ? "done" : stage.state === "active" ? "in progress" : "waiting"}
              </span>
            </li>
          ))}
        </ol>
      )}
      <span className="sr-only">
        {done} of {stages.length} steps complete
      </span>
    </div>
  );
}

/** A deliberately simple plan: three term columns of course blocks. */
function PlanSheet() {
  return (
    <svg viewBox="0 0 120 84" className="h-full w-full">
      <rect x="1" y="1" width="118" height="82" rx="8" className="fill-[rgb(var(--surface-elevated))] stroke-[rgb(var(--border-strong))]" strokeWidth="1.5" />
      <rect x="10" y="10" width="44" height="6" rx="3" className="fill-[rgb(var(--text-primary))]" opacity="0.8" />
      {[0, 1, 2].map((col) => (
        <g key={col} transform={`translate(${10 + col * 35} 24)`}>
          {[0, 1, 2].map((row) => (
            <rect
              key={row}
              y={row * 17}
              width="30"
              height="12"
              rx="3"
              className={row === col ? "fill-[rgb(var(--primary))]" : "fill-[rgb(var(--surface-hover))]"}
            />
          ))}
        </g>
      ))}
    </svg>
  );
}
