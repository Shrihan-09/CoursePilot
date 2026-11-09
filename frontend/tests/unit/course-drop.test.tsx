import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { PlanDiffView, type PlanChanges } from "@/features/plan/plan-changes";
import { SemesterCard, type BoardActions } from "@/features/plan/plan-result";
import type { PlanDiff, PlanTerm } from "@/types/plan";

const warning = "15 credits, below your 16-credit minimum for Spring 2027. You chose to leave this space open; no replacement was added.";

describe("explicit course drop presentation", () => {
  it("shows the credit drop, persistent warning, later requirement contributions and graduation effect", () => {
    const diff = { summary: "Drop the course", big: true, forced_moves: [], requirements_lost: [], requirements_gained: [], new_issues: [],
      last_term_before: "20291", last_term_after: "20299", graduation_after: "20299", terms: [{ term_code: "20271", label: "Spring 2027", credits_before: "18", credits_after: "15", added: [], removed: ["01:510:101"], before: [{ course: "01:510:101", title: "World History" }], after: [], warnings: [warning], requirement_effects: ["Historical Analysis: planned contributions now in Fall 2027."] }] } as unknown as PlanDiff;
    const html = renderToStaticMarkup(<PlanDiffView diff={diff} />);
    expect(html).toContain("18"); expect(html).toContain("15 credits");
    expect(html).toContain(warning);
    expect(html).toContain("Historical Analysis: planned contributions now in Fall 2027.");
    expect(html).toContain("Fall 2029");
    expect(html).not.toContain("Added:");
  });

  it("offers Suggest a replacement without requesting a change during rendering", () => {
    const request = vi.fn(), suggest = vi.fn();
    const actions = { changes: { request } as unknown as PlanChanges, states: new Map(), lockedTerms: [], onOptions: vi.fn(), onAddCourse: suggest } as BoardActions;
    const term = { term_code: "20271", label: "Spring 2027", credits: "15", courses: [], warnings: [warning] } as unknown as PlanTerm;
    const html = renderToStaticMarkup(<SemesterCard term={term} scheduleHref={null} actions={actions} />);
    expect(html).toContain(warning);
    expect(html).toContain("Suggest a replacement");
    expect(request).not.toHaveBeenCalled();
    expect(suggest).not.toHaveBeenCalled();
  });
});
