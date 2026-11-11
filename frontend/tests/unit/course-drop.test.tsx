/**
 * A student's drop: a pure drop preview, the per-term "Keep it at N credits"
 * choice, opt-in replacement suggestions, title-first course lines.
 * Fabricated data (a made-up course in a made-up plan).
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ToastProvider } from "@/components/ui/overlay";
import { Proposal } from "@/features/assistant/assistant-chat";
import { ChangePreviewDialog, confirmLabel, type PlanChanges } from "@/features/plan/plan-changes";
import type { ProposalCard } from "@/types/assistant";
import type { PlanDiff, PlanPreview } from "@/types/plan";

const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;|&apos;/g, "'").replace(/&quot;/g, '"').replace(/\s+/g, " ");

const changes = (preview: PlanPreview | null): PlanChanges => ({
  request: () => {}, preview, previewing: false, confirm: () => {}, confirming: false, cancel: () => {},
  undo: () => {}, undoing: false, error: null,
});

const dropDiff: PlanDiff = {
  summary: "Spring 2027: 18 → 15 credits. Writing Across Fields is now unmet. Graduation unchanged.",
  big: true, graduation_before: "20291", graduation_after: "20291", last_term_before: "20289", last_term_after: "20289",
  requirements_lost: ["Writing Across Fields"], requirements_gained: [], new_issues: [], forced_moves: [],
  unmet: [{ code: "CORE_WX", name: "Writing Across Fields" }],
  dropped_course: "01:111:222", dropped_term: "20271",
  terms: [{
    term_code: "20271", label: "Spring 2027", added: [], removed: ["01:111:222"],
    before: [{ course: "01:111:222", title: "Stories of the Sea in Film", credits: "3", state: "planner_selected" }],
    after: [], credits_before: "18", credits_after: "15", workload_before: "24", workload_after: "21", budget: "17.5",
    credit_floor: "16", below_minimum: true,
    warnings: ["15 credits is below your 16-credit minimum for Spring 2027.",
               "Workload 21 is over your 17.5 budget for Spring 2027."],
    notes: [],
  }],
};

const dropPreview: PlanPreview = {
  preview_id: "p-drop", base_revision: 4, valid: true, applied: false, expires_at: "2026-11-11T20:00:00Z", errors: [],
  change: { kind: "remove", course: "01:111:222", term_code: "20271" }, diff: dropDiff,
};

function render(node: React.ReactNode) {
  return renderToStaticMarkup(<QueryClientProvider client={new QueryClient()}><ToastProvider>{node}</ToastProvider></QueryClientProvider>);
}

describe("a drop preview", () => {
  it("is a pure drop: one course out, nothing in, the unmet requirement named", () => {
    const t = text(render(<ChangePreviewDialog changes={changes(dropPreview)} />));
    expect(t).toContain("Spring 2027: 18 → 15 credits. Writing Across Fields is now unmet. Graduation unchanged.");
    expect(t).toContain("Removed: Stories of the Sea in Film 01:111:222");
    expect(t).not.toContain("Added:");
    expect(t).toContain("Now unmet: Writing Across Fields");
    // No other course moves, so no "more than one course" alarm.
    expect(t).not.toContain("This change affects more than one course");
  });

  it("warns below the minimum and over the workload budget", () => {
    const t = text(render(<ChangePreviewDialog changes={changes(dropPreview)} />));
    expect(t).toContain("15 credits is below your 16-credit minimum for Spring 2027.");
    expect(t).toContain("Workload 21 is over your 17.5 budget for Spring 2027.");
  });

  it("confirming keeps the term at its new total; a replacement is only a suggestion", () => {
    const t = text(render(<ChangePreviewDialog changes={changes(dropPreview)} />));
    expect(confirmLabel(dropDiff)).toBe("Keep it at 15 credits for this term");
    expect(t).toMatch(/Not now.*Suggest a replacement.*Keep it at 15 credits for this term/);
    // Nothing is searched or suggested until the student asks.
    expect(t).not.toContain("Suggested replacements");
  });

  it("shows the title first and the code after it, never the code alone", () => {
    const html = render(<ChangePreviewDialog changes={changes(dropPreview)} />);
    expect(html.indexOf("Stories of the Sea in Film")).toBeLessThan(html.indexOf(">01:111:222<"));
    expect(html).toMatch(/class="code text-xs text-fg-muted">01:111:222</);
  });

  it("an ordinary change keeps Confirm and offers no replacement search", () => {
    const swap = { ...dropDiff, dropped_course: null, dropped_term: null,
      terms: dropDiff.terms.map((x) => ({ ...x, below_minimum: false, warnings: [] })) };
    expect(confirmLabel(swap)).toBe("Confirm");
    const t = text(render(<ChangePreviewDialog changes={changes({ ...dropPreview, diff: swap,
      change: { kind: "swap", course: "01:111:222", term_code: "20271", replacement: "01:333:444" } })} />));
    expect(t).not.toContain("Suggest a replacement");
    expect(t).toMatch(/Not now.*Confirm/);
  });
});

describe("an assistant-proposed drop", () => {
  const card = {
    title: "Remove 01:111:222 Stories of the Sea in Film from Spring 2027", change: dropPreview.change,
    preview_id: "p-drop", summary: dropDiff.summary, diff: dropDiff, big: true, reason: "confirm", action_token: "t".repeat(20),
  } as unknown as ProposalCard;

  it("is the same preview, with the same choices", () => {
    const t = text(render(<Proposal p={card} ctx={{ tap: () => {}, tapping: false, send: () => {} }} />));
    expect(t).toContain("Spring 2027: 18 → 15 credits. Writing Across Fields is now unmet.");
    expect(t).toContain("15 credits is below your 16-credit minimum");
    expect(t).toContain("Suggest a replacement");
    expect(t).toContain("Keep it at 15 credits for this term");
    expect(t).not.toContain("Added:");
  });
});
