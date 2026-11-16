import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import NotFound from "@/app/not-found";
import AppError from "@/app/error";
import { SemesterCard } from "@/features/plan/plan-result";
import { friendlyErrorMessage } from "@/lib/api";
import { courseTitle, SOC_ABBREVIATIONS, type AbbreviationEntry } from "@/lib/academic/course-title";
import { fitChips, overBudget } from "@/lib/academic/plan-text";
import type { PlanTerm, PlannedCourse } from "@/types/plan";

const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/\s+/g, " ");

describe("course titles (A1)", () => {
  it("title-cases SOC upper/lower case titles with minor-word exceptions", () => {
    expect(courseTitle("data structures")).toBe("Data Structures");
    expect(courseTitle("DATA STRUCTURES")).toBe("Data Structures");
    expect(courseTitle("principles of information and data management")).toBe("Principles of Information and Data Management");
    expect(courseTitle("INTRO TO ARTIFICIAL INTELLIGENCE")).toBe("Intro to Artificial Intelligence");
    expect(courseTitle("the art of the possible")).toBe("The Art of the Possible");
    expect(courseTitle("what it's for")).toBe("What It's For");
  });

  it("keeps acronyms and Roman numerals upper-case", () => {
    expect(courseTitle("CALCULUS II FOR THE MATHEMATICAL AND PHYSICAL SCIENCES")).toBe("Calculus II for the Mathematical and Physical Sciences");
    expect(courseTitle("ethics of ai")).toBe("Ethics of AI");
    expect(courseTitle("SPANISH III")).toBe("Spanish III");
    expect(courseTitle("HISTORY OF THE US")).toBe("History of the US");
    expect(courseTitle("COMICS MIX")).toBe("Comics Mix");
  });

  it("handles hyphens, ampersands and punctuation", () => {
    expect(courseTitle("SELF-DIRECTED STUDY")).toBe("Self-Directed Study");
    expect(courseTitle("current moral & social issues")).toBe("Current Moral & Social Issues");
    expect(courseTitle("WRITING: (ADVANCED)")).toBe("Writing: (Advanced)");
  });

  it("leaves deliberately mixed-case titles untouched", () => {
    expect(courseTitle("Data Management for Data Science")).toBe("Data Management for Data Science");
    expect(courseTitle("iOS Development")).toBe("iOS Development");
  });

  it("never expands an abbreviation that is not reviewed", () => {
    expect(SOC_ABBREVIATIONS.every((e) => !e.reviewed)).toBe(true);
    expect(courseTitle("numer anlys & comput")).toBe("Numer Anlys & Comput");
  });

  it("expands only reviewed entries that cite a source", () => {
    const table: AbbreviationEntry[] = [
      { short: "anlys", expanded: "Analysis", reviewed: true, source: "SOC expandedTitle, 01:640:373" },
      { short: "numer", expanded: "Numerical", reviewed: true, source: null },
      { short: "comput", expanded: "Computing", reviewed: false, source: "x" },
    ];
    expect(courseTitle("NUMER ANLYS & COMPUT", table)).toBe("Numer Analysis & Comput");
  });

  it("is empty for a missing title", () => {
    expect(courseTitle(null)).toBe("");
    expect(courseTitle("  ")).toBe("");
  });
});

const workload = (extra: Partial<NonNullable<PlanTerm["workload"]>> = {}) => ({
  level: "semi_rigorous", budget: "40", total: "46", credit_floor: "12", credit_max: "18", contact_hours: "20", notes: [],
  major_related: 3, fit: "Why this fits you: Semi-rigorous + rushing + job 10 h/week → 18 credits, 3 major-related courses (your range: 2-3), workload 46 of 40.",
  shortfalls: [], ...extra,
});

