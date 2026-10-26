/**
 * Phase 7.4: the Degree Navigator import on the Degree page - import step,
 * staged review table, reconciliation view, "estimated" vs "from your Degree
 * Navigator audit" labels, the degree-block picker, and DN notes on plan
 * recommendations. Fabricated data only (subject 999).
 */
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import {
  BlockPicker,
  DnNodeSource,
  DnReviewBody,
  DnSourceNotice,
  ExportInstructions,
  dnLookup,
} from "@/features/degree/degree-navigator";
import { SemesterCard } from "@/features/plan/plan-result";
import type { DnImportDetail, DnProgress, DnRequirementRow } from "@/types/degree-audit";
import type { PlannedCourse } from "@/types/plan";

const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;|&apos;/g, "'").replace(/&quot;/g, "\"").replace(/\s+/g, " ");
const DISAGREE = "Degree Navigator and CoursePilot disagree; confirm with your advisor";

const req = (over: Partial<DnRequirementRow>): DnRequirementRow => ({
  id: "r1", kind: "requirement", row_index: 0, parent_row_index: null, dn_number: 1,
  dn_heading: "Requirement R1 : Invented Studies [IS]", dn_code: null, title: "Invented Studies [IS]",
  extracted_icon: "fulfilled", extracted_total: 1, extracted_completed: 1,
  extracted_needs: "This requirement is (will be) satisfied.", dn_courses: [],
  dn_status: "satisfied", planning_status: "satisfied", planning_rule: "Degree Navigator shows it fulfilled.",
  requirement_code: "REQ_IS", mapping_review_status: "reviewed", state: "matched", issues: [],
  excluded: false, edited: false, reconciliation: "agrees", cp_status: "satisfied", cp_courses: [],
  needs_review: false, label: null, ...over,
});

const detail = (over: Partial<DnImportDetail>): DnImportDetail => ({
  id: "i1", state: "extracted", version: 3, block_index: 0, block_count: 1,
  dn_subject: "Invented Program (NB)", dn_version_label: "Fall 2098", as_of: "2098-10-01T13:00:00Z",
  as_of_source: "print_header",
  freshness: { as_of: "2098-10-01T13:00:00Z", as_of_source: "print_header", age_days: 2, stale: false, reasons: [],
               message: "From your Degree Navigator audit, printed Oct 01, 2098 (2 days ago)." },
  track_check: "matches_track", track_message: "This audit is for your track (Invented Program).",
  mapping_key: "sas-999-ba", warnings: [], failure_code: null, failure_message: null,
  created_at: "2098-10-03T00:00:00Z", expires_at: "2098-10-10T00:00:00Z", confirmed_at: null, superseded_at: null,
  requirements: [
    req({}),
    req({ id: "r2", dn_number: 2, dn_heading: "Requirement R2 : Invented Lab [IL]", requirement_code: "REQ_IL",
          reconciliation: "dn_satisfied_cp_not", cp_status: "unsatisfied", needs_review: true, label: DISAGREE }),
    req({ id: "r3", dn_number: 3, dn_heading: "Requirement R3 : Unmapped Electives [UE]", requirement_code: null,
          state: "unmapped", reconciliation: "unmapped", dn_status: "not_satisfied", planning_status: "unknown" }),
    req({ id: "r4", dn_number: 4, dn_heading: "Requirement R4 : Flipped [FL]", requirement_code: "REQ_FL",
          state: "needs_review", reconciliation: "agrees",
          issues: [{ code: "STATUS_TEXT_MISMATCH", field: null, severity: "needs_review",
                     message: "The status icon disagrees with the requirement's \"Needs\" line." }] }),
  ],
  courses: [{ id: "c9", list_kind: "unused", requirement_number: null, raw_text: "TR:999:ABC ( 3, Transfer, T )",
              course_code: null, credits: null, term: null, grade: null, kind: "unclassified", state: "needs_review",
              issues: [], excluded: false, history_row_id: null }],
  history: [
    { id: "h1", course_code: "01:999:101", term_code: "20989", status: "completed", grade: "A", credits_earned: "3.0",
      state: "matched", issues: [], excluded: false, resolution: null, outcome: null },
    { id: "h2", course_code: "01:999:102", term_code: "20989", status: "completed", grade: "B", credits_earned: "3.0",
      state: "conflict", issues: [{ code: "CONFLICTS_WITH_RECORD", field: null, severity: "conflict",
      message: "Your record already has this course in this term with different values." }],
      excluded: false, resolution: null, outcome: null },
  ],
  history_version: 2, history_outcome: null,
  readiness: { ready: false, blocking: 3, requirement_counts: {}, course_counts: {}, history_counts: {}, track_blocks: false },
  ...over,
});

