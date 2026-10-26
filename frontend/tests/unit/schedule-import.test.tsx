/**
 * Phase 7.2.2: imported registrations on the week calendar. Only a
 * registration that EXACTLY matched a published SOC section carries meetings
 * and is drawn; anything else is listed, never drawn - the importer never
 * invents a meeting time. Fabricated data only.
 */
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { DroppedList, ImportedWeek, matchText, rowEffectText, WebRegExportSteps } from "@/features/schedule/import-review";
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


describe("Phase 7.4: what confirming a WebReg import does to the record", () => {
  it("shows the pre-selected section and every record effect before confirmation", () => {
    expect(rowEffectText(row({ resolved_index: "91234", history_effect: "add_in_progress", section_effect: "add" })))
      .toBe("Pre-selected: section 01, index 91234. Confirming adds it to your record as in progress and records this section as your current section.");
    expect(rowEffectText(row({ resolved_index: "91234", history_effect: "already_in_progress", section_effect: "already_confirmed" })))
      .toMatch(/already in progress on your record \(unchanged\) and this section is already your confirmed section/);
    expect(rowEffectText(row({ resolved_index: "91234", history_effect: "planned_to_in_progress", section_effect: "replace" })))
      .toMatch(/changes it from planned to in progress and replaces the section you confirmed before/);
    expect(rowEffectText(row({ state: "metadata_unavailable", match_status: "NO_MATCH" }))).toBeNull();
    expect(rowEffectText(row({ excluded: true, history_effect: "add_in_progress" }))).toBeNull();
  });

  it("names a completed-record conflict in words", () => {
    expect(matchText(row({ state: "conflict", match_status: "NEEDS_CONFIRMATION", issues: ["RECORD_SAYS_COMPLETED"] })))
      .toBe("Needs your review: your record shows this course COMPLETED in this term");
  });

  it("flags courses missing from a newer schedule and says nothing was removed", () => {
    const t = text(renderToStaticMarkup(<DroppedList courses={[
      { term_code: "20999", course_string: "01:999:202", section_number: "01", index_number: "95555", source: "webreg_import" }]} />));
    expect(t).toContain("Not on your latest WebReg schedule - dropped?");
    expect(t).toContain("01:999:202");
    expect(t).toContain("CoursePilot removes nothing");
    expect(renderToStaticMarkup(<DroppedList courses={[]} />)).toBe("");
  });

  it("gives the WebReg export steps with Chrome's own Save as PDF", () => {
    const t = text(renderToStaticMarkup(<WebRegExportSteps />));
    expect(t).toContain("View / Print Schedule");
    expect(t).toContain("Save as PDF");
    expect(t).toContain("not Microsoft Print to PDF");
    expect(t).toMatch(/never asks for your NetID/);
  });
});