describe("semester header (A2)", () => {
  it("turns the fit sentence's inputs into chips plus the major count", () => {
    expect(fitChips(workload())).toEqual(["Semi-rigorous", "Rushing", "Job 10h/wk", "3 major courses"]);
    expect(fitChips(workload({ major_related: 1, fit: "Why this fits you: Light → 12 credits." }))).toEqual(["Light", "1 major course"]);
    expect(fitChips(workload({ fit: "Some other wording entirely." }))).toEqual(["3 major courses"]);
    expect(fitChips(null)).toEqual([]);
  });

  it("flags workload over budget, and only then", () => {
    expect(overBudget({ total: "46", budget: "40" })).toBe(6);
    expect(overBudget({ total: "40", budget: "40" })).toBeNull();
    expect(overBudget({ total: "18", budget: "48" })).toBeNull();
    expect(overBudget({ total: "5", budget: "0" })).toBeNull();
    expect(overBudget(null)).toBeNull();
  });

  const course = (code: string, title: string, system: string | null, role: PlannedCourse["role"] = "major"): PlannedCourse => ({
    course: code, title, credits: "4", term_code: "20271", reasons: [], role,
    requirements: system ? [{ requirement_code: "R", requirement_name: "Req", requirement_system: system, provisional: false }] : [],
    prerequisite: { state: "satisfied_by_history", rules_term: null, raw_text: null, canonical_text: null, combination: null, corequisite_text: null, conditional_on: [], unknown_reasons: [] },
    offering: { kind: "confirmed_in_term", target_term: "20271", observed_terms: [], same_season_terms: [], most_recent_term: null, rules_term: null, target_term_coverage: null },
    corequisite_group: [], unlocks: [], interchangeable_alternatives: 0,
    workload: { weight: "9", credits: "4", contact_hours: "4", has_lab: false, level: 300, technical: null, components: {}, delivery: ["online"], basis_term: null },
  });
  const term = (w = workload()): PlanTerm => ({ term_code: "20271", label: "Spring 2027", credits: "8",
    courses: [course("01:198:213", "software methodology", "major", "anchor"), course("01:730:105", "CURRENT MORAL & SOCIAL ISSUES", null, "filler")], workload: w });

  it("renders chips, the over-budget chip and keeps the full sentence behind the info button", () => {
    const html = renderToStaticMarkup(<SemesterCard term={term()} scheduleHref={null} />);
    const t = text(html);
    expect(html).toContain('data-testid="over-budget"');
    expect(t).toContain("Over budget by 6");
    expect(t).toContain("Job 10h/wk");
    expect(t).toContain("Why this fits you");
    expect(html).toMatch(/aria-expanded="false"/);
    // The sentence is present (for the toggle) but hidden until opened.
    expect(html).toMatch(/<p id="[^"]+" hidden="" [^>]*>Why this fits you: Semi-rigorous/);
  });

  it("has no over-budget chip when within budget", () => {
    const html = renderToStaticMarkup(<SemesterCard term={term(workload({ total: "30" }))} scheduleHref={null} />);
    expect(html).not.toContain('data-testid="over-budget"');
  });
});

describe("course cards (A3)", () => {
  it("shows the title first in title case, the code muted after it, category chip, delivery label and anchor badge", () => {
    const html = renderToStaticMarkup(<SemesterCard term={{ term_code: "20271", label: "Spring 2027", credits: "8", workload: workload(),
      courses: [{ ...(renderCourse()), role: "anchor" }] }} scheduleHref={null} />);
    const t = text(html);
    expect(t).toMatch(/Software Methodology 01:198:213/);
    expect(html).toContain('data-testid="category-chip"');
    expect(t).toContain("Major");
    expect(html).toContain('data-testid="anchor-badge"');
    expect(html).toContain('role="tooltip"');
    expect(t).toContain("has an online section");
  });
});

function renderCourse(): PlannedCourse {
  return {
    course: "01:198:213", title: "software methodology", credits: "4", term_code: "20271", reasons: [], role: "major",
    requirements: [{ requirement_code: "R", requirement_name: "CS core", requirement_system: "major", provisional: false }],
    prerequisite: { state: "satisfied_by_history", rules_term: null, raw_text: null, canonical_text: null, combination: null, corequisite_text: null, conditional_on: [], unknown_reasons: [] },
    offering: { kind: "confirmed_in_term", target_term: "20271", observed_terms: [], same_season_terms: [], most_recent_term: null, rules_term: null, target_term_coverage: null },
    corequisite_group: [], unlocks: [], interchangeable_alternatives: 0,
    workload: { weight: "9", credits: "4", contact_hours: "4", has_lab: false, level: 300, technical: null, components: {}, delivery: ["online"], basis_term: null },
  };
}

describe("errors (A5)", () => {
  it("styled not-found page offers a way home", () => {
    const html = renderToStaticMarkup(<NotFound />);
    expect(text(html)).toContain("Page not found");
    expect(html).toContain('href="/home"');
    expect(text(html)).toContain("Go home");
    expect(html).not.toContain("This page could not be found");
  });

  it("styled error page offers Try again and Go home", () => {
    const html = renderToStaticMarkup(<AppError error={new Error("Internal Server Error")} reset={() => {}} />);
    const t = text(html);
    expect(t).toContain("Try again");
    expect(t).toContain("Go home");
    expect(t).not.toContain("Internal Server Error");
  });

  it("never shows a bare Internal Server Error from a failed request", () => {
    expect(friendlyErrorMessage(500, "Internal Server Error")).not.toMatch(/internal server error/i);
    expect(friendlyErrorMessage(500, undefined)).toMatch(/couldn't finish/);
    expect(friendlyErrorMessage(502, null)).toMatch(/can't be reached/);
    expect(friendlyErrorMessage(500, "Unable to generate a plan.")).toBe("Unable to generate a plan.");
    expect(friendlyErrorMessage(409, "Your plan changed; reload it.")).toBe("Your plan changed; reload it.");
  });
});