const noop = () => {};
const review = (d: DnImportDetail) =>
  renderToStaticMarkup(<DnReviewBody detail={d} busy={false} error={null} onResolve={noop} onConfirm={noop} onDiscard={noop} />);

describe("import step", () => {
  it("tells the student how to export from DN with Chrome's own Save as PDF", () => {
    const t = text(renderToStaticMarkup(<ExportInstructions />));
    expect(t).toContain("Save as PDF");
    expect(t).toContain("not Microsoft Print to PDF");
    expect(t).toMatch(/never asks for your NetID/);
    expect(t).toMatch(/read, then deleted/);
  });
});

describe("staged review table and reconciliation view", () => {
  const t = text(review(detail({})));

  it("lists every DN requirement with its status and the planning rule", () => {
    expect(t).toContain("Requirement R1 : Invented Studies [IS]");
    expect(t).toContain("Degree Navigator: Fulfilled");
    expect(t).toContain("Planning uses: Satisfied. Degree Navigator shows it fulfilled.");
  });

  it("labels each reconciliation and shows every disagreement as needs review", () => {
    expect(t).toContain("Agrees with CoursePilot");
    expect(t).toContain("Needs review: CoursePilot doesn't count it yet");
    expect(t).toContain(DISAGREE);
    expect(t).toContain("Unmapped - shown, not used");
  });

  it("asks the student to resolve unreadable rows and history conflicts", () => {
    expect(t).toContain("The status icon disagrees with the requirement's \"Needs\" line.");
    expect(t).toContain("Set status as Degree Navigator shows it");
    expect(t).toContain("Keep my record");
    expect(t).toContain("Use Degree Navigator's");
    expect(t).toContain("TR:999:ABC ( 3, Transfer, T )");          // raw text kept
    expect(t).toContain("3 row(s) need your review");
  });

  it("cannot be confirmed until ready, and says why when the track does not match", () => {
    expect(review(detail({}))).toMatch(/<button[^>]*disabled[^>]*>[^<]*<svg[^>]*>.*?Confirm/);
    const mismatch = text(review(detail({
      track_check: "mismatch",
      track_message: "This audit is for Invented Minor (NB), your CoursePilot track is Invented Program.",
      readiness: { ready: false, blocking: 0, requirement_counts: {}, course_counts: {}, history_counts: {}, track_blocks: true },
    })));
    expect(mismatch).toContain("your CoursePilot track is Invented Program");
    expect(mismatch).toContain("this audit is for a different program");
  });

  it("shows what confirmation did, and no actions afterwards", () => {
    const done = text(review(detail({
      state: "confirmed",
      history_outcome: { inserted: 1, replaced: 0, skipped: 1, excluded: 0 },
    })));
    expect(done).toContain("Added 1, replaced 0, already recorded 1, excluded 0.");
    expect(done).toContain("Confirmed");
    expect(done).not.toContain("Keep my record");
  });
});

