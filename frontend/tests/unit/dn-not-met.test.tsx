/**
 * Task 3: Degree Navigator says "not fulfilled" where CoursePilot says met.
 * While that is unresolved nothing - the requirement, its group, the degree
 * summary or the plan - reads as complete or on track. Fabricated data only.
 */
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { dnNotMet } from "@/features/degree/degree-navigator";
import { DegreeSummary, DnNotMetNotice } from "@/features/degree/degree-view";
import { leafTally, matchesFilter, presentationFor } from "@/features/degree/requirement-tree";
import { DN_NOT_MET_MESSAGE } from "@/lib/academic/vocabulary";
import type { DegreeAudit, RequirementResult } from "@/types/degree";
import type { DnProgress, DnRequirementRow } from "@/types/degree-audit";

const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;|&apos;/g, "'").replace(/\s+/g, " ");

const node = (code: string, children: RequirementResult[] = []): RequirementResult => ({
  requirement_code: code, requirement_name: `Invented ${code}`, requirement_type: children.length ? "all_of" : "course",
  status: "satisfied", needed_count: null, satisfied_count: 1, needed_credits: null, satisfied_credits: "0",
  needed_distinct_categories: null, distinct_categories: 0, allocated_courses: [], eligible_not_allocated: [],
  children, reason: "Met.", source_prose: null, curation_status: null,
} as unknown as RequirementResult);

const row = (over: Partial<DnRequirementRow>): DnRequirementRow => ({
  id: "r1", kind: "requirement", row_index: 0, parent_row_index: null, dn_number: 2,
  dn_heading: "Requirement R2 : Invented B [IB]", dn_code: null, title: "Invented B [IB]",
  extracted_icon: "not_fulfilled", extracted_total: 1, extracted_completed: 0, extracted_needs: null, dn_courses: [],
  dn_status: "not_satisfied", planning_status: "not_satisfied", planning_rule: "Degree Navigator shows it not fulfilled.",
  requirement_code: "REQ_B", mapping_review_status: "reviewed", state: "matched", issues: [], excluded: false,
  edited: false, reconciliation: "cp_satisfied_dn_not", cp_status: "satisfied", cp_courses: [],
  needs_review: true, label: DN_NOT_MET_MESSAGE, ...over,
});

const progress = (rows: DnRequirementRow[]): DnProgress => ({
  source: "degree_navigator", label: "From your Degree Navigator audit", imports: [], requirements: rows, nudge: null,
});

describe("Degree Navigator says not met, Coursenav says met", () => {
  const tree = node("ROOT", [node("REQ_A"), node("REQ_B")]);
  const review = new Set(["REQ_B"]);

  it("finds only DN 'not fulfilled' where Coursenav says met - never a member goal", () => {
    const rows = [row({}), row({ id: "g", kind: "goal", requirement_code: "CORE_AH", mapping_role: "member_goal" }),
                  row({ id: "x", requirement_code: "REQ_X", excluded: true })];
    expect(dnNotMet(progress(rows))).toEqual([{ code: "REQ_B", name: "Invented B [IB]" }]);
  });

  it("the requirement and every group above it read Needs review, never Complete", () => {
    expect(presentationFor(tree.children[1]!, review).label).toBe("Needs review");
    expect(presentationFor(tree, review).label).toBe("Needs review");
    expect(presentationFor(tree.children[0]!, review).label).toBe("Complete");
    expect(matchesFilter(tree.children[1]!, "complete", review)).toBe(false);
    expect(leafTally([tree], review)).toMatchObject({ satisfied: 1, needs_review: 1 });
  });

  it("the overall summary is not 'All requirements complete' and the warning is shown", () => {
    const audit = { status: "complete", requirements: [tree], credits_required_min: null,
                    credits_applicable_to_degree: "0", credits_completed: "0", credits_in_progress: "0",
                    credits_excluded: "0", credits_remaining: null } as unknown as DegreeAudit;
    const html = text(renderToStaticMarkup(<DegreeSummary audit={audit} review={review} />));
    expect(html).not.toContain("All requirements complete");
    expect(html).toContain("Needs review");
    const notice = text(renderToStaticMarkup(<DnNotMetNotice items={[{ code: "REQ_B", name: "Invented B" }]} />));
    expect(notice).toContain("Degree Navigator says this isn't met — confirm with your advisor.");
  });
});
