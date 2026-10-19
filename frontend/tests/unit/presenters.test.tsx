/**
 * Phase 7.2 presenters, rendered to static markup - the words a student
 * reads. Each test pins a trust-model rule from the brief:
 *   - UNKNOWN reads as "can't determine", never as failure;
 *   - pending-review programs always carry the warning;
 *   - a what-if is labelled hypothetical;
 *   - a stale plan says so and why;
 *   - blocked courses show the ORIGINAL Rutgers condition;
 *   - CoursePilot never claims to register anyone;
 *   - untimed meetings are never drawn as timed.
 */
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { PendingReviewNotice } from "@/features/degree/degree-view";
import { leafTally, RequirementNode } from "@/features/degree/requirement-tree";
import { ScenarioBanner } from "@/features/degree/what-if";
import { Blockers, StaleNotice } from "@/features/plan/plan-result";
import { RankingNote, RegistrationHandoff, ValidityAndAvailability } from "@/features/schedule/option-view";
import { WeekCalendar } from "@/features/schedule/week-calendar";
import type { RequirementResult } from "@/types/degree";
import type { SavedPlan } from "@/types/plan";
import type { ScheduleOption, ScheduleResult } from "@/types/schedule";

const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&quot;/g, '"').replace(/\s+/g, " ");

const req = (over: Partial<RequirementResult>): RequirementResult => ({
  requirement_code: "R1", requirement_name: "Mathematics placement", requirement_type: "course", status: "satisfied",
  needed_count: null, satisfied_count: 0, needed_credits: null, satisfied_credits: "0", needed_distinct_categories: null,
  distinct_categories: 0, allocated_courses: [], eligible_not_allocated: [], children: [], reason: "Engine reason text.",
  source_prose: null, curation_status: null, ...over,
});

const noEvidence = () => ({ allocations: [], grades: [] });

describe("degree", () => {
  it("an indeterminate requirement reads as 'can't determine', with the engine's reason", () => {
    const html = renderToStaticMarkup(
      <ul><RequirementNode node={req({ status: "indeterminate", reason: "Placement cannot be read from the record." })} evidence={noEvidence} filter="all" /></ul>);
    const t = text(html);
    expect(t).toContain("Can't determine");
    expect(t).toContain("Placement cannot be read from the record.");
    expect(t).toMatch(/not a finding that you fail/);
    expect(t).not.toMatch(/Not met|Failed|not satisfied/i);
  });

  it("shows the engine's counts and the Rutgers text, adding no number of its own", () => {
    const html = renderToStaticMarkup(
      <ul><RequirementNode node={req({ requirement_type: "credits", needed_credits: "12", satisfied_credits: "7", status: "partially_satisfied", source_prose: "Twelve credits of electives." })} evidence={noEvidence} filter="all" /></ul>);
    const t = text(html);
    expect(t).toContain("7 of 12 credits");
    expect(t).toContain("As published by Rutgers");
    expect(t).toContain("Twelve credits of electives.");
    expect(t).not.toMatch(/%/);
  });

  it("the status tally counts each leaf under the engine's own status", () => {
    const tree = req({ requirement_code: "ROOT", status: "partially_satisfied", children: [
      req({ requirement_code: "A", status: "satisfied" }),
      req({ requirement_code: "B", status: "indeterminate" }),
      req({ requirement_code: "C", status: "not_evaluable" }),
    ] });
    const t = leafTally([tree]);
    expect(t.satisfied).toBe(1);
    expect(t.indeterminate).toBe(1);
    expect(t.not_evaluable).toBe(1);
    expect(t.partially_satisfied).toBe(0);
  });

  it("filters hide a completed requirement from 'still needed', but never invent one", () => {
    const html = renderToStaticMarkup(<ul><RequirementNode node={req({ status: "satisfied" })} evidence={noEvidence} filter="remaining" /></ul>);
    expect(text(html).trim()).toBe("");
  });

  it("pending review is shown when the lifecycle OR the engine says so", () => {
    const supported = { support_status: "supported" as const, program_name: "P" };
    const pending = { support_status: "pending_review" as const, program_name: "P" };
    const finding = { findings: [{ severity: "warning" as const, code: "program_pending_review", message: "m", requirement_code: null, remediation: null }] };
    expect(renderToStaticMarkup(<PendingReviewNotice program={supported} audit={{ findings: [] }} />)).toBe("");
    expect(text(renderToStaticMarkup(<PendingReviewNotice program={pending} audit={{ findings: [] }} />))).toMatch(/pending human review/);
    expect(text(renderToStaticMarkup(<PendingReviewNotice program={supported} audit={finding} />))).toMatch(/pending human review/);
  });

  it("a what-if is labelled hypothetical and says the program is unchanged", () => {
    const t = text(renderToStaticMarkup(<ScenarioBanner programName="Mathematics (2026-2027)" />));
    expect(t).toMatch(/What-if scenario/);
    expect(t).toMatch(/Hypothetical/);
    expect(t).toMatch(/Your program is not changed/);
  });
});

