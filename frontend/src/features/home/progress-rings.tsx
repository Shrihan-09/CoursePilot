"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { cn } from "@/lib/utils";
import type { RingValue } from "@/lib/home";

/**
 * Three concentric progress rings (UI polish B1): outer = degree credits,
 * middle = major requirements, inner = Core / gen-ed. Solid = done, faded =
 * in progress, grey = needs review or can't be determined (never drawn as
 * done). Each ring and legend row links to its Degree section.
 */

const RADII = { degree: 86, major: 64, core: 42 } as const;
const STROKE = 16;
const COLOR: Record<RingValue["key"], string> = { degree: "var(--ring-1)", major: "var(--ring-2)", core: "var(--ring-3)" };

export function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(true);
  useEffect(() => {
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReduced(mq.matches);
    update();
    mq.addEventListener("change", update);
    return () => mq.removeEventListener("change", update);
  }, []);
  return reduced;
}

function Arc({ r, from, length, color, opacity = 1, grown, animate, dashed }: {
  r: number; from: number; length: number; color: string; opacity?: number; grown: boolean; animate: boolean; dashed?: boolean;
}) {
  if (length <= 0) return null;
  const c = 2 * Math.PI * r;
  const shown = grown ? length : 0;
  return (
    <circle cx={100} cy={100} r={r} fill="none" stroke={color} strokeOpacity={opacity} strokeWidth={STROKE}
      strokeLinecap={length >= 0.999 ? "butt" : "round"}
      strokeDasharray={dashed ? "3 4" : `${shown * c} ${c}`}
      strokeDashoffset={dashed ? undefined : -from * c}
      transform="rotate(-90 100 100)"
      style={animate && !dashed ? { transition: "stroke-dasharray 900ms cubic-bezier(0.2, 0.8, 0.2, 1)" } : undefined}
      data-segment />
  );
}

export function ringLabel(v: RingValue): string {
  return `${v.label}: ${v.available ? v.detail : v.summary}`;
}

export function ProgressRings({ rings, hrefs, size = 200, className, anchors = false }: {
  rings: RingValue[];
  hrefs: Record<RingValue["key"], string>;
  size?: number;
  className?: string;
  /** Give each legend row an id (ring-degree, ring-major, ring-core) so Home can link to it. */
  anchors?: boolean;
}) {
  const reduced = usePrefersReducedMotion();
  const [grown, setGrown] = useState(false);
  useEffect(() => {
    if (reduced) { setGrown(true); return; }
    const id = requestAnimationFrame(() => setGrown(true));
    return () => cancelAnimationFrame(id);
  }, [reduced]);
  const shown = reduced || grown;

  return (
    <div className={cn("flex flex-col items-center gap-5 sm:flex-row sm:items-center", className)} data-testid="progress-rings"
      data-reduced-motion={reduced ? "true" : "false"}>
      <svg viewBox="0 0 200 200" width={size} height={size} role="group" aria-label="Progress rings" className="shrink-0">
        {rings.map((v) => {
          const r = RADII[v.key];
          const color = COLOR[v.key];
          return (
            <Link key={v.key} href={hrefs[v.key]} aria-label={ringLabel(v)} className="cp-ring-link">
              <title>{ringLabel(v)}</title>
              <circle cx={100} cy={100} r={r} fill="none" stroke="rgb(var(--surface-hover))" strokeWidth={STROKE} />
              {v.available ? (
                <>
                  <Arc r={r} from={0} length={v.done} color={`rgb(${color})`} grown={shown} animate={!reduced} />
                  <Arc r={r} from={v.done} length={v.inProgress} color={`rgb(${color})`} opacity={0.4} grown={shown} animate={!reduced} />
                  <Arc r={r} from={v.done + v.inProgress} length={v.review} color="rgb(var(--ring-review))" grown={shown} animate={!reduced} />
                </>
              ) : (
                <Arc r={r} from={0} length={1} color="rgb(var(--ring-review))" opacity={0.6} grown animate={false} dashed />
              )}
            </Link>
          );
        })}
      </svg>
      <ul className="w-full min-w-0 space-y-2 text-sm">
        {rings.map((v) => (
          <li key={v.key} id={anchors ? `ring-${v.key}` : undefined} className="scroll-mt-24">
            <Link href={hrefs[v.key]} className="group flex min-h-11 items-start gap-2.5 rounded-md p-1.5 hover:bg-surface-hover">
              <span aria-hidden className="mt-1 size-3 shrink-0 rounded-full" style={{ background: v.available ? `rgb(${COLOR[v.key]})` : "rgb(var(--ring-review))" }} />
              <span className="min-w-0">
                <span className="block font-medium text-fg">
                  {v.label} <span className="tabular font-semibold">{v.summary}</span>
                </span>
                <span className="block text-xs text-fg-secondary">{v.detail}</span>
              </span>
            </Link>
          </li>
        ))}
        <li className="flex flex-wrap gap-x-3 gap-y-1 pl-1.5 text-2xs text-fg-muted" aria-label="Ring key">
          <span className="inline-flex items-center gap-1"><span aria-hidden className="h-1.5 w-3 rounded-full" style={{ background: "rgb(var(--text-secondary))" }} />Done</span>
          <span className="inline-flex items-center gap-1"><span aria-hidden className="h-1.5 w-3 rounded-full" style={{ background: "rgb(var(--text-secondary) / .4)" }} />In progress</span>
          <span className="inline-flex items-center gap-1"><span aria-hidden className="h-1.5 w-3 rounded-full" style={{ background: "rgb(var(--ring-review))" }} />Needs review / unknown</span>
        </li>
      </ul>
    </div>
  );
}
