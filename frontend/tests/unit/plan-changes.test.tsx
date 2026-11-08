/**
 * Prompt 11 Part A: the Options sheet, the change preview, "Your choices",
 * course states, locks and undo on the plan board. Fabricated data.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ToastProvider } from "@/components/ui/overlay";
import { ChangePreviewDialog, CourseOptionsSheet, gateText, type PlanChanges, UndoBar, YourChoicesCard } from "@/features/plan/plan-changes";
import { type BoardActions, PlanView, SemesterCard } from "@/features/plan/plan-result";
import type { Choices, CourseOptions, PlannedCourse, PlanPreview, PlanTerm, SavedPlan } from "@/types/plan";

const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;|&apos;/g, "'").replace(/&quot;/g, '"').replace(/\s+/g, " ");
const EASY_HARD = /\b(easy|easier|hard|harder|difficult)\b/i;

const changes = (preview: PlanPreview | null = null, extra: Partial<PlanChanges> = {}): PlanChanges => ({
  request: () => {}, preview, previewing: false, confirm: () => {}, confirming: false, cancel: () => {},
  undo: () => {}, undoing: false, error: null, ...extra,
});

const course = (code: string, role: string, title = "COURSE"): PlannedCourse => ({
  course: code, title, credits: "3", term_code: "20271", reasons: [], requirements: [], role,
  prerequisite: { state: "satisfied_by_history", conditional_on: [], unknown_reasons: [] },
  offering: { kind: "confirmed_in_term", target_term: "20271", observed_terms: [], same_season_terms: [] },
} as unknown as PlannedCourse);

const term: PlanTerm = {
  term_code: "20271", label: "Spring 2027", credits: "9", free_elective_credits: "0",
  courses: [course("01:640:152", "anchor", "CALCULUS II"), course("01:190:207", "filler", "GREEK MYTHOLOGY"),
            course("01:198:211", "major", "COMPUTER ARCHITECTURE")],
} as unknown as PlanTerm;

const actions = (c = changes()): BoardActions => ({
  changes: c, lockedTerms: [], onOptions: () => {},
  states: new Map([
    ["01:640:152@20271", { course: "01:640:152", term_code: "20271", state: "locked", locked_reason: "anchor" }],
    ["01:190:207@20271", { course: "01:190:207", term_code: "20271", state: "user_selected", locked_reason: "user_selected" }],
    ["01:198:211@20271", { course: "01:198:211", term_code: "20271", state: "planner_selected", locked_reason: null }],
  ]),
});

const preview: PlanPreview = {
  preview_id: "p1", base_revision: 3, valid: true, applied: false, expires_at: "2026-11-08T20:00:00Z", errors: [],
  change: { kind: "swap", course: "01:810:135", term_code: "20271", replacement: "01:920:101" },
  diff: {
    summary: "01:810:135 out of Spring 2027; 01:920:101 into Spring 2027; 1 other course move; graduation unchanged",
    big: true, graduation_before: "20291", graduation_after: "20291", last_term_before: "20281", last_term_after: "20281",
    requirements_lost: [], requirements_gained: ["Social Analysis"], new_issues: [],
    forced_moves: [{ course: "01:190:207", title: "GREEK MYTHOLOGY", from_term: "20279", to_term: "20281",
                     reason: "moved to keep its requirements, prerequisites and credit limits" }],
    terms: [{ term_code: "20271", label: "Spring 2027", added: ["01:920:101"], removed: ["01:810:135"],
              before: [{ course: "01:810:135", title: "INTRO TO MUSIC", credits: "3", state: "planner_selected" }],
              after: [{ course: "01:920:101", title: "INTRO TO SOCIOLOGY", credits: "3", state: "proposed_change" }],
              credits_before: "16", credits_after: "16", workload_before: "21", workload_after: "20.5", budget: "24" }],
  },
};

function withClient(node: React.ReactNode, seed: (c: QueryClient) => void = () => {}) {
  const client = new QueryClient();
  seed(client);
  return renderToStaticMarkup(<QueryClientProvider client={client}><ToastProvider>{node}</ToastProvider></QueryClientProvider>);
}

describe("the change preview", () => {
  it("shows before/after, every other course that moves, and asks to confirm", () => {
    const t = text(renderToStaticMarkup(<ChangePreviewDialog changes={changes(preview)} />));
    expect(t).toContain("Preview your change");
    expect(t).toContain("Nothing is saved until you confirm");
    expect(t).toContain("Removed: 01:810:135 INTRO TO MUSIC");
    expect(t).toContain("Added: 01:920:101 INTRO TO SOCIOLOGY");
    expect(t).toContain("16 → 16 credits · workload 21 → 20.5 of 24");
    expect(t).toContain("Also changes");
    expect(t).toContain("01:190:207 GREEK MYTHOLOGY: Fall 2027");
    expect(t).toContain("This change affects more than one course");
    expect(t).toContain("Now completed: Social Analysis");
    expect(t).toMatch(/Not now.*Confirm/);
    expect(t).not.toMatch(EASY_HARD);
  });

  it("a refused change says why, in words, with no confirm", () => {
    const refused = { ...preview, valid: false, diff: null, preview_id: "",
      errors: ["anchor_cannot_be_swapped: 01:640:152 is a required anchor (required retake); it can only move to another valid term."] };
    const t = text(renderToStaticMarkup(<ChangePreviewDialog changes={changes(refused)} />));
    expect(t).toContain("This change can't be made");
    expect(t).toContain("01:640:152 is a required anchor");
    expect(t).not.toContain("anchor_cannot_be_swapped");
    expect(t).not.toContain("Confirm");
  });

  it("renders nothing without a preview", () => {
    expect(renderToStaticMarkup(<ChangePreviewDialog changes={changes()} />)).toBe("");
  });
});

describe("the board: states, Options, locks", () => {
  it("each course shows its state and an Options button; each semester can be locked or lightened", () => {
    const t = text(renderToStaticMarkup(<SemesterCard term={term} scheduleHref={null} actions={actions()} />));
    expect(t).toContain("Anchor");
    expect(t).toContain("Your choice");
    expect(t.match(/Options/g)?.length).toBe(3);
    expect(t).toContain("Lock semester");
    expect(t).toContain("Lighten");
    const locked = text(renderToStaticMarkup(
      <SemesterCard term={term} scheduleHref={null} actions={{ ...actions(), lockedTerms: ["20271"] }} />));
    expect(locked).toContain("Unlock semester");
  });

  it("a read-only board has no actions", () => {
    const t = text(renderToStaticMarkup(<SemesterCard term={term} scheduleHref={null} />));
    expect(t).not.toContain("Options");
    expect(t).not.toContain("Lock semester");
  });

  it("choices the plan could not keep are said, never silently replaced; undo names the last change", () => {
    const plan = {
      id: "p", created_at: "2026-11-08T00:00:00Z", updated_at: "2026-11-08T00:00:00Z", start_term: "20271",
      constraints: { max_credits_per_term: "19", max_courses_per_term: 6 }, freshness: { state: "current", reasons: [] },
      blocked_courses: [], last_change: "Swap 01:810:135 for 01:190:207 in Spring 2027",
      needs_reselection: [{ course: "01:190:207", term_code: "20271", source: "selection",
                            message: "01:190:207 cannot be placed in Spring 2027 as pinned: you have already completed it." }],
      result: { target: {}, status: "partial", terms: [], issues: [], requirements: [], assumptions: [], observations: [],
                metadata: { planning_engine_version: "x" } },
    } as unknown as SavedPlan;
    const t = text(renderToStaticMarkup(
      <ToastProvider><PlanView plan={plan} onRegenerate={() => {}} regenerating={false} actions={actions()} /></ToastProvider>));
    expect(t).toContain("Some of your choices no longer fit");
    expect(t).toContain("didn't pick anything else for you");
    expect(t).toContain("you have already completed it");
    expect(t).toContain("Last change: Swap 01:810:135 for 01:190:207 in Spring 2027");
    expect(t).toContain("Undo last change");
    expect(text(renderToStaticMarkup(<UndoBar plan={{ ...plan, last_change: null }} changes={changes()} />))).toBe("");
  });
});

describe("the Options sheet", () => {
  const options: CourseOptions = {
    course: "01:810:135", term_code: "20271", title: "INTRO TO MUSIC", state: "planner_selected", role: "filler",
    locked: false, locked_reason: null, requirements: ["Arts and the Humanities"], move_terms: ["20279", "20281"],
    can_remove: true, not_available: [{ course: "01:750:204", reason: "prerequisites_not_met" }],
    groups: [
      { kind: "same_requirement", requirement_code: "CORE_AH", requirement_name: "Arts and the Humanities", options: [{
        course: "01:190:207", title: "GREEK MYTHOLOGY", credits: "3", requirement_codes: ["CORE_AH"],
        requirements: ["Arts and the Humanities"], offering: "confirmed", offering_term: "20271", workload_weight: "4",
        contact_hours: "3", delivery: ["async"], fills_multiple: false, group: "same_requirement",
        impact: "Already in your plan for Fall 2027 - it moves here and 01:810:135 takes a later semester.",
        similar_to: "Similar to 01:198:111, where you did well: same Core area, async" }] },
      { kind: "different_requirement", requirement_code: "CORE_SCL", requirement_name: "Social Analysis", options: [{
        course: "01:920:101", title: "INTRO TO SOCIOLOGY", credits: "3", requirement_codes: ["CORE_SCL"],
        requirements: ["Social Analysis"], offering: "historically offered", offering_term: "20271",
        workload_weight: "4", contact_hours: "3", delivery: ["in_person"], fills_multiple: false,
        group: "different_requirement", similar_to: null,
        impact: "Fills Social Analysis now; you'd still need Arts and the Humanities later." }] },
    ],
  };

  it("groups same-requirement options and a different requirement instead, with what each means", () => {
    const t = text(withClient(
      <CourseOptionsSheet target={{ course: "01:810:135", term: "20271" }} onClose={() => {}} changes={changes()} />,
      (c) => c.setQueryData(["course-options", "01:810:135", "20271"], options)));
    expect(t).toContain("Same requirement: Arts and the Humanities");
    expect(t).toContain("A different requirement instead: Social Analysis");
    expect(t).toContain("Confirmed for this semester");
    expect(t).toContain("Historically offered this season");
    expect(t).toContain("has an asynchronous online section · workload 4 · 3 contact hrs/week");
    expect(t).toContain("Similar to 01:198:111, where you did well");
    expect(t).toContain("you'd still need Arts and the Humanities later");
    expect(t).toContain("Lock to Spring 2027");
    expect(t).toContain("Remove from Spring 2027");
    expect(t).toContain("Not available this semester (1)");
    expect(t).toContain("01:750:204 : prerequisites not met by then");
    expect(t).not.toMatch(EASY_HARD);
  });

  it("an anchor is locked with its reason and offers only a move", () => {
    const anchor = { ...options, course: "01:640:152", locked: true, locked_reason: "anchor", state: "locked" as const,
                     groups: [], can_remove: false };
    const t = text(withClient(
      <CourseOptionsSheet target={{ course: "01:640:152", term: "20271" }} onClose={() => {}} changes={changes()} />,
      (c) => c.setQueryData(["course-options", "01:640:152", "20271"], anchor)));
    expect(t).toContain("Required here");
    expect(t).toContain("it has no swap");
    expect(t).toContain("Move to another semester");
    expect(t).not.toContain("Same requirement");
    expect(t).not.toContain("Remove from");
  });

  it("gate codes read as words", () => {
    expect(gateText("honors_only")).toBe("open only to honors programs");
    expect(gateText("something_new")).toBe("something new");
  });
});

describe("Your choices for the next semester", () => {
  it("asks about flexible slots and choose-one requirements; anchors are already set", () => {
    const choices: Choices = { term_code: "20271", questions: [
      { kind: "already_set", term_code: "20271", prompt: "Already set: CALCULUS II (required retake)", course: "01:640:152",
        title: "CALCULUS II", groups: [], requirement_code: null, alternatives: [], recommended: [], reason: null },
      { kind: "slot", term_code: "20271", course: "01:810:135", title: "INTRO TO MUSIC", requirement_code: null,
        prompt: "You have room for one Core course. Which do you want to knock out first?", alternatives: [], recommended: [],
        reason: null, groups: [{ kind: "same_requirement", requirement_code: "CORE_AH", requirement_name: "Arts and the Humanities",
          options: [] }] },
      { kind: "alternative", term_code: "20271", prompt: "Science Requirement (B.S.): which one?", course: null, title: null,
        groups: [], requirement_code: "CS_BS_SCIENCE", recommended: ["PHYS_A"], reason: "Recommended Physics: soonest.",
        alternatives: [{ code: "PHYS_A", name: "Physics 203, 204, 205, 206", estimated_terms: 2 },
                       { code: "CHEM_A", name: "Chemistry 159, 160, 171", estimated_terms: 3 }] },
    ] };
    const t = text(withClient(<YourChoicesCard changes={changes()} />, (c) => c.setQueryData(["plan-choices"], choices)));
    expect(t).toContain("Your choices for Spring 2027");
    expect(t).toContain("Already set: CALCULUS II (required retake)");
    expect(t).toContain("Which do you want to knock out first?");
    expect(t).toContain("Let CoursePilot choose");
    expect(t).toContain("Physics 203, 204, 205, 206 (recommended) · about 2 semesters");
    expect(t).toContain("Chemistry 159, 160, 171 · about 3 semesters");
    expect(t).toContain("Skip for now");
    expect(t).toContain("Want a specific course? Ask the assistant");
    expect(t).toContain("Add a specific course");
  });
});
