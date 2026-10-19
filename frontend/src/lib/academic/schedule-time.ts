/**
 * Display arithmetic over a schedule the Schedule Engine already validated
 * (Phase 7.2). Nothing here checks conflicts, compatibility or eligibility -
 * those were decided by the engine before the option existed. These helpers
 * only lay out and summarise the meetings it returned.
 */

import type { DayCode, MeetingInterval, ScheduleOption, SectionChoice } from "@/types/schedule";

export const WEEK: DayCode[] = ["M", "T", "W", "H", "F", "S", "U"];

export const DAY_NAME: Record<DayCode, string> = {
  M: "Monday", T: "Tuesday", W: "Wednesday", H: "Thursday", F: "Friday", S: "Saturday", U: "Sunday",
};

export const DAY_SHORT: Record<DayCode, string> = {
  M: "Mon", T: "Tue", W: "Wed", H: "Thu", F: "Fri", S: "Sat", U: "Sun",
};

/** 570 -> "9:30 AM". */
export function formatMinute(minute: number): string {
  const h = Math.floor(minute / 60) % 24;
  const m = minute % 60;
  const suffix = h < 12 ? "AM" : "PM";
  const hour = h % 12 === 0 ? 12 : h % 12;
  return `${hour}:${String(m).padStart(2, "0")} ${suffix}`;
}

export function isTimed(m: MeetingInterval): m is MeetingInterval & { day: DayCode; start_minute: number; end_minute: number } {
  return m.kind === "timed" && m.day !== null && m.start_minute !== null && m.end_minute !== null;
}

export function meetingPlace(m: MeetingInterval): string | null {
  const room = [m.building, m.room].filter(Boolean).join(" ");
  const campus = m.campus_name ?? m.campus;
  if (room && campus) return `${room}, ${campus}`;
  return room || campus || null;
}

/** One meeting as words - the text equivalent of a calendar block. */
export function meetingText(m: MeetingInterval): string {
  if (isTimed(m)) {
    return `${DAY_NAME[m.day]} ${formatMinute(m.start_minute)} to ${formatMinute(m.end_minute)}`;
  }
  switch (m.kind) {
    case "asynchronous":
      return "Online, no scheduled meeting time";
    case "arranged":
      return "By arrangement (time not published)";
    case "tba":
      return "Time to be announced by Rutgers";
    default:
      return "Meeting time not usable as published";
  }
}

export interface Block {
  day: DayCode;
  start: number;
  end: number;
  choice: SectionChoice;
  meeting: MeetingInterval;
}

/** Timed meetings per day, sorted by start; everything else separately. */
export function layout(option: ScheduleOption): { blocks: Block[]; untimed: { choice: SectionChoice; meeting: MeetingInterval }[] } {
  const blocks: Block[] = [];
  const untimed: { choice: SectionChoice; meeting: MeetingInterval }[] = [];
  for (const choice of option.choices) {
    for (const meeting of choice.meetings) {
      if (isTimed(meeting)) {
        blocks.push({ day: meeting.day, start: meeting.start_minute, end: meeting.end_minute, choice, meeting });
      } else {
        untimed.push({ choice, meeting });
      }
    }
  }
  blocks.sort((a, b) => WEEK.indexOf(a.day) - WEEK.indexOf(b.day) || a.start - b.start || a.end - b.end);
  return { blocks, untimed };
}

/** Days the calendar should show: Mon-Fri always, weekend only when used. */
export function calendarDays(blocks: Block[]): DayCode[] {
  const used = new Set(blocks.map((b) => b.day));
  return WEEK.filter((d) => ["M", "T", "W", "H", "F"].includes(d) || used.has(d));
}

export interface OptionSummary {
  earliestStart: number | null;
  latestEnd: number | null;
  /** Sum of timed meeting lengths in a week, in minutes. */
  weeklyMinutes: number;
  untimedMeetings: number;
  /** From the engine's own score - not recomputed. */
  classDays: number;
  gapMinutes: number;
}

export function summarize(option: ScheduleOption): OptionSummary {
  const { blocks, untimed } = layout(option);
  return {
    earliestStart: blocks.length ? Math.min(...blocks.map((b) => b.start)) : null,
    latestEnd: blocks.length ? Math.max(...blocks.map((b) => b.end)) : null,
    weeklyMinutes: blocks.reduce((sum, b) => sum + (b.end - b.start), 0),
    untimedMeetings: untimed.length,
    classDays: option.score.class_days,
    gapMinutes: option.score.gap_minutes,
  };
}

export function formatDuration(minutes: number): string {
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  if (h === 0) return `${m} min`;
  return m === 0 ? `${h} h` : `${h} h ${m} min`;
}
