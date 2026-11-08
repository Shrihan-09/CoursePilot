/**
 * Correctness fixes, plan board wording: one credit range (item 6), the
 * "Consider Light" suggestion (item 10), named conditional prerequisites
 * (item 11) and free electives as a course slot (item 12). Fabricated data.
 */
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { PlanView, SemesterCard } from "@/features/plan/plan-result";
import { conditionalLabel, creditsText, electiveSlotText, loadText } from "@/lib/academic/plan-text";
import { planReasonText } from "@/lib/academic/vocabulary";
import type { PlannedCourse, PlanTerm, SavedPlan } from "@/types/plan";

const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;|&apos;/g, "'").replace(/\s+/g, " ");

const constraints = { max_credits_per_term: "19", target_credits_per_term: "16", max_courses_per_term: 6,
  max_terms: 8, include_summer: false, include_winter: false, workload_level: "semi_rigorous" };

const plan = (observations: SavedPlan["result"]["observations"]): SavedPlan => ({
  id: "p", created_at: "2026-11-08T00:00:00Z", updated_at: "2026-11-08T00:00:00Z", start_term: "20271",
  constraints, freshness: { state: "current", reasons: [] }, blocked_courses: [],
  result: {
    target: { program_key: "k", program_name: "P", program_code: "P", degree_type: "BS", school_code: "01",
              catalog_year: "2026-2027", support_status: "supported", curation_status: "c", is_current_program: true },
    status: "covers_all_requirements", terms: [], issues: [], requirements: [], assumptions: [], observations,
    metadata: { planning_engine_version: "x", audit_engine_version: "y", start_term: "20271", constraints,
                constraints_source: "s", academic_fingerprint: "a", rules_fingerprint: "r", offering_dataset: [] },
  },
} as unknown as SavedPlan);

describe("item 6: one credit range", () => {
  it("a workload level shows its range, never the old target/maximum", () => {
    expect(loadText(constraints)).toBe("Semi-rigorous: 16-19 credits and at most 6 courses a semester");
    const t = text(renderToStaticMarkup(<PlanView plan={plan([])} onRegenerate={() => {}} regenerating={false} />));
    expect(t).toContain("Semi-rigorous: 16-19 credits");
    expect(t).not.toMatch(/aiming for 15|never more than 18/);
  });

  it("the student's own range wins over the level's", () => {
    expect(loadText({ ...constraints, credit_min: 13, credit_max: 15 })).toBe(
      "Semi-rigorous: 13-15 credits and at most 6 courses a semester");
  });

  it("without a level the target stays soft and the maximum hard", () => {
    expect(loadText({ ...constraints, workload_level: null, target_credits_per_term: "15", max_credits_per_term: "18" }))
      .toBe("aiming for 15 credits, never more than 18 credits or 6 courses a semester");
  });
});

describe("item 10: Consider Light for the rush semester", () => {
  const light = { code: "consider_light_term", term_code: "20271", message: "Consider Light for your rush semester (Spring 2027): ...",
    details: { level: "light", credit_min: 12, credit_max: 14, major_courses_min: 1, major_courses_max: 2 } };

  it("shows one suggestion with a one-tap button that applies it", () => {
    const applied: unknown[] = [];
    const t = text(renderToStaticMarkup(
      <PlanView plan={plan([light])} onRegenerate={() => {}} regenerating={false} onApplyLight={(o) => applied.push(o)} />));
    expect(t).toContain("Consider Light for Spring 2027");
    expect(t).toContain("Use Light for Spring 2027");
    expect(t.match(/Consider Light for your rush semester/g)?.length).toBe(1);
  });

  it("is absent when the plan has no such observation", () => {
    const t = text(renderToStaticMarkup(<PlanView plan={plan([])} onRegenerate={() => {}} regenerating={false} />));
    expect(t).not.toContain("Consider Light");
  });
});

describe("item 11: conditional prerequisites name the exact courses and terms", () => {
  it("names a planned course and its term", () => {
    expect(conditionalLabel([{ course: "01:640:152", term_code: "20271", source: "planned", required_grade: null }]))
      .toBe("Prerequisites met once you pass 01:640:152 in Spring 2027");
  });

  it("names several, with an in-progress one and a grade", () => {
    expect(conditionalLabel([
      { course: "01:198:211", term_code: "20269", source: "in_progress", required_grade: "C" },
      { course: "01:640:152", term_code: "20271", source: "planned", required_grade: null },
    ])).toBe("Prerequisites met once you pass 01:198:211 with C or better (in progress, Fall 2026) and 01:640:152 in Spring 2027");
  });

  it("the 'why' reason names them too", () => {
    const course = { prerequisite: { conditional_on: [{ course: "01:640:152", term_code: "20271", source: "planned", required_grade: null }] },
      requirements: [], offering: { kind: "historically_offered_in_season" } } as unknown as PlannedCourse;
    expect(planReasonText("prerequisites_conditional_on_plan", course))
      .toBe("Its prerequisites are met once you pass 01:640:152 in Spring 2027");
  });
});

describe("item 12: free electives are a course slot", () => {
  it("grammar and slots", () => {
    expect(creditsText(1)).toBe("1 credit");
    expect(creditsText(4)).toBe("4 credits");
    expect(electiveSlotText(1)).toBe("one 1-credit elective");
    expect(electiveSlotText(3)).toBe("one ~3-credit elective");
    expect(electiveSlotText(4)).toBe("one 4-credit elective");
    expect(electiveSlotText(6)).toBe("two electives (about 6 credits)");
    expect(electiveSlotText(7)).toBe("two electives (about 7 credits)");
  });

  it("the semester card shows the slot, never '1 free-elective credits'", () => {
    const term = { term_code: "20271", label: "Spring 2027", courses: [], credits: "17", free_elective_credits: "1",
      cumulative_credits: "80", workload: null } as unknown as PlanTerm;
    const t = text(renderToStaticMarkup(<SemesterCard term={term} scheduleHref={null} />));
    expect(t).toContain("+ one 1-credit elective");
    expect(t).toContain("1 credit toward the degree total");
    expect(t).not.toContain("1 free-elective credits");
  });
});
