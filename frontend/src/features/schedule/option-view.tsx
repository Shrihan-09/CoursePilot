"use client";

import { Check, ClipboardCopy, Radio as LiveIcon, ShieldCheck } from "lucide-react";
import { useState } from "react";

import { StateBadge, STATUS_ICON } from "@/components/academic/status";
import { Button } from "@/components/ui/button";
import { formatDuration, formatMinute, meetingPlace, meetingText, summarize } from "@/lib/academic/schedule-time";
import {
  availabilityText,
  RESTRICTION_OUTCOME,
  SCHEDULE_REASON,
  SCHEDULE_SEVERITY,
} from "@/lib/academic/vocabulary";
import type { ScheduleIssue, ScheduleOption, ScheduleResult, SectionChoice } from "@/types/schedule";

/**
 * One schedule option, and how the options compare (Phase 7.2).
 *
 * Two facts are kept apart on purpose:
 *   STRUCTURAL VALIDITY - the engine proved these sections fit together
 *     (times, linked components, restrictions, your settings);
 *   LIVE AVAILABILITY  - whether each section was open when CoursePilot last
 *     looked. A valid schedule can contain a section that closes later.
 * Registration happens in WebReg, by the student. CoursePilot registers no one.
 */

const RANKING: Record<string, string> = {
  needs_confirmation: "fewer things to confirm",
  unknown_time_sections: "fewer sections without published times",
  closed_sections_if_current: "fewer closed sections (when availability is current)",
  unknown_availability_if_current: "fewer sections with unknown availability",
  preference_misses: "fewer misses of your soft preferences",
  class_days: "fewer days on campus",
  gap_minutes: "less time between classes",
  index_numbers: "then index number, as a fixed tie-break",
};

export function RankingNote({ objective }: { objective: string[] }) {
  if (!objective.length) return null;
  return (
    <p className="text-xs text-fg-muted">
      Options are listed in a fixed order, not by quality: {objective.map((k) => RANKING[k] ?? k).join(", ")}.
      &quot;Option 1&quot; is first in that order, not &quot;the best&quot; schedule.
    </p>
  );
}

