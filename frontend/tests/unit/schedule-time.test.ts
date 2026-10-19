import { describe, expect, it } from "vitest";

import { calendarDays, formatDuration, formatMinute, layout, meetingText, summarize } from "@/lib/academic/schedule-time";
import type { MeetingInterval, ScheduleOption, SectionChoice } from "@/types/schedule";

const meeting = (over: Partial<MeetingInterval>): MeetingInterval => ({
  kind: "timed", day: "M", start_minute: 600, end_minute: 680, start_date: null, end_date: null,
  mode_code: "02", mode_desc: "LEC", campus: "CAC", campus_name: "College Avenue", building: "HCK", room: "110", ...over,
});

const choice = (index: string, meetings: MeetingInterval[]): SectionChoice => ({
  course: "01:198:111", component: "primary", course_string: "01:198:111", supplement_code: "", title: "Intro",
  section_number: "01", index_number: index, campus_code: "NB", meetings, instructors: [],
  availability: { state: "open", freshness: "stale", observed_at: null, source: null, source_hash: null, since: null, reasons: [] },
  restriction: { outcome: "unrestricted", entries: [], matched: null, open_to_text: null, eligibility_text: null, special_permission: null, reasons: [] },
  cross_listed_indexes: [], time_verified: true, notes: null, equivalent_sections: [], credits: "4",
  meeting_components: [], component_evidence: null, meeting_text_in_notes: null,
});

const option = (choices: SectionChoice[]): ScheduleOption => ({
  rank: 1, choices, total_credits: "4",
  score: { needs_confirmation: 0, unknown_time_sections: 0, preference_misses: 0, class_days: 2, gap_minutes: 35, closed_sections_if_current: 0, unknown_availability_if_current: 0 },
  reasons: [], issues: [], days: ["M", "W"],
  availability: { state: "open", freshness: "stale", as_of: null, all_open_now: false, courses: [] },
});

describe("schedule display arithmetic", () => {
  it("formats times", () => {
    expect(formatMinute(0)).toBe("12:00 AM");
    expect(formatMinute(570)).toBe("9:30 AM");
    expect(formatMinute(720)).toBe("12:00 PM");
    expect(formatMinute(1310)).toBe("9:50 PM");
    expect(formatDuration(95)).toBe("1 h 35 min");
    expect(formatDuration(40)).toBe("40 min");
  });

  it("never draws an untimed meeting on the calendar", () => {
    const o = option([choice("12345", [
      meeting({}), meeting({ day: "W", start_minute: 1200, end_minute: 1290 }),
      meeting({ kind: "tba", day: null, start_minute: null, end_minute: null }),
      meeting({ kind: "asynchronous", day: null, start_minute: null, end_minute: null }),
    ])]);
    const { blocks, untimed } = layout(o);
    expect(blocks.map((b) => [b.day, b.start])).toEqual([["M", 600], ["W", 1200]]);
    expect(untimed.map((u) => u.meeting.kind)).toEqual(["tba", "asynchronous"]);
    expect(meetingText(untimed[0]!.meeting)).toMatch(/to be announced/);
  });

  it("summarises from the meetings and takes days/gaps from the engine's score", () => {
    const s = summarize(option([choice("1", [meeting({}), meeting({ day: "W", start_minute: 480, end_minute: 560 })])]));
    expect(s.earliestStart).toBe(480);
    expect(s.latestEnd).toBe(680);
    expect(s.weeklyMinutes).toBe(160);
    expect(s.classDays).toBe(2);
    expect(s.gapMinutes).toBe(35);
  });

  it("shows weekends only when a meeting falls on one", () => {
    const weekday = layout(option([choice("1", [meeting({})])])).blocks;
    expect(calendarDays(weekday)).toEqual(["M", "T", "W", "H", "F"]);
    const sat = layout(option([choice("1", [meeting({ day: "S" })])])).blocks;
    expect(calendarDays(sat)).toEqual(["M", "T", "W", "H", "F", "S"]);
  });
});
