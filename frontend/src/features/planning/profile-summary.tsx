import { STATUS_ICON } from "@/components/academic/status";
import { DAY_NAME, formatMinute } from "@/lib/academic/schedule-time";
import { GOAL_KIND, PACE_LABEL, READINESS_CHECK, STRATEGY_LABEL } from "@/lib/academic/vocabulary";
import { cn } from "@/lib/utils";
import type { PlanningProfileInput, Readiness } from "@/types/personalization";

/**
 * "Here's how CoursePilot will plan for you" - the confirmed (or about to be
 * confirmed) Planning Profile in plain words. Preferences only: nothing here
 * describes the student's record.
 */

function minutes(hhmm: string): number {
  const [h, m] = hhmm.split(":").map(Number);
  return (h ?? 0) * 60 + (m ?? 0);
}

export function profileLines(p: PlanningProfileInput, programName?: string): [string, string][] {
  const lines: [string, string][] = [];
  if (programName) lines.push(["Program", programName]);
  lines.push(["Planning pace", PACE_LABEL[p.pace] ?? p.pace]);
  lines.push(["When several courses fit", STRATEGY_LABEL[p.strategy ?? "balanced"] ?? String(p.strategy)]);
  lines.push(["Target semester load", `${p.target_credits_per_term} credits`]);
  lines.push(["Maximum semester load", `${p.max_credits_per_term} credits, never more`]);
  lines.push(["Courses per semester", `At most ${p.max_courses_per_term}`]);
  lines.push(["Summer", p.include_summer ? "Can include summer courses" : "Not planned"]);
  lines.push(["Winter", p.include_winter ? "Can include winter courses" : "Not planned"]);
  const sched: string[] = [];
  if (p.schedule.earliest_start) sched.push(`No classes before ${formatMinute(minutes(p.schedule.earliest_start))}`);
  if (p.schedule.latest_end) sched.push(`No classes after ${formatMinute(minutes(p.schedule.latest_end))}`);
  if (p.schedule.avoid_days.length) sched.push(`No classes on ${p.schedule.avoid_days.map((d) => DAY_NAME[d]).join(", ")}`);
  lines.push(["Schedule", sched.length ? sched.join(" · ") : "No time limits"]);
  lines.push(["Additional goals", p.additional_goals.length
    ? p.additional_goals.map((g) => `Exploring ${g.label} (${GOAL_KIND[g.kind]?.toLowerCase() ?? g.kind})`).join("; ")
    : "None"]);
  return lines;
}

export function ProfileSummary({ profile, programName, className }: { profile: PlanningProfileInput; programName?: string; className?: string }) {
  return (
    <dl className={cn("divide-y divide-border rounded-lg border border-border bg-surface-elevated", className)}>
      {profileLines(profile, programName).map(([term, value]) => (
        <div key={term} className="grid gap-1 p-3 sm:grid-cols-[12rem_1fr] sm:gap-4">
          <dt className="text-xs font-medium uppercase tracking-wide text-fg-muted">{term}</dt>
          <dd className="text-sm text-fg [overflow-wrap:anywhere]">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

/** The server's readiness checks. "Needed" is not failure; warnings never block. */
export function ReadinessList({ readiness }: { readiness: Readiness }) {
  return (
    <ul className="space-y-2">
      {readiness.checks.map((c) => {
        const look = READINESS_CHECK[c.state];
        const Icon = STATUS_ICON[look.icon];
        return (
          <li key={c.code} className="flex items-start gap-2 text-sm">
            <Icon aria-hidden className={cn("mt-0.5 size-4 shrink-0",
              c.state === "done" ? "text-success" : c.state === "warning" ? "text-warning" : "text-fg-muted")} />
            <span className="sr-only">{look.label}: </span>
            <span className={c.state === "missing" ? "font-medium text-fg" : "text-fg-secondary"}>{c.message}</span>
          </li>
        );
      })}
    </ul>
  );
}
