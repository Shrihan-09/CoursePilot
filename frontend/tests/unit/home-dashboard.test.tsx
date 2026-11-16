import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ usePathname: () => "/home", useRouter: () => ({ push: () => {} }) }));

import HomePage from "@/app/(app)/home/page";
import { ProgressRings } from "@/features/home/progress-rings";
import { GpaRow } from "@/features/profile/profile-overview";
import { GPA_VISIBLE_KEY, readGpaVisible, writeGpaVisible } from "@/lib/gpa-visibility";
import {
  degreeRing, graduation, metricRing, nextUp, plannedCourseCount, registrationWindow, wholeCredits,
  type NextUpInput, type RegistrationSchedule,
} from "@/lib/home";
import type { ProgressMetric } from "@/types/progress";
import type { PlanTerm, SavedPlan } from "@/types/plan";

const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&quot;/g, '"').replace(/\s+/g, " ");
const GPA = "3.137";

/* ---------------------------------------------------------------- fixtures */
const course = (code: string, term = "20271") => ({ course: code, title: "data structures", credits: "4", term_code: term, reasons: [], requirements: [] }) as unknown as PlanTerm["courses"][number];
function plan(extra: Partial<SavedPlan["result"]> = {}, freshness: SavedPlan["freshness"]["state"] = "current"): SavedPlan {
  return {
    id: "p", created_at: "", updated_at: "", start_term: "20271", constraints: {} as SavedPlan["constraints"],
    freshness: { state: freshness, reasons: [] }, blocked_courses: [],
    result: {
      status: "covers_all_requirements", issues: [], requirements: [], assumptions: [], observations: [],
      metadata: {} as SavedPlan["result"]["metadata"], target: {} as SavedPlan["result"]["target"],
      graduation_target: { term_code: "20289", basis: "stated", entry_term: null, semesters: null },
      degree_credits: { required: "120", completed: "52", in_progress: "14", planned_courses: "48", planned_free_electives: "6", total: "120", met: true, source_title: "", source_url: "", source_quote: "", retrieved_at: "" },
      terms: [
        { term_code: "20271", label: "Spring 2027", credits: "20", courses: ["01:198:213", "01:198:336", "01:640:250", "01:640:373", "01:730:105"].map((c) => course(c)) },
        { term_code: "20279", label: "Fall 2027", credits: "8", courses: [course("01:198:344", "20279"), course("01:198:440", "20279")] },
      ],
      ...extra,
    },
  };
}
const contribution = (n: number, d = 1) => ({ units: { numerator: n, denominator: d }, percent: null, exact_percent: null });
const metric = (budget: number, done: number, current: number, unknown: number, codes: string[] = []): ProgressMetric => ({
  scope: "primary_program", label: "CS", availability: "available", denominator_units: budget, denominator_definition: "",
  completed: { ...contribution(done), percent: ((done / budget) * 100).toFixed(2) },
  projected_current: { ...contribution(current), percent: ((current / budget) * 100).toFixed(2) },
  projected_plan: null, remaining: contribution(budget - done), unknown_obligations: unknown, percentage_kind: "exact",
  status: "", drill_down: "", requirement_codes: codes, requirement_results: [], allocations: [], explanation: "",
});
const schedule: RegistrationSchedule = {
  term_code: "20271", term_label: "Spring 2027",
  tiers: [[105, "2026-11-09"], [90, "2026-11-10"], [75, "2026-11-11"], [60, "2026-11-12"], [40, "2026-11-13"], [25, "2026-11-16"], [1, "2026-11-17"], [0, "2026-11-18"]]
    .map(([min, d]) => ({ min_degree_credits: min as number, label: `${min}+ degree credits`, opens_at: `${d}T09:00:00-05:00` })),
  all_students_opens_at: "2026-11-19T09:00:00-05:00", closes_at: "2026-11-20T02:00:00-05:00", nightly_closure: {}, source_url: "https://nbregistrar.rutgers.edu/x", source_basis: "",
};

