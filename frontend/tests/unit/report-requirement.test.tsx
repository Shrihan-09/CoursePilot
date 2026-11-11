/**
 * Prompt 08 B1: "Report a wrong requirement" on every requirement row, and the
 * plan's "after admission" label. Fabricated data only.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ReportRequirement, ReportTargetContext } from "@/features/degree/report-requirement";
import { RequirementNode } from "@/features/degree/requirement-tree";
import type { RequirementResult } from "@/types/degree";

const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;|&apos;/g, "'").replace(/\s+/g, " ");

function render(node: React.ReactNode, target = true) {
  const tree = target
    ? <ReportTargetContext.Provider value={{ program_key: "zz-999-bs", catalog_year: "2026-2027" }}>{node}</ReportTargetContext.Provider>
    : node;
  return renderToStaticMarkup(<QueryClientProvider client={new QueryClient()}>{tree}</QueryClientProvider>);
}

const leaf = (code: string, name: string): RequirementResult => ({
  requirement_code: code, requirement_name: name, requirement_type: "course", requirement_system: "major",
  status: "unsatisfied", reason: "0 of 1 courses completed.", needed_count: 1, satisfied_count: 0,
  needed_credits: null, satisfied_credits: "0", needed_distinct_categories: null, distinct_categories: 0,
  allocated_courses: [], children: [], source_prose: null, curation_status: "curated_from_prose",
} as unknown as RequirementResult);

describe("report a wrong requirement", () => {
  it("is offered on every requirement row of a program's tree", () => {
    const root = { ...leaf("SYN_ROOT", "Synthetic Major"), requirement_type: "all_of",
      children: [leaf("SYN_A", "Synthetic Course A"), leaf("SYN_B", "Synthetic Course B")] } as RequirementResult;
    const t = text(render(<ul><RequirementNode node={root} evidence={() => ({ allocations: [], grades: [] })} filter="all" /></ul>));
    const buttons = t.match(/Report a problem with this requirement/g) ?? [];
    expect(buttons.length).toBe(3);
  });

  it("is absent where the tree has no program to report against", () => {
    const t = text(render(<ReportRequirement code="SYN_A" name="Synthetic Course A" />, false));
    expect(t.trim()).toBe("");
  });

  it("names the requirement for screen readers", () => {
    const html = render(<ReportRequirement code="SYN_A" name="Synthetic Course A" />);
    expect(html).toContain('aria-label="Report a problem with Synthetic Course A"');
  });
});
