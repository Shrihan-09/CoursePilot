/**
 * Phase 7.2.2: imported registrations on the week calendar. Only a
 * registration that EXACTLY matched a published SOC section carries meetings
 * and is drawn; anything else is listed, never drawn - the importer never
 * invents a meeting time. Fabricated data only.
 */
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ImportedWeek, matchText } from "@/features/schedule/import-review";
import type { Registration, ScheduleImportRow } from "@/types/schedule-import";

const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ");

const reg = (over: Partial<Registration>): Registration => ({
  id: "r", term_code: "20999", course_string: "01:999:101", section_number: "01", index_number: "91234",
  source: "student_schedule_pdf", confirmed_at: "2099-09-01T00:00:00Z", metadata_state: "available",
  supplement_code: "", instructors: [], modality: { classification: "in_person" },
  meetings: [],
  normalized_meetings: [{ kind: "timed", day: "M", start_minute: 600, end_minute: 680, start_date: null, end_date: null,
    mode_code: null, mode_desc: "LEC", campus: "BUS", campus_name: null, building: "HLL", room: "114" }],
  ...over,
});

describe("imported week", () => {
  it("draws exact-match registrations only and says what it left out", () => {
    const t = text(renderToStaticMarkup(<ImportedWeek registrations={[
      reg({}),
      reg({ id: "u", course_string: "01:999:202", metadata_state: "unavailable", normalized_meetings: [] }),
    ]} />));
    expect(t).toContain("10:00 AM to 11:20 AM");
    expect(t).toContain("01:999:101");
    expect(t).not.toContain("01:999:202");
    expect(t).toMatch(/1 registration without public meeting data is listed below, not drawn/);
  });

  it("draws nothing when no registration has public meeting data", () => {
    expect(renderToStaticMarkup(<ImportedWeek registrations={[reg({ metadata_state: "unavailable", normalized_meetings: [] })]} />)).toBe("");
  });
});

const row = (over: Partial<ScheduleImportRow>): ScheduleImportRow => ({
  id: "x", term_code: "20999", course_string: "01:999:101", section_number: "01", index_number: "91234",
  state: "matched", issues: [], excluded: false, replace_existing: false, match_status: "EXACT_MATCH", ...over,
});

describe("import review match labels", () => {
  it("names each of the four match states for a student, never the raw code", () => {
    expect(matchText(row({}))).toBe("Exact match with Rutgers section data");
    expect(matchText(row({ match_status: "NO_MATCH", issues: ["SOC_METADATA_UNAVAILABLE"] })))
      .toBe("No matching Rutgers section data");
    expect(matchText(row({ match_status: "MULTIPLE_MATCHES", issues: ["MULTIPLE_SOC_MATCHES"] })))
      .toMatch(/^Several Rutgers sections match - enter the exact index$/);
    expect(matchText(row({ match_status: "NEEDS_CONFIRMATION", issues: ["IDENTIFIERS_DISAGREE"] })))
      .toBe("Needs your review: the index and the course/section point to different sections");
    for (const status of ["EXACT_MATCH", "MULTIPLE_MATCHES", "NO_MATCH", "NEEDS_CONFIRMATION"] as const) {
      expect(matchText(row({ match_status: status }))).not.toMatch(/[A-Z]{3,}_[A-Z]/);
    }
  });

  it("an excluded row says so instead of a match state", () => {
    expect(matchText(row({ excluded: true, match_status: "NEEDS_CONFIRMATION" }))).toBe("Excluded by you");
  });
});