/* ---------------------------------------------------------------- record */
describe("your record", () => {
  it("Planned counts the planned courses in the current plan (not history 'planned' entries)", () => {
    expect(plannedCourseCount(plan())).toBe(7);
    expect(plannedCourseCount(null)).toBe(0);
  });

  it("credits are whole numbers", () => {
    expect(wholeCredits("34.0")).toBe("34");
    expect(wholeCredits(120)).toBe("120");
    expect(wholeCredits("3.5")).toBe("3.5");
    expect(wholeCredits(null)).toBe("-");
  });
});

/* ---------------------------------------------------------------- rings */
describe("ring math (matches the audit / modeled progress)", () => {
  it("outer ring: earned + in-progress degree credits over the degree total", () => {
    const r = degreeRing(plan().result.degree_credits);
    expect(r.done).toBeCloseTo(52 / 120);
    expect(r.inProgress).toBeCloseTo(14 / 120);
    expect(r.review).toBe(0);
    expect(r.summary).toBe("66 / 120 credits");
    expect(degreeRing(null).available).toBe(false);
  });

  it("middle/inner rings: completed, in-progress increment and unknown obligations as a grey segment", () => {
    const r = metricRing("major", "Major requirements", metric(18, 8, 10, 2));
    expect(r.done).toBeCloseTo(8 / 18);
    expect(r.inProgress).toBeCloseTo(2 / 18);
    expect(r.review).toBeCloseTo(2 / 18);
    expect(r.done + r.inProgress + r.review).toBeLessThanOrEqual(1);
  });

  it("uses exact ratios (fractional units) as the engine states them", () => {
    const m = metric(4, 0, 0, 0);
    m.completed = { ...contribution(3, 2), percent: "37.50" };
    m.projected_current = { ...contribution(3, 2), percent: "37.50" };
    expect(metricRing("core", "Core", m).done).toBeCloseTo(1.5 / 4);
  });

  it("needs review (Degree Navigator disagrees) is never drawn as done", () => {
    const r = metricRing("major", "Major requirements", metric(18, 8, 10, 2), true);
    expect(r.done).toBe(0);
    expect(r.review).toBeCloseTo(10 / 18);
    expect(r.summary).toBe("Needs review");
  });

  it("an unavailable metric is not modeled, with no percentage assumed", () => {
    const m = { ...metric(18, 8, 8, 0), availability: "unavailable" as const };
    const r = metricRing("core", "Core / gen-ed", m);
    expect(r.available).toBe(false);
    expect(r.done).toBe(0);
  });

  it("segments never exceed a full ring", () => {
    const r = metricRing("major", "M", metric(4, 4, 5, 3));
    expect(r.done + r.inProgress + r.review).toBeLessThanOrEqual(1 + 1e-9);
  });

  it("rings render with accessible labels and links, and skip motion under reduced motion", () => {
    const rings = [degreeRing(plan().result.degree_credits), metricRing("major", "Major requirements", metric(18, 8, 10, 2)), metricRing("core", "Core / gen-ed", metric(12, 4, 5, 2))];
    const html = renderToStaticMarkup(<ProgressRings rings={rings} hrefs={{ degree: "/degree#a", major: "/degree#b", core: "/degree#c" }} />);
    // Server render (and prefers-reduced-motion) = no animation, arcs drawn at full length.
    expect(html).toContain('data-reduced-motion="true"');
    expect(html).not.toContain("transition");
    expect(html).toContain('aria-label="Degree credits: 52 earned, 14 in progress, of the 120-credit degree total."');
    expect(html).toContain('href="/degree#b"');
    expect(text(html)).toContain("Needs review / unknown");
  });
});

