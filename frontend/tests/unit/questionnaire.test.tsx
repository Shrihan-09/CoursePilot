/**
 * Task 2 planning questionnaire presenters. Pins:
 *   - commitments map to the server's hours and budget drop;
 *   - a skipped answer reads as the default (no commitments, not honors);
 *   - pace and workload level stay separate, with a conflict warning;
 *   - fixed weekly times and per-semester answers are shown;
 *   - each plan semester shows "Why this fits you" and any shortfall;
 *   - no wording calls a course easy or hard.
 */
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { SemesterCard } from "@/features/plan/plan-result";
import { profileLines } from "@/features/planning/profile-summary";
import { answersInvalid, clearedAnswers } from "@/features/planning/questionnaire-fields";
import {
  blockedTimeText,
  budgetReduction,
  commitmentHours,
  commitmentSummary,
  JOB_BANDS,
  paceLevelConflict,
} from "@/lib/academic/questionnaire";
import { NO_COMMITMENTS, type PlanningProfileInput } from "@/types/personalization";
import type { PlanTerm } from "@/types/plan";

const EASY_HARD = /\b(easy|easier|hard|harder|difficult)\b/i;
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/\s+/g, " ");

const base: PlanningProfileInput = {
  pace: "balanced", target_credits_per_term: 15, max_credits_per_term: 18, max_courses_per_term: 6, max_terms: null,
  include_summer: false, include_winter: false,
  schedule: { earliest_start: null, latest_end: null, avoid_days: [] },
  additional_goals: [], workload_level: "semi_rigorous",
};

describe("questionnaire mappings", () => {
  it("maps commitments to the server's hours and budget drop", () => {
    const c = { ...NO_COMMITMENTS, job_hours_per_week: 10, greek_life: "new_member" as const, clubs: "heavy" as const };
    expect(commitmentHours(c)).toBe(28);
    expect(budgetReduction(15)).toBe(5);
    expect(budgetReduction(4)).toBe(1.5);
    expect(commitmentSummary(c)).toMatch(/About 28 hours a week/);
    expect(commitmentSummary(NO_COMMITMENTS)).toBe("No commitments");
    expect(commitmentSummary(undefined)).toBe("No commitments");
  });

  it("warns when the graduation target and level pull apart, never otherwise", () => {
    expect(paceLevelConflict("finish_earlier", "light")).toMatch(/early/);
    expect(paceLevelConflict("lighter", "challenging")).toMatch(/No rush/);
    expect(paceLevelConflict("balanced", "light")).toBeNull();
    expect(paceLevelConflict("finish_earlier", null)).toBeNull();
  });

  it("describes a fixed time by type only", () => {
    expect(blockedTimeText({ kind: "job", days: ["T", "H"], start: "13:00", end: "17:00" })).toBe("Job shift: TH 13:00-17:00");
  });
});

describe("profile summary", () => {
  it("reads skipped answers as the defaults", () => {
    const lines = Object.fromEntries(profileLines(base));
    expect(lines["Commitments"]).toBe("No commitments");
    expect(lines["Honors program"]).toMatch(/^Not answered - honors-only courses are not planned/);
    expect(lines["Semesters answered separately"]).toBeUndefined();
  });

  it("shows commitments, honors, fixed times and per-semester answers", () => {
    const lines = Object.fromEntries(profileLines({
      ...base, honors: true,
      commitments: { ...NO_COMMITMENTS, job_hours_per_week: 15 },
      schedule: { ...base.schedule, blocked_times: [{ kind: "club", days: ["W"], start: "18:00", end: "19:00" }] },
      term_overrides: [{ term_code: "20279", major_courses_min: 1, major_courses_max: 1, commitments: null, blocked_times: [] }],
    }));
    expect(lines["Commitments"]).toMatch(/About 15 hours a week, so each semester's workload budget is 5 lower/);
    expect(lines["Honors program"]).toMatch(/^Yes/);
    expect(lines["Schedule"]).toContain("Busy Club or org meeting: W 18:00-19:00");
    expect(lines["Semesters answered separately"]).toBe("Fall 2027: 1 major-related courses, 0 fixed times");
    expect(Object.values(lines).join(" ")).not.toMatch(EASY_HARD);
  });
});

describe("Prompt 09 questions", () => {
  it("shows the student's own credit and major ranges and campuses", () => {
    const lines = Object.fromEntries(profileLines({
      ...base, credit_min: 15, credit_max: 18, major_courses_min: 2, major_courses_max: 3,
      schedule: { ...base.schedule, preferred_campuses: ["BUS", "LIV"] },
    }));
    expect(lines["Credits per semester"]).toBe("15-18 (the minimum is kept)");
    expect(lines["Major courses per semester"]).toBe("2-3");
    expect(lines["Schedule"]).toContain("Prefer BUS, LIV");
  });

  it("clears every answer to its default", () => {
    const cleared = clearedAnswers({
      commitments: { ...NO_COMMITMENTS, job_hours_per_week: 15 }, honors: true,
      blocked_times: [{ kind: "job", days: ["M"], start: "09:00", end: "12:00" }], term_overrides: [],
      credit_min: 15, credit_max: 18, major_min: 1, major_max: 1, preferred_campuses: ["BUS"],
      schedule_shape: { earliest_start: "10:00", latest_end: null, avoid_days: ["F"] }, pins: [{ course: "01:640:152", term_code: "20271" }],
    });
    expect(cleared).toEqual({
      commitments: NO_COMMITMENTS, honors: null, blocked_times: [], term_overrides: [],
      credit_min: null, credit_max: null, major_min: null, major_max: null, preferred_campuses: [],
      schedule_shape: { earliest_start: null, latest_end: null, avoid_days: [] }, pins: [],
    });
  });

  it("refuses a minimum above the maximum", () => {
    const ok = clearedAnswers({ commitments: NO_COMMITMENTS, honors: null, blocked_times: [], term_overrides: [],
      credit_min: null, credit_max: null, major_min: null, major_max: null, preferred_campuses: [] });
    expect(answersInvalid(ok)).toBeNull();
    expect(answersInvalid({ ...ok, credit_min: 19, credit_max: 16 })).toMatch(/credit range/);
  });

  it("offers job hours as bands", () => {
    expect(JOB_BANDS.map((b) => b.hours)).toEqual([0, 8, 15, 25]);
  });
});

describe("plan semester", () => {
  it("shows why the semester fits and any shortfall", () => {
    const term: PlanTerm = {
      term_code: "20271", label: "Spring 2027", credits: "15", courses: [],
      workload: {
        level: "semi_rigorous", budget: "19", total: "18", credit_floor: "16", credit_max: "19", contact_hours: "14",
        notes: [], base_budget: "24", commitment_hours: 15, major_related: 2, major_min: 3, major_max: 3,
        major_range_source: "level",
        fit: "Why this fits you: Semi-rigorous · 2 major-related courses (your range: 3) · commitments 15 hours a week (job), so a workload budget of 19 instead of 24 · workload 18 of 19.",
        shortfalls: ["2 major-related courses, below your range of 3: the workload budget was reached first."],
      },
    } as PlanTerm;
    const t = text(renderToStaticMarkup(<SemesterCard term={term} scheduleHref={null} />));
    expect(t).toContain("Why this fits you: Semi-rigorous");
    expect(t).toContain("below your range of 3: the workload budget was reached first");
    expect(t).not.toMatch(EASY_HARD);
  });
});
