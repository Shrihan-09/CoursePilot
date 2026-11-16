// UI polish + Home/rings/profile walkthrough (feature/ui-polish-home).
// Synthetic only: no student DB, model or real record is touched. An isolated
// Next mirror (temp dir, AUTH_MODE=dev, port 3102) talks to an in-process fake
// API (port 18013) serving a FABRICATED student. Screenshots + results go to
// <out-dir>, which must be outside the repo and OneDrive.
//
//   run: node tests/visual/ui-polish.mjs <out-dir> [before|after]
//
// "before" only captures screenshots (the pre-change UI). "after" also asserts:
// no horizontal scroll at 360px, GPA hidden by default and per-device reveal,
// GPA absent from console output, Next-up action, styled error / not-found.
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { cp, mkdir, mkdtemp, open, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { spawn, execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import AxeBuilder from "@axe-core/playwright";
import { chromium } from "@playwright/test";

const source = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const out = path.resolve(process.argv[2] ?? "");
const mode = process.argv[3] ?? "after";
if (!process.argv[2] || out.toLowerCase().includes("onedrive") || out.startsWith(source)) throw new Error("Output must be outside repo/OneDrive");
await mkdir(out, { recursive: true });
const mirror = await mkdtemp(path.join(tmpdir(), "coursepilot-ui-polish-"));
await cp(source, mirror, { recursive: true, filter: (file) => !path.relative(source, file).split(path.sep).some((p) => p === "node_modules" || p === ".next" || p.startsWith(".env")) });
await symlink(path.join(source, "node_modules"), path.join(mirror, "node_modules"), "junction");

/* ------------------------------------------------------------ fixtures */
// FABRICATED. The GPA is a sentinel so a leak is easy to detect.
const GPA = "3.137";
const TERM = "20271";
const course = (code, title, system, name, role = "major", delivery = ["in_person"]) => ({
  course: code, title, credits: "4", term_code: TERM, reasons: ["satisfies_requirement"], role,
  requirements: system ? [{ requirement_code: name.toUpperCase().replace(/\W+/g, "_"), requirement_name: name, requirement_system: system, provisional: false }] : [],
  prerequisite: { state: "satisfied_by_history", rules_term: TERM, raw_text: null, canonical_text: null, combination: null, corequisite_text: null, conditional_on: [], unknown_reasons: [] },
  offering: { kind: "confirmed_in_term", target_term: TERM, observed_terms: ["20261", TERM], same_season_terms: ["20261"], most_recent_term: TERM, rules_term: TERM, target_term_coverage: "complete" },
  corequisite_group: [], unlocks: [], interchangeable_alternatives: 0,
  workload: { weight: "9", credits: "4", contact_hours: "4", has_lab: false, level: 300, technical: "technical", components: {}, delivery, basis_term: TERM },
  notes: [],
});
const spring = [
  course("01:198:213", "software methodology", "major", "Computer Science core", "anchor"),
  course("01:198:336", "principles of information and data management", "major", "Computer Science electives"),
  course("01:640:250", "intro linear algebra", "major", "Mathematics requirement"),
  course("01:640:373", "numer anlys & comput", "core", "Quantitative and Formal Reasoning", "multi_requirement", ["online"]),
  course("01:730:105", "current moral & social issues", null, "", "filler", ["async"]),
];
const fall = [course("01:198:344", "design and analysis of computer algorithms", "major", "Computer Science core"), course("01:198:440", "intro to artificial intelligence", "major", "Computer Science electives")].map((c) => ({ ...c, term_code: "20279" }));
const fit = "Why this fits you: Semi-rigorous + rushing + job 10 h/week → 18 credits, 3 major-related courses (your range: 2-3), async filler, workload 46 of 40.";
const plan = {
  id: "synthetic-plan", revision: 1, created_at: "2026-11-09T00:00:00Z", updated_at: "2026-11-09T12:00:00Z", start_term: TERM,
  freshness: { state: "current", reasons: [] }, constraints: { max_credits_per_term: "18", max_courses_per_term: 6, max_terms: 8, include_summer: false, include_winter: false, pace: "balanced", workload_level: "semi_rigorous" },
  edits: { locks: [], selections: [], avoid: [], locked_terms: [], term_caps: [], alternative_selections: {} },
  course_states: [], needs_reselection: [], last_change: null, blocked_courses: [],
  result: {
    target: { program_key: "rutgers-nb-198-bs", program_name: "Computer Science", program_code: "198", degree_type: "BS", school_code: "01", catalog_year: "2025-2026", support_status: "supported", curation_status: "reviewed", is_current_program: true },
    status: "covers_all_requirements", issues: [], requirements: [], assumptions: [], observations: [],
    metadata: { planning_engine_version: "synthetic", audit_engine_version: "synthetic", start_term: TERM, constraints: {}, constraints_source: "planning_profile", academic_fingerprint: "x", rules_fingerprint: "x", offering_dataset: [] },
    graduation_target: { term_code: "20289", basis: "your stated graduation semester", entry_term: "20259", semesters: 8 },
    degree_credits: { required: "120", completed: "52", in_progress: "14", planned_courses: "48", planned_free_electives: "6", total: "120", met: true, source_title: "SAS degree requirements", source_url: "https://example.invalid/sas", source_quote: "120 credits", retrieved_at: "2026-10-01T00:00:00Z" },
    terms: [
      { term_code: TERM, label: "Spring 2027", credits: "20", free_elective_credits: "0", courses: spring,
        workload: { level: "semi_rigorous", budget: "40", total: "46", credit_floor: "12", credit_max: "18", contact_hours: "20", notes: [], base_budget: "48", commitment_hours: 18, major_related: 3, major_min: 2, major_max: 3, fit, shortfalls: [] } },
      { term_code: "20279", label: "Fall 2027", credits: "8", free_elective_credits: "6", courses: fall,
        workload: { level: "semi_rigorous", budget: "48", total: "18", credit_floor: "12", credit_max: "18", contact_hours: "8", notes: [], major_related: 2, fit: "Why this fits you: Semi-rigorous → 14 credits, 2 major-related courses (your range: 2-3), workload 18 of 48.", shortfalls: [] } },
    ],
  },
};
const program = { program_key: "rutgers-nb-198-bs", program_name: "Computer Science", program_code: "198", school_code: "01", degree_type: "BS", variant: "", catalog_year: "2025-2026", lifecycle_state: "published", publication_basis: null, support_status: "pending_review",
  warnings: [{ code: "ENCODING_NOT_VERIFIED", severity: "warning", message: "This program's encoding has not yet been verified against the official requirements." }, { code: "RULE_NOT_EVALUABLE", severity: "info", message: "1 program rule can't be evaluated automatically." }] };
const profile = { program, onboarding_completed_at: "2026-09-01T00:00:00Z", transcript_reported_gpa: { value: GPA, source: "student_uploaded_unofficial_transcript", as_of: "2026-09-01T00:00:00Z" }, declaration_status: "declared", first_term: "20259", class_standing: "sophomore", home_school_code: "01", class_year: 2029, math_placement: null };
const req = (code, name, status, system = "major", extra = {}) => ({ requirement_code: code, requirement_name: name, requirement_type: "course_set", status, needed_count: 2, satisfied_count: status === "satisfied" ? 2 : 1, needed_credits: null, satisfied_credits: "0", needed_distinct_categories: null, distinct_categories: 0, allocated_courses: [], eligible_not_allocated: [], children: [], reason: `Synthetic: ${status}.`, source_prose: null, curation_status: "reviewed", system, ...extra });
const audit = { program_name: "Computer Science", program_code: "198", degree_type: "BS", catalog_year: "2025-2026", status: "in_progress", credits_completed: "52", credits_applicable_to_degree: "34", credits_excluded: "0", credits_in_progress: "14", credits_required_min: "67", credits_remaining: "33",
  requirements: [req("CS_CORE", "Computer Science core", "partially_satisfied"), req("CS_ELECTIVES", "Computer Science electives", "unsatisfied"), req("MATH", "Mathematics requirement", "satisfied"), req("CORE_QQ", "Quantitative and Formal Reasoning", "indeterminate", "core")],
  rules: [], allocation: [], sharing_policy: "", findings: [], excluded_courses: [], grade_evaluations: [], unallocated_courses: [], disclaimers: [] };
const contribution = (n, d, budget) => ({ units: { numerator: n, denominator: d }, percent: budget ? ((n / d / budget) * 100).toFixed(2) : null, exact_percent: null });
const metric = (scope, label, budget, done, current, unknown) => ({ scope, label, availability: "available", denominator_units: budget, denominator_definition: "Synthetic obligation units.",
  completed: contribution(done, 1, budget), projected_current: contribution(current, 1, budget), projected_plan: null, remaining: contribution(budget - done, 1, budget), unknown_obligations: unknown,
  percentage_kind: unknown ? "confirmed_lower_bound" : "exact", status: "in_progress", drill_down: `/profile#${scope}`, requirement_codes: [], requirement_results: [], allocations: [], explanation: "Synthetic." });
const progress = { schema_version: "1", metric_policy_version: "modeled-obligations/1", display_percent_decimal_places: 2, coverage: "partial", graduation_eligibility: "not_assessed", coverage_limitations: ["Synthetic fixture."], projection_state: "absent",
  metrics: [metric("modeled_overall", "Modeled overall", 30, 12, 15, 2), metric("primary_program", "Computer Science", 18, 8, 10, 0), metric("school_core", "SAS Core", 12, 4, 5, 2)], gates: [], findings: [] };
const schedule = { term_code: TERM, term_label: "Spring 2027", source_url: "https://nbregistrar.rutgers.edu/undergrad/s27prereg.htm", source_basis: "owner-provided screenshot of the page", recorded_on: "2026-11-08",
  tiers: [[105, "2026-11-09T09:00"], [90, "2026-11-10T09:00"], [75, "2026-11-11T09:00"], [60, "2026-11-12T09:00"], [40, "2026-11-13T09:00"], [25, "2026-11-16T09:00"], [1, "2026-11-17T09:00"], [0, "2026-11-18T09:00"]].map(([min, at]) => ({ min_degree_credits: min, label: `${min}+ degree credits`, opens_at: `${at}:00-05:00` })),
  all_students_opens_at: "2026-11-19T09:00:00-05:00", nightly_closure: { start: "02:00", end: "06:00" } };
const footer = "CoursePilot is a planning aid; your advisor and Degree Navigator are authoritative.";

/* ------------------------------------------------------------ fake API */
const state = { meStatus: 200, slowPlan: 0 };
const server = createServer(async (rq, res) => {
  const url = new URL(rq.url, "http://localhost"); const route = url.pathname.replace(/^\/api\/v1/, "");
  for await (const _ of rq) { /* drain */ }
  const send = (value, code = 200) => { res.writeHead(code, { "content-type": "application/json" }); res.end(JSON.stringify(value)); };
  if (route === "/me") return state.meStatus === 200 ? send({ sign_in_method: "dev", institution: { status: "verified" }, has_academic_profile: true, onboarding_complete: true }) : send({ detail: "boom" }, state.meStatus);
  if (route === "/student/onboarding") return send({ state: "complete", profile: { ...profile, transcript_reported_gpa: mode === "after" ? null : profile.transcript_reported_gpa }, history_counts: { completed: 14, in_progress: 4, planned: 0 }, open_imports: [], next_steps: [], setup: { steps: [], completed_steps: [] } });
  if (route === "/student/profile") return send(profile);
  if (route === "/student/audit") return send(audit);
  if (route === "/student/progress") return send(progress);
  if (route === "/student/planning-readiness") return send({ state: "ready" });
  if (route === "/student/plans/current") { if (state.slowPlan) await new Promise((r) => setTimeout(r, state.slowPlan)); return send({ plan }); }
  if (route === "/student/plans/context") return send({ earliest_recorded_term: "20259", latest_recorded_term: "20269", current_term: "20269", current_term_label: "Fall 2026", earliest_start_term: TERM, earliest_start_label: "Spring 2027", start_options: [{ term_code: TERM, label: "Spring 2027" }], loaded_terms: [], default_constraints: plan.constraints, constraints_source: "planning_profile" });
  if (route === "/student/plans/current/choices") return send({ term_code: TERM, questions: [] });
  if (route === "/student/plans/personalization") return send({ use_grades_for_suggestions: false });
  if (route === "/student/grades-reminder") return send({ show: false });
  if (route === "/student/transcripts") return send({ imports: [{ import_id: "t1", state: "confirmed", created_at: "2026-09-01T14:00:00Z", confirmed_at: "2026-09-01T14:05:00Z", transcript_reported_gpa: GPA }] });
  if (route === "/degree-audit-imports") return send({ imports: [] });
  if (route === "/degree-audit-imports/progress") return send({ source: "coursepilot_estimate", label: "Estimated by CoursePilot", imports: [], requirements: [], nudge: null });
  if (route === "/student/schedule-imports") return send({ imports: [{ id: "s1", state: "confirmed", expires_at: "2026-12-01T00:00:00Z", created_at: "2026-11-02T10:00:00Z" }] });
  if (route === "/student/registrations") return send({ registrations: [] });
  if (route === `/registration-schedules/${TERM}`) return send(schedule);
  if (route === "/student/planning-profile") return send({ profile: { workload_level: "semi_rigorous", pace: "balanced", commitments: [] }, state: "confirmed" });
  if (route === "/student/assistant") return send({ enabled: true, user_key: "ui-polish-synthetic", conversation_id: "c1", remaining_today: 50, daily_limit: 50, history: [], suggestions: [], conversations: [], footer });
  if (route === "/programs") return send({ programs: [] });
  return send({ detail: "not in fixture" }, 404);
});
await new Promise((r) => server.listen(18013, "127.0.0.1", r));
const log = await open(path.join(out, "frontend-server.log"), "w");
const child = spawn(process.execPath, [path.join(mirror, "node_modules/next/dist/bin/next"), "dev", "-p", "3102"], { cwd: mirror, env: { ...process.env, AUTH_MODE: "dev", COURSEPILOT_API_URL: "http://127.0.0.1:18013/api/v1", NEXT_TELEMETRY_DISABLED: "1" }, windowsHide: true, stdio: ["ignore", log.fd, log.fd] });

const BASE = "http://localhost:3102";
const checks = [], failures = [], consoleLines = [], axeFindings = [];
const check = (ok, what) => { (ok ? checks : failures).push(what); if (!ok) console.log(`CHECK FAILED: ${what}`); };
let browser;
async function signIn(context) {
  const page = await context.newPage();
  page.on("console", (m) => consoleLines.push(m.text()));
  page.on("pageerror", (e) => consoleLines.push(`pageerror: ${e.message}`));
  await page.goto(`${BASE}/signin`); await page.fill("#subject", "ui-polish-synthetic");
  await page.locator('input[name="returnTo"]').evaluate((el) => { el.value = "/home"; });
  await page.click('button[type="submit"]'); await page.waitForURL("**/home");
  return page;
}
const settle = (page) => page.waitForLoadState("networkidle").then(() => page.waitForTimeout(900));
const overflow = (page) => page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1);
async function shot(page, name, full = true) { await settle(page); await page.screenshot({ path: path.join(out, `${mode}-${name}.png`), fullPage: full }); }

try {
  for (let i = 0; i < 120; i++) { try { if ((await fetch(`${BASE}/signin`)).ok) break; } catch {} await new Promise((r) => setTimeout(r, 1000)); }
  browser = await chromium.launch({ channel: "chrome" });
  for (const scheme of ["light", "dark"]) {
    const context = await browser.newContext({ colorScheme: scheme, viewport: { width: 1440, height: 1000 } });
    const page = await signIn(context);
    for (const [vp, size] of [["360", { width: 360, height: 780 }], ["desktop", { width: 1440, height: 1000 }]]) {
      await page.setViewportSize(size);
      for (const [name, url] of [["home", "/home"], ["plan", "/plan"], ["degree", "/degree"], ["profile", "/profile"], ["not-found", "/this-page-does-not-exist"]]) {
        await page.goto(`${BASE}${url}`); await shot(page, `${scheme}-${vp}-${name}`);
        if (mode === "after" && vp === "360") check(!(await overflow(page)), `${scheme} ${name}: no horizontal scroll at 360px`);
        if (mode === "after" && ["home", "plan", "degree", "profile"].includes(name)) {
          const axe = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
          const contrast = axe.violations.filter((v) => v.id === "color-contrast");
          axeFindings.push({ page: `${scheme}-${vp}-${name}`, violations: axe.violations.map((v) => ({ id: v.id, nodes: v.nodes.length, sample: v.nodes[0]?.target?.join(" ") })) });
          check(contrast.length === 0, `${scheme} ${vp} ${name}: WCAG AA colour contrast (${contrast.map((v) => v.nodes.map((n) => n.target.join(" ")).join(", ")).join("; ")})`);
        }
        if (mode === "after" && (name === "home" || name === "profile")) check(!(await page.locator("body").innerText()).includes(GPA), `${scheme} ${vp} ${name}: GPA hidden by default`);
      }
      // A course card on its own.
      await page.goto(`${BASE}/plan`); await settle(page);
      const card = page.getByTestId("plan-course").first();
      if (await card.count()) await card.screenshot({ path: path.join(out, `${mode}-${scheme}-${vp}-course-card.png`) });
      // Error page: the API fails behind the signed-in layout.
      state.meStatus = 500; await page.goto(`${BASE}/home`); await shot(page, `${scheme}-${vp}-error`, false); state.meStatus = 200;
      if (mode === "after") {
        const body = await page.locator("body").innerText();
        check(!/Internal Server Error/i.test(body), `${scheme} ${vp}: no bare Internal Server Error`);
      }
    }
    // Loading skeleton on Plan (desktop).
    state.slowPlan = 4000; await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto(`${BASE}/plan`); await page.waitForTimeout(1500);
    await page.screenshot({ path: path.join(out, `${mode}-${scheme}-desktop-plan-loading.png`) }); state.slowPlan = 0;
    if (mode === "after") {
      await page.goto(`${BASE}/home`); await settle(page);
      check(await page.getByTestId("next-up").count() === 1, `${scheme}: Home has one Next-up card`);
      check(await page.getByTestId("progress-rings").count() === 1, `${scheme}: Home shows rings`);
      await page.goto(`${BASE}/profile`); await settle(page);
      await page.getByRole("button", { name: /show gpa/i }).click();
      check((await page.locator("body").innerText()).includes(GPA), `${scheme}: GPA revealed after toggle`);
      await shot(page, `${scheme}-desktop-profile-gpa-shown`);
      await page.reload(); await settle(page);
      check((await page.locator("body").innerText()).includes(GPA), `${scheme}: reveal persists on this device`);
      const fresh = await browser.newContext({ colorScheme: scheme, viewport: { width: 1440, height: 1000 } });
      const other = await signIn(fresh); await other.goto(`${BASE}/profile`); await settle(other);
      check(!(await other.locator("body").innerText()).includes(GPA), `${scheme}: another device stays hidden`);
      await fresh.close();
      await page.goto(`${BASE}/home`); await settle(page);
      check(!(await page.locator("body").innerText()).includes(GPA), `${scheme}: Home never shows GPA, even after reveal`);
    }
    await context.close();
  }
  if (mode === "after") check(!consoleLines.some((l) => l.includes(GPA)), "GPA never appears in browser console output");
  await writeFile(path.join(out, `${mode}-results.json`), JSON.stringify({ checks, failures, axeFindings, mirror }, null, 2));
  console.log(`${mode}: ${checks.length} checks passed, ${failures.length} failed; ${out}`);
  if (failures.length) process.exitCode = 1;
} finally {
  await browser?.close();
  if (process.platform === "win32") { try { execFileSync("taskkill", ["/PID", String(child.pid), "/T", "/F"], { windowsHide: true, stdio: "ignore" }); } catch {} } else child.kill("SIGTERM");
  server.closeAllConnections(); server.close(); await log.close();
}