const savedPlan = (over: Partial<SavedPlan>): SavedPlan => ({
  id: "p", created_at: "2026-10-18T00:00:00Z", updated_at: "2026-10-18T00:00:00Z", start_term: "20271",
  constraints: { max_credits_per_term: "15", max_courses_per_term: 5, max_terms: 8, include_summer: false, include_winter: false },
  freshness: { state: "current", reasons: [] }, blocked_courses: [],
  result: {
    target: { program_key: "k", program_name: "P", program_code: "P", degree_type: "BA", school_code: "01", catalog_year: "2026-2027",
              support_status: "supported", curation_status: "c", is_current_program: true },
    status: "partial", terms: [], issues: [], requirements: [], assumptions: [], observations: [],
    metadata: { planning_engine_version: "6.5.0", audit_engine_version: "7.0.1", start_term: "20271",
                constraints: { max_credits_per_term: "15", max_courses_per_term: 5, max_terms: 8, include_summer: false, include_winter: false },
                constraints_source: "s", academic_fingerprint: "a", rules_fingerprint: "r", offering_dataset: [] },
  },
  ...over,
});

describe("plan", () => {
  it("a current plan shows no staleness notice; a stale one names what changed", () => {
    expect(renderToStaticMarkup(<StaleNotice plan={savedPlan({})} onRegenerate={() => {}} regenerating={false} />)).toBe("");
    const t = text(renderToStaticMarkup(
      <StaleNotice plan={savedPlan({ freshness: { state: "stale", reasons: ["academic_record_changed", "rutgers_data_changed"] } })} onRegenerate={() => {}} regenerating={false} />));
    expect(t).toMatch(/out of date/);
    expect(t).toContain("Your academic record changed.");
    expect(t).toContain("CoursePilot loaded newer Rutgers course data.");
  });

  it("courses needing confirmation show the Rutgers condition and are not called ineligible", () => {
    const t = text(renderToStaticMarkup(
      <Blockers
        issues={[{ severity: "needs_confirmation", code: "ELIGIBILITY_UNKNOWN", message: "Some courses could not be evaluated.", requirement_code: "R", course: null, details: {} }]}
        blocked={[{ course: "01:198:442", title: "Combinatorial Optimization", issue_code: "ELIGIBILITY_UNKNOWN", requirement_code: "R",
                    reasons: ["unmodeled_condition:instructor_permission"], prerequisite_text: "Permission of instructor.", prerequisite_term: "20261" }]}
      />));
    expect(t).toContain("Courses that need confirmation");
    expect(t).toContain("01:198:442");
    expect(t).toContain("Permission of instructor.");
    expect(t).toMatch(/not a finding that you are ineligible/);
    expect(t).toContain("Needs confirmation");
  });
});

const opt: ScheduleOption = {
  rank: 1, total_credits: "4", reasons: ["no_time_conflicts", "required_components_included"], issues: [], days: ["M"],
  score: { needs_confirmation: 0, unknown_time_sections: 1, preference_misses: 0, class_days: 1, gap_minutes: 0, closed_sections_if_current: 0, unknown_availability_if_current: 0 },
  availability: { state: "open", freshness: "stale", as_of: null, all_open_now: false, courses: [] },
  choices: [{
    course: "01:198:111", component: "primary", course_string: "01:198:111", supplement_code: "", title: "Intro to CS",
    section_number: "01", index_number: "09211", campus_code: "NB", instructors: [],
    meetings: [
      { kind: "timed", day: "M", start_minute: 600, end_minute: 680, start_date: null, end_date: null, mode_code: null, mode_desc: "LEC", campus: "CAC", campus_name: null, building: "HCK", room: "110" },
      { kind: "arranged", day: null, start_minute: null, end_minute: null, start_date: null, end_date: null, mode_code: null, mode_desc: null, campus: null, campus_name: null, building: null, room: null },
    ],
    availability: { state: "open", freshness: "stale", observed_at: "2026-10-14T02:31:00Z", source: "openSections.json", source_hash: null, since: null, reasons: [] },
    restriction: { outcome: "unrestricted", entries: [], matched: null, open_to_text: null, eligibility_text: null, special_permission: null, reasons: [] },
    cross_listed_indexes: [], time_verified: false, notes: null, equivalent_sections: [], credits: "4", meeting_components: [], component_evidence: null, meeting_text_in_notes: null,
  }],
};

describe("schedule", () => {
  it("the calendar lists each day's meetings as text and keeps untimed meetings off it", () => {
    const t = text(renderToStaticMarkup(<WeekCalendar option={opt} />));
    expect(t).toContain("10:00 AM to 11:20 AM");
    expect(t).toContain("Not on the calendar");
    expect(t).toMatch(/By arrangement/);
  });

  it("registration is the student's, in WebReg - CoursePilot never claims it", () => {
    const t = text(renderToStaticMarkup(<RegistrationHandoff option={opt} />));
    expect(t).toContain("09211");
    expect(t).toMatch(/doesn't register you/);
    expect(t).not.toMatch(/\b(you are|you're|successfully) registered\b|added to your schedule|enrolled you/i);
  });

  it("structural validity and live availability are separate statements", () => {
    const result = { metadata: { availability_freshness: "stale", availability_observed_at: "2026-10-14T02:31:00Z" } } as unknown as ScheduleResult;
    const t = text(renderToStaticMarkup(<ValidityAndAvailability option={opt} result={result} />));
    expect(t).toContain("Why this schedule is valid");
    expect(t).toContain("No two meetings overlap");
    expect(t).toMatch(/Open seats \(live, separate from validity\)/);
    expect(t).toMatch(/Not current/);
    expect(t).toMatch(/A valid schedule can include a section that is closed/);
  });

  it("the ranking is explained and never called 'best'", () => {
    const t = text(renderToStaticMarkup(<RankingNote objective={["needs_confirmation", "class_days", "gap_minutes", "index_numbers"]} />));
    expect(t).toMatch(/fixed order, not by quality/);
    expect(t).toMatch(/not "the best"/);
  });
});