/* ---------------------------------------------------------------- registration + next up */
describe("registration window", () => {
  it("picks the Registrar tier from earned degree credits", () => {
    const w = registrationWindow(schedule, 52, new Date("2026-11-10T12:00:00-05:00"))!;
    expect(w.tier_label).toBe("40+ degree credits");
    expect(w.state).toBe("upcoming");
    expect(registrationWindow(schedule, 52, new Date("2026-11-15T12:00:00-05:00"))!.state).toBe("open");
    expect(registrationWindow(schedule, 52, new Date("2026-11-21T12:00:00-05:00"))!.state).toBe("closed");
    expect(registrationWindow(schedule, 0, new Date("2026-11-10"))!.tier_label).toBe("0+ degree credits");
    expect(registrationWindow(schedule, null, new Date())).toBeNull();
    expect(registrationWindow(null, 52, new Date())).toBeNull();
  });
});

describe("Next up picks the single most important action", () => {
  const base: NextUpInput = { onboardingState: "complete", readiness: "ready" as NextUpInput["readiness"], plan: plan(), choicesPending: 0, hasDnAudit: true, registration: null };
  const open = registrationWindow(schedule, 52, new Date("2026-11-15T12:00:00-05:00"));
  const upcoming = registrationWindow(schedule, 52, new Date("2026-11-10T12:00:00-05:00"));

  it("follows the documented order", () => {
    expect(nextUp({ ...base, onboardingState: "needs_academic_history" }).kind).toBe("setup");
    expect(nextUp({ ...base, readiness: "needs_planning_profile" as NextUpInput["readiness"] }).kind).toBe("personalize");
    expect(nextUp({ ...base, plan: null }).kind).toBe("build_plan");
    expect(nextUp({ ...base, plan: plan({}, "stale") }).kind).toBe("stale_plan");
    expect(nextUp({ ...base, choicesPending: 2, registration: open }).kind).toBe("choices");
    expect(nextUp({ ...base, registration: open, hasDnAudit: false }).kind).toBe("registration");
    expect(nextUp({ ...base, hasDnAudit: false }).kind).toBe("upload_dn");
    expect(nextUp(base).kind).toBe("schedule");
    expect(nextUp({ ...base, plan: plan({ terms: [] }) }).kind).toBe("degree");
  });

  it("states the registration window with its basis and the schedule action", () => {
    const up = nextUp({ ...base, registration: upcoming });
    expect(up.title).toMatch(/^Your Spring 2027 registration opens Fri, Nov 13/);
    expect(up.body).toContain("52 earned degree credits");
    expect(up.body).toContain("40+ degree credits");
    expect(up.cta).toBe("Build Spring 2027 schedule");
    expect(up.href).toBe("/schedule?term=20271");
    expect(nextUp({ ...base, registration: open }).title).toBe("Spring 2027 registration is open for you");
  });

  it("a closed window or another term's schedule is not the next action", () => {
    const closed = registrationWindow(schedule, 52, new Date("2026-11-25T12:00:00-05:00"));
    expect(nextUp({ ...base, registration: closed }).kind).toBe("schedule");
    expect(nextUp({ ...base, registration: { ...open!, term_label: "Fall 2027" } }).kind).toBe("schedule");
  });
});

describe("graduation card", () => {
  it("is on track only when the plan covers everything and nothing needs review", () => {
    expect(graduation(plan(), 0)).toMatchObject({ term: "Fall 2028", onTrack: true });
    expect(graduation(plan(), 1).onTrack).toBe(false);
    expect(graduation(plan({ status: "partial" }), 0).onTrack).toBe(false);
    expect(graduation(plan({}, "stale"), 0).onTrack).toBe(false);
    expect(graduation(null, 0).term).toBeNull();
  });
});