describe("estimated vs from your Degree Navigator audit", () => {
  const estimate: DnProgress = {
    source: "coursepilot_estimate", label: "Estimated - import your Degree Navigator audit for accuracy",
    imports: [], requirements: [], nudge: "Import your Degree Navigator audit for accuracy.",
  };
  const fromDn: DnProgress = {
    source: "degree_navigator", label: "From your Degree Navigator audit",
    imports: [detail({ state: "confirmed" })], requirements: [req({}), detail({}).requirements[1]!],
    nudge: null, disagreement_label: DISAGREE,
  };

  it("says the status is estimated when nothing is imported", () => {
    const t = text(renderToStaticMarkup(<DnSourceNotice progress={estimate} onImport={noop} />));
    expect(t).toContain("Requirement status is estimated");
    expect(t).toContain("Estimated - import your Degree Navigator audit for accuracy");
  });

  it("names the audit, its age and the disagreements once imported", () => {
    const t = text(renderToStaticMarkup(<DnSourceNotice progress={fromDn} onImport={noop} />));
    expect(t).toContain("From your Degree Navigator audit");
    expect(t).toContain("printed Oct 01, 2098 (2 days ago)");
    expect(t).toContain(`1 requirement: ${DISAGREE}.`);
  });

  it("nudges a re-import when the audit is stale", () => {
    const stale = { ...fromDn, imports: [detail({ state: "confirmed", freshness: { ...detail({}).freshness!, stale: true,
      reasons: ["predates_final_grades"], message: "Your Degree Navigator audit may be out of date." } })] };
    expect(text(renderToStaticMarkup(<DnSourceNotice progress={stale} onImport={noop} />))).toContain("Re-import");
  });

  it("labels each requirement by the source that decided it", () => {
    const lookup = dnLookup(fromDn);
    const dn = text(renderToStaticMarkup(<DnNodeSource row={lookup("REQ_IL")} hasImport />));
    expect(dn).toContain("From your Degree Navigator audit:");
    expect(dn).toContain(DISAGREE);
    expect(text(renderToStaticMarkup(<DnNodeSource row={lookup("REQ_OTHER")} hasImport />)))
      .toContain("Not in your Degree Navigator audit - CoursePilot estimate.");
    expect(text(renderToStaticMarkup(<DnNodeSource row={undefined} hasImport={false} />)))
      .toContain("Estimated by CoursePilot.");
  });
});

describe("degree-block picker (03a #4)", () => {
  const blocks = [
    { id: "a", dn_subject: "Invented Program (NB)", block_index: 0, block_count: 2, track_check: "matches_track" as const },
    { id: "b", dn_subject: "Invented Minor (NB)", block_index: 1, block_count: 2, track_check: "mismatch" as const },
  ];

  it("offers every degree block of the PDF and marks the one that is not the student's track", () => {
    const t = text(renderToStaticMarkup(<BlockPicker blocks={blocks} value="a" onChange={noop} />));
    expect(t).toContain("This PDF holds 2 degree audits");
    expect(t).toContain("Invented Program (NB) (block 1 of 2)");
    expect(t).toContain("Invented Minor (NB) (block 2 of 2) - not your track");
  });

  it("is not shown for a single-degree PDF", () => {
    expect(renderToStaticMarkup(<BlockPicker blocks={[blocks[0]!]} value="a" onChange={noop} />)).toBe("");
  });
});

describe("plan recommendations carry DN notes (03a #5)", () => {
  it("prints the recommendation's NEEDS REVIEW note", () => {
    const course: PlannedCourse = {
      course: "01:999:301", title: "Invented Seminar", credits: "3", term_code: "20991", reasons: [],
      requirements: [{ requirement_code: "REQ_IL", requirement_name: "Invented Lab", requirement_system: "major", provisional: true }],
      prerequisite: { state: "satisfied_by_history", rules_term: "20991", raw_text: null, canonical_text: null, combination: null,
                      corequisite_text: null, conditional_on: [], unknown_reasons: [] },
      offering: { kind: "confirmed_in_term", target_term: "20991", observed_terms: ["20991"], same_season_terms: [],
                  most_recent_term: "20991", rules_term: "20991", target_term_coverage: null },
      corequisite_group: [], unlocks: [], interchangeable_alternatives: 0,
      notes: [`NEEDS REVIEW - Invented Lab: ${DISAGREE}. Planning uses Degree Navigator's status: Degree Navigator shows it fulfilled.`],
    };
    const t = text(renderToStaticMarkup(
      <SemesterCard term={{ term_code: "20991", label: "Spring 2099", courses: [course], credits: "3" }} scheduleHref={null} />));
    expect(t).toContain(`NEEDS REVIEW - Invented Lab: ${DISAGREE}`);
  });
});
