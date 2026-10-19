/**
 * Phase 7.2.1 presenters: planning preferences, readiness, observations,
 * current schedule. Each pins a rule from the brief:
 *   - a target is not a maximum and a maximum is "never more";
 *   - BALANCED is the default and finishing earlier is never assumed;
 *   - additional programs are shown as EXPLORING, never as part of the plan;
 *   - a readiness gap is "needed", not failure;
 *   - earlier completion is offered as a possibility about MODELED
 *     requirements, never a graduation claim;
 *   - a current schedule shows only CONFIRMED sections.
 */
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { StaleNotice } from "@/features/plan/plan-result";
import { profileLines, ProfileSummary, ReadinessList } from "@/features/planning/profile-summary";
import { asOption } from "@/features/schedule/current-schedule";
import { WeekCalendar } from "@/features/schedule/week-calendar";
import { READINESS_CHECK, STALE_REASON } from "@/lib/academic/vocabulary";
import type { PlanningProfileInput } from "@/types/personalization";
import type { SavedPlan } from "@/types/plan";
import type { SectionChoice } from "@/types/schedule";

const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/\s+/g, " ");

const profile: PlanningProfileInput = {
  pace: "balanced", target_credits_per_term: 16, max_credits_per_term: 18, max_courses_per_term: 6, max_terms: null,
  include_summer: false, include_winter: true,
  schedule: { earliest_start: "10:00", latest_end: null, avoid_days: ["F"] },
  additional_goals: [{ kind: "second_major", program_key: "sas-640-ba-option-a", label: "Mathematics", status: "exploring" }],
};

describe("planning preferences", () => {
  it("says target and maximum in their real meanings", () => {
    const lines = Object.fromEntries(profileLines(profile, "Computer Science"));
    expect(lines["Planning pace"]).toBe("Balanced");
    expect(lines["Target semester load"]).toBe("16 credits");
    expect(lines["Maximum semester load"]).toBe("18 credits, never more");
    expect(lines["Winter"]).toMatch(/Can include/);
    expect(lines["Summer"]).toBe("Not planned");
    expect(lines["Schedule"]).toContain("No classes before 10:00 AM");
    expect(lines["Schedule"]).toContain("Friday");
  });

  it("shows an additional program only as something being explored", () => {
    const t = text(renderToStaticMarkup(<ProfileSummary profile={profile} />));
    expect(t).toContain("Exploring Mathematics");
    expect(t).not.toMatch(/double major plan|combined/i);
  });
});

describe("readiness", () => {
  it("a gap is 'needed', not a failure", () => {
    expect(READINESS_CHECK.missing.tone).not.toBe("danger");
    expect(READINESS_CHECK.missing.icon).not.toBe("failed");
    const t = text(renderToStaticMarkup(<ReadinessList readiness={{ state: "needs_planning_profile", checks: [
      { code: "academic_history", state: "done", message: "Academic history confirmed." },
      { code: "planning_profile", state: "missing", message: "Tell CoursePilot how you'd like your semesters planned." },
    ] }} />));
    expect(t).toContain("Done: Academic history confirmed.");
    expect(t).toContain("Needed: Tell CoursePilot");
  });
});

const plan = (over: Partial<SavedPlan>): SavedPlan => ({
  id: "p", created_at: "2026-10-18T00:00:00Z", updated_at: "2026-10-18T00:00:00Z", start_term: "20271",
  constraints: { max_credits_per_term: "18", max_courses_per_term: 6, max_terms: 8, include_summer: false, include_winter: false,
                 target_credits_per_term: "16", pace: "balanced" },
  freshness: { state: "stale", reasons: ["planning_preferences_changed"] }, blocked_courses: [],
  result: {
    target: { program_key: "k", program_name: "P", program_code: "P", degree_type: "BA", school_code: "01", catalog_year: "2026-2027",
              support_status: "supported", curation_status: "c", is_current_program: true },
    status: "covers_all_requirements", terms: [], issues: [], requirements: [], assumptions: [], observations: [],
    metadata: { planning_engine_version: "6.6.0", audit_engine_version: "7.0.1", start_term: "20271",
                constraints: { max_credits_per_term: "18", max_courses_per_term: 6, max_terms: 8, include_summer: false, include_winter: false },
                constraints_source: "s", academic_fingerprint: "a", rules_fingerprint: "r", offering_dataset: [] },
  },
  ...over,
});

describe("plan", () => {
  it("a preference change makes the plan visibly out of date", () => {
    const t = text(renderToStaticMarkup(<StaleNotice plan={plan({})} onRegenerate={() => {}} regenerating={false} />));
    expect(t).toContain(STALE_REASON.planning_preferences_changed);
  });
});

const section = (index: string, day: "M" | "W", confirmed = true): SectionChoice => ({
  course: "01:198:211", component: "primary", course_string: "01:198:211", supplement_code: "", title: "Arch",
  section_number: "01", index_number: index, campus_code: "NB", instructors: [],
  meetings: [{ kind: "timed", day, start_minute: 600, end_minute: 680, start_date: null, end_date: null, mode_code: null,
               mode_desc: "LEC", campus: "BUS", campus_name: null, building: "HLL", room: "114" }],
  availability: { state: "unknown", freshness: "unobserved", observed_at: null, source: null, source_hash: null, since: null, reasons: [] },
  restriction: { outcome: "unrestricted", entries: [], matched: null, open_to_text: null, eligibility_text: null, special_permission: null, reasons: [] },
  cross_listed_indexes: [], time_verified: confirmed, notes: null, equivalent_sections: [], credits: "4",
  meeting_components: [], component_evidence: null, meeting_text_in_notes: null,
});

describe("current schedule", () => {
  it("draws exactly the confirmed sections - nothing inferred", () => {
    const t = text(renderToStaticMarkup(<WeekCalendar option={asOption([section("12345", "M")])} />));
    expect(t).toContain("10:00 AM to 11:20 AM");
    expect(t.match(/01:198:211/g)?.length).toBe(2);   // mobile agenda + desktop grid of ONE section
  });
});