export function Comparison({ options, selected, onSelect }: { options: ScheduleOption[]; selected: number; onSelect: (rank: number) => void }) {
  const rows: [string, (o: ScheduleOption) => string][] = [
    ["Days on campus", (o) => String(summarize(o).classDays)],
    ["Earliest start", (o) => { const s = summarize(o).earliestStart; return s === null ? "-" : formatMinute(s); }],
    ["Latest end", (o) => { const s = summarize(o).latestEnd; return s === null ? "-" : formatMinute(s); }],
    ["Time between classes", (o) => formatDuration(summarize(o).gapMinutes)],
    ["Weekly class time", (o) => formatDuration(summarize(o).weeklyMinutes)],
    ["Credits", (o) => o.total_credits ?? "Not all published"],
    ["Open now", (o) => (o.availability.freshness === "current" ? (o.availability.all_open_now ? "All open" : "Not all open") : "Not current")],
  ];
  return (
    <div className="overflow-x-auto rounded border border-border">
      <table className="w-full min-w-[28rem] border-collapse text-sm">
        <caption className="sr-only">Schedule options compared</caption>
        <thead>
          <tr className="bg-surface">
            <th scope="col" className="sticky left-0 bg-surface p-2 text-left text-xs font-medium text-fg-muted">Option</th>
            {options.map((o) => (
              <th key={o.rank} scope="col" className="p-1 text-left">
                <button
                  type="button"
                  onClick={() => onSelect(o.rank)}
                  aria-pressed={selected === o.rank}
                  className={`min-h-9 min-w-9 rounded px-2 text-sm font-semibold ${selected === o.rank ? "bg-primary text-primary-foreground" : "text-fg hover:bg-surface-hover"}`}
                >
                  {o.rank}
                </button>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map(([label, value]) => (
            <tr key={label} className="border-t border-border">
              <th scope="row" className="sticky left-0 bg-surface-elevated p-2 text-left text-xs font-medium text-fg-muted">{label}</th>
              {options.map((o) => (
                <td key={o.rank} className={`tabular p-2 ${selected === o.rank ? "bg-primary-subtle text-fg" : "text-fg-secondary"}`}>{value(o)}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function IssueList({ issues }: { issues: ScheduleIssue[] }) {
  if (!issues.length) return null;
  return (
    <ul className="space-y-2">
      {issues.map((issue, i) => {
        const sev = SCHEDULE_SEVERITY[issue.severity];
        const Icon = STATUS_ICON[sev.icon];
        return (
          <li key={`${issue.code}-${i}`} className="flex gap-3 rounded border border-border bg-surface-elevated p-3 text-sm">
            <Icon aria-hidden className="mt-0.5 size-4 shrink-0 text-fg-muted" />
            <div className="min-w-0 space-y-0.5">
              <p className="text-xs font-medium uppercase tracking-wide text-fg-muted">{sev.label}</p>
              <p className="text-fg [overflow-wrap:anywhere]">{issue.message}</p>
              {(issue.courses.length > 0 || issue.indexes.length > 0) && (
                <p className="code text-xs text-fg-muted">{[...issue.courses, ...issue.indexes.map((x) => `index ${x}`)].join(" · ")}</p>
              )}
            </div>
          </li>
        );
      })}
    </ul>
  );
}

function SectionDetails({ choice }: { choice: SectionChoice }) {
  const availability = availabilityText(choice.availability);
  const restriction = RESTRICTION_OUTCOME[choice.restriction.outcome];
  return (
    <li className="space-y-2 p-3 sm:p-4">
      <div className="flex flex-col gap-1 sm:flex-row sm:items-baseline sm:justify-between">
        <p className="min-w-0 text-sm">
          <span className="code font-semibold text-fg">{choice.course_string}{choice.supplement_code ? ` ${choice.supplement_code}` : ""}</span>{" "}
          <span className="text-fg-secondary [overflow-wrap:anywhere]">{choice.title ?? ""}</span>
        </p>
        <p className="tabular shrink-0 text-xs text-fg-muted">
          Section {choice.section_number} · Index <span className="code font-semibold text-fg">{choice.index_number}</span>
          {choice.credits !== null && <> · {choice.credits} cr</>}
        </p>
      </div>
      {choice.component === "required_companion" && (
        <p className="text-xs text-fg-secondary">
          Required with {choice.course}: Rutgers links this section to the one you take.
          {choice.component_evidence && <span className="text-fg-muted"> (Rutgers: &quot;{choice.component_evidence}&quot;)</span>}
        </p>
      )}
      <ul className="space-y-0.5 text-sm text-fg-secondary">
        {choice.meetings.map((m, i) => {
          const place = meetingPlace(m);
          return <li key={i}>{meetingText(m)}{place ? ` · ${place}` : ""}</li>;
        })}
      </ul>
      <p className="text-sm text-fg-secondary">
        {choice.instructors.length ? `Instructor: ${choice.instructors.join(", ")}` : "Instructor not listed by Rutgers"}
        {" · "}Campus {choice.campus_code}
      </p>
      <div className="flex flex-wrap gap-2">
        <StateBadge state={availability} />
        <StateBadge state={restriction} />
      </div>
      {(choice.restriction.open_to_text || choice.restriction.eligibility_text || choice.restriction.special_permission) && (
        <p className="text-xs text-fg-muted [overflow-wrap:anywhere]">
          {[choice.restriction.open_to_text && `Open to: ${choice.restriction.open_to_text}`,
            choice.restriction.eligibility_text,
            choice.restriction.special_permission && `Special permission: ${choice.restriction.special_permission}`]
            .filter(Boolean).join(" · ")}
        </p>
      )}
      {choice.notes && <p className="text-xs text-fg-muted [overflow-wrap:anywhere]">Rutgers note: {choice.notes}</p>}
    </li>
  );
}

export function RegistrationHandoff({ option }: { option: ScheduleOption }) {
  const [copied, setCopied] = useState(false);
  const indexes = option.choices.map((c) => c.index_number);
  return (
    <section aria-labelledby="register-heading" className="space-y-2 animate-cp-rise rounded-xl border border-border bg-surface-elevated p-4">
      <h3 id="register-heading" className="text-sm font-semibold text-fg">Register in WebReg yourself</h3>
      <p className="text-sm text-fg-secondary">
        CoursePilot doesn&apos;t register you, and nothing here adds or drops a course. Enter these index
        numbers in Rutgers WebReg when your registration window opens.
      </p>
      <p className="code tabular text-base font-semibold tracking-wide text-fg">{indexes.join("  ")}</p>
      <Button
        size="sm"
        variant="secondary"
        onClick={async () => {
          try {
            await navigator.clipboard.writeText(indexes.join(", "));
            setCopied(true);
          } catch {
            setCopied(false);
          }
        }}
      >
        {copied ? <Check aria-hidden className="size-4" /> : <ClipboardCopy aria-hidden className="size-4" />}
        {copied ? "Copied" : "Copy index numbers"}
      </Button>
    </section>
  );
}

export function ValidityAndAvailability({ option, result }: { option: ScheduleOption; result: ScheduleResult }) {
  const m = result.metadata;
  const current = m.availability_freshness === "current";
  return (
    <div className="grid gap-3 md:grid-cols-2">
      <section aria-labelledby="valid-heading" className="space-y-2 animate-cp-rise rounded-xl border border-border bg-surface-elevated p-4">
        <h3 id="valid-heading" className="flex items-center gap-2 text-sm font-semibold text-fg">
          <ShieldCheck aria-hidden className="size-4 text-success" /> Why this schedule is valid
        </h3>
        <ul className="space-y-1 text-sm text-fg-secondary">
          {option.reasons.map((r) => <li key={r}>· {SCHEDULE_REASON[r] ?? r}</li>)}
        </ul>
      </section>
      <section aria-labelledby="live-heading" className="space-y-2 animate-cp-rise rounded-xl border border-border bg-surface-elevated p-4">
        <h3 id="live-heading" className="flex items-center gap-2 text-sm font-semibold text-fg">
          <LiveIcon aria-hidden className="size-4 text-fg-muted" /> Open seats (live, separate from validity)
        </h3>
        <p className="text-sm text-fg-secondary">
          {current
            ? `Checked with Rutgers ${m.availability_observed_at ? new Date(m.availability_observed_at).toLocaleString() : "recently"}.`
            : `Not current${m.availability_observed_at ? ` - last checked ${new Date(m.availability_observed_at).toLocaleString()}` : ""}. Sections may have opened or closed since.`}
        </p>
        <p className="text-xs text-fg-muted">A valid schedule can include a section that is closed or closes later.</p>
      </section>
    </div>
  );
}

export function OptionDetails({ option, result }: { option: ScheduleOption; result: ScheduleResult }) {
  return (
    <div className="space-y-4">
      <ValidityAndAvailability option={option} result={result} />
      {option.issues.length > 0 && (
        <section aria-labelledby="option-issues" className="space-y-2">
          <h3 id="option-issues" className="text-sm font-semibold text-fg">Check before registering</h3>
          <IssueList issues={option.issues} />
        </section>
      )}
      <section aria-labelledby="sections-heading" className="space-y-2">
        <h3 id="sections-heading" className="text-sm font-semibold text-fg">Sections</h3>
        <ul className="divide-y divide-border animate-cp-rise rounded-xl border border-border bg-surface-elevated">
          {option.choices.map((c) => <SectionDetails key={c.index_number} choice={c} />)}
        </ul>
      </section>
      <RegistrationHandoff option={option} />
    </div>
  );
}
