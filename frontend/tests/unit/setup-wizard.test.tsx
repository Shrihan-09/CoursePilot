/**
 * Prompt 10a first-run setup wizard. Each test pins a rule from the brief:
 *   - a new user is routed to the wizard; a returning user is not;
 *   - a resumed wizard opens at the first unfinished step;
 *   - skipping every optional step still builds a plan, labelled "estimated";
 *   - upload cards reflect the import status and open the review inline;
 *   - answers saved in the wizard appear in Plan -> Preferences;
 *   - choices are native, labelled inputs; the progress text says "Step N of 8".
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { FileText } from "lucide-react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { PreferencesForm } from "@/features/planning/preferences-form";
import { Chip, ChoiceCard } from "@/features/setup/choices";
import { RecordCard, levelLine } from "@/features/setup/steps";
import {
  SETUP_STEPS,
  STEP_KEYS,
  asProfileInput,
  firstTermChoices,
  homeFor,
  importCardState,
  planSourceLabel,
  programSupportLabel,
  resumeStep,
  stepCounter,
  stepProgress,
  withStepDone,
} from "@/lib/setup";
import type { Profile } from "@/types/onboarding";
import type { PlanningProfileInput, PlanningProfileResponse } from "@/types/personalization";

const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/\s+/g, " ");
const profile = { program: { program_key: "sas-198-bs" } } as unknown as Profile;

describe("routing", () => {
  it("sends a user who hasn't finished setup to the wizard, and a returning user Home", () => {
    expect(homeFor({ onboarding_complete: false })).toBe("/onboarding");
    expect(homeFor({ onboarding_complete: true })).toBe("/home");
    // API unreachable: never trap anyone in the wizard.
    expect(homeFor(null)).toBe("/home");
  });
});

describe("resume", () => {
  it("opens at the first unfinished step", () => {
    expect(resumeStep({ profile: null, setup: undefined })).toBe("welcome");
    expect(resumeStep({ profile, setup: { steps: STEP_KEYS, completed_steps: ["welcome", "about"] } })).toBe("record");
    // A gap: the first step NOT done wins, even if later ones are.
    expect(resumeStep({ profile, setup: { steps: STEP_KEYS, completed_steps: ["welcome", "about", "record", "week"] } }))
      .toBe("intensity");
    expect(resumeStep({ profile, setup: { steps: STEP_KEYS, completed_steps: [...STEP_KEYS] } })).toBe("build");
    // A record but no saved progress (set up before the wizard existed): About you.
    expect(resumeStep({ profile, setup: undefined })).toBe("welcome");
    expect(resumeStep({ profile, setup: { steps: STEP_KEYS, completed_steps: ["welcome"] } })).toBe("about");
  });

  it("saves the finished steps once each, in wizard order", () => {
    expect(withStepDone(["record", "welcome"], "about")).toEqual(["welcome", "about", "record"]);
    expect(withStepDone(["welcome"], "welcome")).toEqual(["welcome"]);
  });

  it("shows Step N of 8 and a matching bar", () => {
    expect(SETUP_STEPS).toHaveLength(8);
    expect(stepCounter("record")).toBe("Step 3 of 8");
    expect(stepProgress("build")).toBe(100);
    expect(stepProgress("welcome")).toBe(13);
  });
});

describe("skipping", () => {
  it("every step except About you can be skipped", () => {
    const skippable = SETUP_STEPS.filter((s) => s.skippable).map((s) => s.key);
    expect(skippable).toEqual(["record", "intensity", "week", "schedule", "musts"]);
  });

  it("with no Degree Navigator audit the plan is labelled estimated", () => {
    expect(planSourceLabel(undefined)).toMatch(/^Estimated/);
    expect(planSourceLabel({ source: "coursepilot_estimate" })).toBe("Estimated — import your audit for accuracy");
    expect(planSourceLabel({ source: "degree_navigator" })).toBe("From your Degree Navigator audit");
  });
});

describe("uploads", () => {
  it("each card's state comes from the API's import states", () => {
    expect(importCardState([])).toBe("not_added");
    expect(importCardState(["discarded", "expired", "failed"])).toBe("not_added");
    expect(importCardState(["confirmed"])).toBe("confirmed");
    expect(importCardState(["confirmed", "extracted"])).toBe("needs_review");
    expect(importCardState(["processing"])).toBe("processing");
    expect(importCardState([], true)).toBe("processing");
  });

  it("a card shows its state in words and opens the review inline", () => {
    const html = renderToStaticMarkup(
      <RecordCard id="dn" icon={FileText} title="Degree Navigator audit (PDF)" what="Rutgers' view."
        how={["One", "In Chrome, choose Print → Save as PDF (not Microsoft Print to PDF).", "Three"]}
        state="needs_review" open onToggle={() => {}} onFile={() => {}} error={null}>
        <p>REVIEW-INLINE</p>
      </RecordCard>);
    expect(text(html)).toContain("Needs review");
    expect(html).toContain('data-state="needs_review"');
    expect(html).toContain("REVIEW-INLINE");
    expect(html).toContain('aria-expanded="true"');
    const closed = text(renderToStaticMarkup(
      <RecordCard id="dn" icon={FileText} title="DN" what="x" how={["a", "Save as PDF, not Microsoft Print to PDF", "c"]}
        state="not_added" open={false} onToggle={() => {}} onFile={() => {}} error={null}><p>HIDDEN</p></RecordCard>));
    expect(closed).toContain("Not added");
    expect(closed).toContain("Microsoft Print to PDF");
    expect(closed).not.toContain("HIDDEN");
  });
});

describe("labels", () => {
  const v = (support_status: string) => ({ catalog_year: "2025", support_status }) as never;
  it("a program's label comes from its published support status", () => {
    expect(programSupportLabel({ versions: [v("supported")] })).toBe("Beta supported");
    expect(programSupportLabel({ versions: [v("pending_review")] })).toBe("Preview — not yet reviewed");
    expect(programSupportLabel({ versions: [v("unavailable")] })).toBe("Coming soon");
  });

  it("a workload level reads as one line of ranges, with no easy/hard wording", () => {
    expect(levelLine({ credit_floor: "15.0", credit_max: "18.0", major_min: 2, major_max: 3 })).toBe("15-18 credits · 2-3 major courses");
    expect(levelLine({ credit_floor: "12", credit_max: "14", major_min: 1, major_max: 1 })).toBe("12-14 credits · 1 major course");
    for (const s of SETUP_STEPS) expect(`${s.title} ${s.why}`).not.toMatch(/\b(easy|hard)\b/i);
  });

  it("first semesters offered are Fall/Spring up to this Fall", () => {
    const t = firstTermChoices(new Date(2026, 10, 7));
    expect(t[0]).toBe("20269");
    expect(t).toContain("20251");
    expect(t.every((c) => /^\d{4}[19]$/.test(c))).toBe(true);
  });
});

describe("accessibility", () => {
  it("choices are native radios/checkboxes inside their labels", () => {
    const card = renderToStaticMarkup(<ChoiceCard name="lvl" checked onSelect={() => {}} title="Balanced" detail="15-17 credits" />);
    expect(card).toMatch(/<label[^>]*>.*<input type="radio"[^>]*name="lvl"[^>]*checked=""/);
    expect(card).toContain("cp-glow-selected");
    const chip = renderToStaticMarkup(<Chip type="checkbox" name="fri" checked={false} onChange={() => {}} label="No Fridays" />);
    expect(chip).toMatch(/<label[^>]*>.*<input type="checkbox"[^>]*name="fri"/);
    expect(text(chip)).toContain("No Fridays");
  });
});

describe("preferences", () => {
  const meta = {
    profile: null, defaults: {} as PlanningProfileInput, disclaimer: "d",
    paces: [{ value: "balanced", label: "On time", description: "" }],
    workload_levels: [{ value: "semi_rigorous", label: "Semi-rigorous", description: "", credit_floor: "16", credit_max: "19", budget: "", major_min: 2, major_max: 3 }],
    greek_life: [{ value: "none", label: "None", hours: 0 }, { value: "active", label: "Active member", hours: 4 }],
    clubs: [{ value: "none", label: "None", hours: 0 }],
    filler_preferences: [{ value: "flexible", label: "Flexible or asynchronous", description: "" }],
  } as unknown as PlanningProfileResponse;
  // What the wizard wrote, as the server returns it (with its read-only fields).
  const saved = {
    pace: "balanced", target_credits_per_term: 15, max_credits_per_term: 18, max_courses_per_term: 6, max_terms: null,
    include_summer: false, include_winter: false,
    schedule: { earliest_start: "10:00", latest_end: null, avoid_days: ["F"], preferred_campuses: ["LIV"], blocked_times: [] },
    additional_goals: [], workload_level: "semi_rigorous", filler_preference: "flexible",
    pins: [{ course: "01:640:152", term_code: "20271" }],
    commitments: { job_hours_per_week: 15, greek_life: "active", clubs: "none", athletics_hours_per_week: 0, research_hours_per_week: 0, recruiting_hours_per_week: 0 },
    honors: true, version: 3, confirmed_at: "2026-11-07T00:00:00Z", source: "student_form", effective_max_terms: 8,
  } as unknown as PlanningProfileInput;

  it("a saved profile is sent back without the server's read-only fields", () => {
    const input = asProfileInput(saved) as unknown as Record<string, unknown>;
    for (const k of ["version", "confirmed_at", "source", "effective_max_terms"]) expect(input).not.toHaveProperty(k);
    expect(input.workload_level).toBe("semi_rigorous");
  });

  it("answers saved in the wizard appear in Plan -> Preferences, grouped into the wizard's sections", () => {
    const html = renderToStaticMarkup(
      <QueryClientProvider client={new QueryClient()}>
        <PreferencesForm meta={meta} initial={saved} busy={false} submitLabel="Review" onSubmit={() => {}} />
      </QueryClientProvider>);
    const t = text(html);
    for (const section of ["About you", "Intensity", "Your week", "Schedule shape", "Musts", "More"]) expect(t).toContain(section);
    // Only the first section starts open: not one wall of fields.
    expect(html.match(/<details[^>]*open=""/g)).toHaveLength(1);
    expect(html).toMatch(/<input[^>]*name="workload_level"[^>]*checked=""|<input[^>]*checked=""[^>]*name="workload_level"/);
    expect(t).toContain("About 19 hours a week"); // job 15 + active Greek life 4
    expect(html).toMatch(/<option value="15" selected="">/);
    expect(t).toContain("01:640:152");
    expect(html).toMatch(/value="10:00"/);
  });
});
