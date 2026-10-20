"use client";

import { useState } from "react";

import { Tabs } from "@/components/ui/overlay";
import {
  calendarDays,
  DAY_NAME,
  DAY_SHORT,
  formatMinute,
  layout,
  meetingPlace,
  meetingText,
  type Block,
} from "@/lib/academic/schedule-time";
import { courseTone } from "@/lib/tone";
import { cn } from "@/lib/utils";
import type { DayCode, ScheduleOption, SectionChoice } from "@/types/schedule";

/**
 * The week of ONE schedule option, as the Schedule Engine returned it.
 *
 * Desktop: a time grid. Each day is a labelled list whose items are the
 * meetings in time order, so the grid is also the text representation - a
 * screen reader reads "Monday, list, 01:198:211 Lecture, 10:20 AM to 11:40 AM,
 * Hill 114". Phones: day tabs and an agenda; a desktop grid shrunk to 320px
 * is unreadable. Meetings without a published time are listed separately -
 * they are never drawn as if they had one.
 */

const PX_PER_MIN = 0.9;

function label(b: Block): string {
  const component = b.choice.supplement_code ? ` ${b.choice.supplement_code}` : "";
  const kind = b.meeting.mode_desc ? ` ${b.meeting.mode_desc.toLowerCase()}` : "";
  return `${b.choice.course_string}${component}${kind}`;
}

function DayList({ day, blocks, top, positioned }: { day: DayCode; blocks: Block[]; top: number; positioned: boolean }) {
  return (
    <ul aria-label={DAY_NAME[day]} className={cn(positioned ? "relative" : "space-y-2")}>
      {blocks.length === 0 && !positioned && <li className="text-sm text-fg-muted">No classes.</li>}
      {blocks.map((b, i) => {
        const place = meetingPlace(b.meeting);
        return (
          <li
            key={`${b.choice.index_number}-${b.start}-${i}`}
            className={cn(
              "cp-tone cp-lift animate-cp-rise overflow-hidden rounded-md border-l-[3px] px-2 py-1 text-xs",
              courseTone(b.choice.course_string),
              positioned && "absolute inset-x-1",
            )}
            style={{
              animationDelay: `${Math.min(i, 6) * 40}ms`,
              ...(positioned ? { top: (b.start - top) * PX_PER_MIN, height: Math.max(22, (b.end - b.start) * PX_PER_MIN) } : {}),
            }}
          >
            <span className="code block truncate font-semibold text-fg">{label(b)}</span>
            <span className="tabular block truncate text-fg-secondary">
              {formatMinute(b.start)} to {formatMinute(b.end)}
            </span>
            {place && <span className="block truncate text-fg-secondary">{place}</span>}
          </li>
        );
      })}
    </ul>
  );
}

export function WeekCalendar({ option }: { option: ScheduleOption }) {
  const { blocks, untimed } = layout(option);
  const days = calendarDays(blocks);
  const byDay = (d: DayCode) => blocks.filter((b) => b.day === d);
  const firstDay = days.find((d) => byDay(d).length > 0) ?? days[0] ?? "M";
  const [day, setDay] = useState<DayCode>(firstDay);

  const start = blocks.length ? Math.floor(Math.min(...blocks.map((b) => b.start)) / 60) * 60 : 8 * 60;
  const end = blocks.length ? Math.ceil(Math.max(...blocks.map((b) => b.end)) / 60) * 60 : 17 * 60;
  const hours: number[] = [];
  for (let m = start; m <= end; m += 60) hours.push(m);
  const height = (end - start) * PX_PER_MIN;

  return (
    <div className="space-y-3">
      {/* Phones: day tabs + agenda */}
      <div className="md:hidden">
        <Tabs
          label="Day"
          value={day}
          onChange={setDay}
          items={days.map((d) => ({ value: d, label: DAY_SHORT[d], count: byDay(d).length }))}
        />
        <div role="tabpanel" aria-label={DAY_NAME[day]} className="pt-3">
          <DayList day={day} blocks={byDay(day)} top={start} positioned={false} />
        </div>
      </div>

      {/* Tablet and up: the week grid */}
      <div className="hidden md:block">
        <div className="grid gap-px overflow-hidden rounded-xl border border-border bg-border/60" style={{ gridTemplateColumns: `3.5rem repeat(${days.length}, minmax(0, 1fr))` }}>
          <div className="bg-surface-elevated" aria-hidden />
          {days.map((d) => (
            <div key={d} className="bg-surface-elevated px-2 py-2.5 text-center text-2xs font-semibold uppercase tracking-[0.08em] text-fg-secondary" aria-hidden>
              {DAY_SHORT[d]}
            </div>
          ))}
          <div className="relative bg-surface-elevated" style={{ height }} aria-hidden>
            {hours.map((m) => (
              <span key={m} className="tabular absolute right-1 -translate-y-1/2 text-2xs text-fg-muted" style={{ top: (m - start) * PX_PER_MIN }}>
                {formatMinute(m).replace(":00", "")}
              </span>
            ))}
          </div>
          {days.map((d) => (
            <div key={d} className="relative bg-surface-elevated" style={{ height }}>
              {hours.map((m) => (
                <div key={m} aria-hidden className="absolute inset-x-0 border-t border-border/50" style={{ top: (m - start) * PX_PER_MIN }} />
              ))}
              <DayList day={d} blocks={byDay(d)} top={start} positioned />
            </div>
          ))}
        </div>
      </div>

      {untimed.length > 0 && (
        <div className="space-y-1">
          <p className="text-xs font-medium uppercase tracking-wide text-fg-muted">Not on the calendar</p>
          <ul className="space-y-1 text-sm">
            {untimed.map(({ choice, meeting }, i) => (
              <li key={`${choice.index_number}-${i}`} className="text-fg-secondary">
                <span className="code text-fg">{choice.course_string}</span> - {meetingText(meeting)}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

/** Confirmed sections as a single option the calendar can draw (descriptive, never generated). */
export function asOption(choices: SectionChoice[]): ScheduleOption {
  return {
    rank: 1, choices, total_credits: null, reasons: [], issues: [], days: [],
    score: { needs_confirmation: 0, unknown_time_sections: 0, preference_misses: 0, class_days: 0,
             gap_minutes: 0, closed_sections_if_current: 0, unknown_availability_if_current: 0 },
    availability: { state: "unknown", freshness: "unobserved", as_of: null, all_open_now: false, courses: [] },
  };
}