/* ---------------------------------------------------------------- GPA privacy */
describe("GPA hidden by default; reveal is per device", () => {
  afterEach(() => vi.restoreAllMocks());

  it("the GPA row renders masked by default and never logs the value", () => {
    const spies = (["log", "info", "warn", "error", "debug"] as const).map((k) => vi.spyOn(console, k).mockImplementation(() => {}));
    const html = renderToStaticMarkup(<GpaRow value={GPA} />);
    expect(html).not.toContain(GPA);
    expect(html).toContain("Show GPA");
    expect(html).toContain('aria-pressed="false"');
    for (const s of spies) for (const call of s.mock.calls) expect(JSON.stringify(call)).not.toContain(GPA);
  });

  it("the reveal stores only a flag on this device, never the GPA", () => {
    const store = new Map<string, string>();
    const storage = { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => void store.set(k, v), removeItem: (k: string) => void store.delete(k) };
    expect(readGpaVisible(storage)).toBe(false);
    writeGpaVisible(true, storage);
    expect([...store.entries()]).toEqual([[GPA_VISIBLE_KEY, "1"]]);
    expect(readGpaVisible(storage)).toBe(true);
    writeGpaVisible(false, storage);
    expect(store.size).toBe(0);
    expect(readGpaVisible(null)).toBe(false);
  });
});

/* ---------------------------------------------------------------- Home render */
describe("Home dashboard", () => {
  function renderHome() {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
    const p = plan();
    client.setQueryData(["onboarding"], { state: "complete", history_counts: { completed: 14, in_progress: 4, planned: 0 }, open_imports: [], next_steps: [],
      // An older API that still sent the GPA here must not make Home show it.
      profile: { onboarding_completed_at: null, transcript_reported_gpa: { value: GPA, source: "x", as_of: null },
        program: { program_key: "k", program_name: "Computer Science", program_code: "198", school_code: "01", degree_type: "BS", variant: "", catalog_year: "2025-2026", lifecycle_state: "", publication_basis: null, support_status: "pending_review",
          warnings: [{ code: "W1", severity: "warning", message: "This program's encoding has not yet been verified." }, { code: "W2", severity: "info", message: "1 program rule can't be evaluated." }] } } });
    client.setQueryData(["audit"], { credits_applicable_to_degree: "34.0", credits_required_min: "67.0", credits_in_progress: "14.0", requirements: [] });
    client.setQueryData(["plan"], { plan: p });
    client.setQueryData(["readiness"], { state: "ready" });
    client.setQueryData(["plan-choices"], { term_code: "20271", questions: [] });
    client.setQueryData(["dn-imports"], { imports: [] });
    client.setQueryData(["dn-progress"], { source: "coursepilot_estimate", label: "", imports: [], requirements: [], nudge: null });
    client.setQueryData(["modeled-progress"], { metrics: [{ ...metric(18, 8, 10, 0), scope: "primary_program" }, { ...metric(12, 4, 5, 2), scope: "school_core" }] });
    client.setQueryData(["grades-reminder"], { show: false });
    return renderToStaticMarkup(<QueryClientProvider client={client}><HomePage /></QueryClientProvider>);
  }

  it("never shows the GPA, shows Planned from the plan, whole-number credits and one primary action", () => {
    const html = renderHome();
    const t = text(html);
    expect(t).not.toContain(GPA);
    expect(t).not.toMatch(/GPA/);
    expect(t).toMatch(/Planned 7/);
    expect(t).toContain("Degree: 66 / 120 · Major: 34 / 67");
    expect(t).not.toContain("34.0");
    expect(html.match(/data-testid="progress-rings"/g)?.length).toBe(1);
    // The two program banners collapse into one Preview chip (full text one tap away).
    expect(html).toContain('data-testid="preview-chip"');
    expect(html).not.toMatch(/role="status"[^>]*>[^<]*<svg[^>]*>[^]*?encoding has not yet been verified/);
    // No stale copy.
    expect(t).not.toContain("Coming later");
    // "View modeled progress" lives in the Degree card with an explanation.
    expect(t).toContain("View modeled progress");
    expect(t).toContain("Ask the assistant");
    expect(t).toContain("What Core is left?");
  });
});
