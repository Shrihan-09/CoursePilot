/**
 * Correctness fixes item 3: a choose-one requirement (ANY_OF, e.g. Science
 * Requirement B.S. with 8 alternatives) counts as ONE unit on the Degree page,
 * using the plan's chosen alternative, else the furthest-along one. Unchosen
 * alternatives are never "still needed". Fabricated data only.
 */
import { describe, expect, it } from "vitest";

import { countedChildren, leafTally, matchesFilter } from "@/features/degree/requirement-tree";
import type { RequirementResult, RequirementStatus } from "@/types/degree";
import { planChoices } from "@/types/plan";

const leaf = (code: string, status: RequirementStatus): RequirementResult => ({
  requirement_code: code, requirement_name: code, requirement_type: "course", status,
  needed_count: 1, satisfied_count: status === "satisfied" ? 1 : 0, needed_credits: null, satisfied_credits: "0",
  needed_distinct_categories: null, distinct_categories: 0, allocated_courses: [], eligible_not_allocated: [],
  children: [], reason: "", source_prose: null, curation_status: null,
} as unknown as RequirementResult);

const group = (code: string, type: string, children: RequirementResult[], status: RequirementStatus): RequirementResult => ({
  ...leaf(code, status), requirement_type: type, needed_count: type === "any_of" ? 1 : null, children,
} as unknown as RequirementResult);

const seq = (code: string, done: number, total: number) => group(code,
  "all_of",
  Array.from({ length: total }, (_, i) => leaf(`${code}_${i}`, i < done ? "satisfied" : "unsatisfied")),
  done === total ? "satisfied" : done ? "partially_satisfied" : "unsatisfied");

const science = group("SCIENCE", "any_of", [
  seq("PHYS_A", 1, 4), seq("CHEM", 2, 4),
  ...["BIO", "GEO", "ALT4", "ALT5", "ALT6", "ALT7"].map((c) => seq(c, 0, 3)),
], "partially_satisfied");
const major = group("MAJOR", "all_of", [leaf("CS_111", "satisfied"), science], "partially_satisfied");

describe("a choose-one requirement is one unit", () => {
  it("counts the chosen alternative once, never its siblings", () => {
    const t = leafTally([major], new Set(), { SCIENCE: ["PHYS_A"] });
    expect(t).toMatchObject({ satisfied: 1, partially_satisfied: 1, unsatisfied: 0 });
  });

  it("before the fix every leaf of all 8 alternatives was tallied (28 leaves)", () => {
    // The fixed tally: 2 units in all. The old walk counted 1 + 4 + 4 + 6 x 3 = 27 + 1.
    const t = leafTally([major]);
    const total = Object.values(t).reduce((a, b) => a + b, 0);
    expect(total).toBe(2);
  });

  it("uses the furthest-along alternative when none is chosen", () => {
    expect(countedChildren(science).map((c) => c.requirement_code)).toEqual(["CHEM"]);
    expect(countedChildren(science, { SCIENCE: ["PHYS_A"] }).map((c) => c.requirement_code)).toEqual(["PHYS_A"]);
  });

  it("a met choose-one counts as one satisfied unit", () => {
    const met = { ...science, status: "satisfied" } as RequirementResult;
    expect(leafTally([met])).toMatchObject({ satisfied: 1, unsatisfied: 0, partially_satisfied: 0 });
  });

  it("'Still needed' shows only the chosen alternative's remaining courses", () => {
    const choices = { SCIENCE: ["PHYS_A"] };
    const bio = science.children.find((c) => c.requirement_code === "BIO")!;
    expect(matchesFilter(science, "remaining", new Set(), choices)).toBe(true);
    expect(countedChildren(science, choices)).not.toContain(bio);
    // BIO is unsatisfied on its own, but it is not what the student is doing.
    const onlyBio = group("ONLY", "any_of", [seq("PHYS_A", 4, 4), bio], "satisfied");
    expect(matchesFilter(onlyBio, "remaining", new Set(), { ONLY: ["PHYS_A"] })).toBe(false);
  });

  it("reads the plan's chosen alternatives, skipping undecided ones", () => {
    const plan = { result: { metadata: { alternative_choices: [
      { requirement_code: "SCIENCE", status: "planner_recommended", chosen: [{ code: "PHYS_A", name: "Physics" }] },
      { requirement_code: "X", status: "undecided", chosen: [] },
    ] } } } as unknown as Parameters<typeof planChoices>[0];
    expect(planChoices(plan)).toEqual({ SCIENCE: ["PHYS_A"] });
    expect(planChoices(null)).toEqual({});
  });
});
