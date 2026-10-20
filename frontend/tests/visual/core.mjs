// Phase 7.2/7.2.1 visual + functional QA: Home, Degree, What-if, Personalize, Plan, Schedule.
// Not a CI test; needs the local stack running (see qa.mjs for the commands).
//
//   run: node tests/visual/core.mjs <out-dir>
//
// Drives the INSTALLED Chrome. Every student is FABRICATED: a dev sign-in
// subject `qa72-*`, a program choice and a handful of course entries made
// through the same-origin API proxy - no transcript, no real person.
// Writes screenshots + findings.json to <out-dir>; nothing goes in the repo.

import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

import AxeBuilder from "@axe-core/playwright";
import { chromium } from "@playwright/test";

const [OUT] = process.argv.slice(2);
if (!OUT) throw new Error("usage: core.mjs <out-dir>");
const BASE = "http://localhost:3000";
const VIEWPORTS = {
  narrow: { width: 320, height: 640 },
  mobile: { width: 390, height: 844 },
  tablet: { width: 820, height: 1180 },
  laptop: { width: 1280, height: 800 },
  desktop: { width: 1600, height: 1000 },
};
const findings = [];
const assertions = [];
await mkdir(OUT, { recursive: true });
const browser = await chromium.launch({ channel: "chrome" });

function expect(name, ok, detail = "") {
  assertions.push({ name, ok: Boolean(ok), detail });
  if (!ok) console.log(`ASSERTION FAILED: ${name} ${detail}`);
}

async function check(page, name) {
  const overflow = await page.evaluate(() =>
    document.documentElement.scrollWidth > document.documentElement.clientWidth + 1);
  const small = await page.evaluate(() => [...document.querySelectorAll(
    "a[href], button:not([disabled]), input:not([type=hidden]), select, summary, [role=tab], [role=radio]")]
    .filter((el) => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
      if ((el.type === "checkbox" || el.type === "radio") && el.closest("label")) return false;
      if (r.width <= 1 || r.height <= 1 || s.position === "absolute" && s.clip !== "auto") return false;
      return r.width > 0 && r.height > 0 && s.visibility !== "hidden" && (r.height < 32 || r.width < 24); })
    .map((el) => `${el.tagName.toLowerCase()}:${(el.textContent || el.getAttribute("aria-label") || "").trim().slice(0, 30)}:${Math.round(el.getBoundingClientRect().height)}`));
  const axe = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  findings.push({ name, overflow, smallTargets: small,
    axe: axe.violations.map((v) => ({ id: v.id, impact: v.impact, nodes: v.nodes.length,
                                      sample: v.nodes[0]?.target?.join(" ") })) });
}

async function shot(page, name, { full = false, audit = true } = {}) {
  // Let entrance animations (cp-rise, 360 ms + stagger) settle before auditing.
  await page.waitForTimeout(700);
  await page.screenshot({ path: path.join(OUT, `${name}.png`), fullPage: full });
  if (audit) await check(page, name);
}

async function api(page, method, url, body) {
  return page.evaluate(async ([m, u, b]) => {
    const r = await fetch(`/api/cp${u}`, { method: m, headers: b ? { "content-type": "application/json" } : {},
                                           body: b ? JSON.stringify(b) : undefined });
    let json = null;
    try { json = await r.json(); } catch { /* 204 */ }
    return { status: r.status, json };
  }, [method, url, body ?? null]);
}

async function signIn(context, subject) {
  await context.clearCookies();
  const page = await context.newPage();
  await page.goto(`${BASE}/signin`);
  await page.fill("#subject", subject);
  await page.click("button[type=submit]");
  await page.waitForURL(/onboarding|home/);
  return page;
}

// Fabricated. Phase 7.2.1: two courses IN PROGRESS in Fall 2026 (a term with
// Rutgers section data), so "My current schedule" has something to confirm.
const HISTORY = [
  ["01:198:111", "20259", "A"], ["01:640:151", "20259", "B+"], ["01:355:101", "20259", "A"],
  ["01:198:112", "20261", "B"], ["01:640:152", "20261", "B"], ["01:198:205", "20261", "B+"],
  ["01:198:211", "20269", null, "in_progress"], ["01:198:206", "20269", null, "in_progress"],
];

async function seed(page, { pending = false, history = true } = {}) {
  const programs = (await api(page, "GET", "/programs")).json.programs;
  const pick = pending
    ? programs.find((p) => p.versions.some((v) => v.support_status === "pending_review"))
    : programs.find((p) => p.program_code === "198" && p.degree_type === "BA");
  const version = pick.versions.find((v) => v.support_status !== "unavailable");
  await api(page, "POST", "/student/profile", { program_key: pick.program_key, catalog_year: version.catalog_year });
  if (history) {
    for (const [course_code, term_code, grade, status = "completed"] of HISTORY) {
      await api(page, "POST", "/student/courses", { course_code, term_code, status, grade, credit_origin: "rutgers" });
    }
  }
  await api(page, "POST", "/student/onboarding/complete");
  return pick;
}

const responsive = async (page, url, name, waitFor, { full = true } = {}) => {
  for (const [vp, size] of Object.entries(VIEWPORTS)) {
    await page.setViewportSize(size);
    await page.goto(`${BASE}${url}`);
    await waitFor(page);
    await shot(page, `${name}-${vp}`, { full, audit: vp !== "desktop" });
  }
  await page.setViewportSize(VIEWPORTS.laptop);
};

for (const scheme of ["light", "dark"]) {
  const stamp = `${scheme}-${Date.now().toString(36)}`;
  const context = await browser.newContext({ colorScheme: scheme, viewport: VIEWPORTS.laptop });

  /* ------------------------------------------------ student with a record */
  const page = await signIn(context, `qa721-${stamp}`);
  await seed(page);

  await responsive(page, "/home", `${scheme}-home`, (p) => p.getByText("Next step").waitFor());
  expect(`${scheme} home suggests personalizing first`, await page.getByText("Personalize your plan").count());

  // Degree
  const degreeReady = (p) => p.getByRole("heading", { name: "Requirements" }).waitFor({ timeout: 30000 });
  await responsive(page, "/degree", `${scheme}-degree`, degreeReady);
  await page.goto(`${BASE}/degree`);
  await degreeReady(page);
  await page.locator("details > summary").first().click();
  await shot(page, `${scheme}-degree-expanded`, { full: true });
  await page.setViewportSize(VIEWPORTS.narrow);
  await shot(page, `${scheme}-degree-expanded-narrow`, { full: true });
  await page.setViewportSize(VIEWPORTS.laptop);
  for (const tab of ["Still needed", "Can't determine", "Complete"]) {
    await page.getByRole("tab", { name: tab }).click();
    await shot(page, `${scheme}-degree-filter-${tab.replace(/\W+/g, "-").toLowerCase()}`, { full: true, audit: tab === "Can't determine" });
  }
  const unknownCount = await page.locator("[data-status=indeterminate], [data-status=not_evaluable]").count();
  expect(`${scheme} degree: no UNKNOWN row is labelled as failed`,
    unknownCount === 0 || !(await page.locator("[data-status=indeterminate] summary, [data-status=not_evaluable] summary").filter({ hasText: /Not met|Failed/ }).count()));

  // What-if
  await page.getByRole("tab", { name: "All" }).click();
  await page.getByRole("tab", { name: "What-if" }).click();
  await page.getByLabel("Program").waitFor();
  await shot(page, `${scheme}-whatif-empty`);
  const options = await page.locator("#" + (await page.getByLabel("Program").getAttribute("id")) + " option").allTextContents();
  const math = options.find((o) => /Mathematics/.test(o)) ?? options[1];
  await page.getByLabel("Program").selectOption({ label: math });
  await page.getByRole("button", { name: "Compare" }).click();
  await page.getByText("What-if scenario:").waitFor({ timeout: 30000 });
  await shot(page, `${scheme}-whatif-result`, { full: true });
  await page.setViewportSize(VIEWPORTS.mobile);
  await shot(page, `${scheme}-whatif-result-mobile`, { full: true });
  await page.setViewportSize(VIEWPORTS.laptop);
  expect(`${scheme} what-if banner says the program is unchanged`, await page.getByText("Your program is not changed").count());
  const profileAfter = (await api(page, "GET", "/student/profile")).json.program.program_code;
  expect(`${scheme} what-if did not change the real program`, profileAfter === "198", profileAfter);

  // Plan: personalize -> review -> confirm -> ready -> build
  await page.goto(`${BASE}/plan`);
  await page.getByRole("heading", { name: "Personalize your plan" }).waitFor();
  await shot(page, `${scheme}-plan-needs-profile`, { full: true });
  expect(`${scheme} no plan can be built before preferences are confirmed`,
    !(await page.getByRole("button", { name: "Build my plan" }).count()));
  const personalizeReady = (p) => p.getByText("How would you like to pace your semesters?").waitFor({ timeout: 30000 });
  await responsive(page, "/plan/personalize", `${scheme}-personalize`, personalizeReady);
  await page.goto(`${BASE}/plan/personalize`);
  await personalizeReady(page);
  expect(`${scheme} Balanced is the default pace`, await page.getByRole("radio", { name: /Balanced/ }).isChecked());
  await page.getByLabel("Aim for (credits)").fill("16");
  await page.getByLabel("Never more than (credits)").fill("18");
  await page.getByLabel("I'd consider winter courses").check();
  await page.getByLabel("No classes before").fill("09:00");
  await page.getByLabel("Kind").selectOption("minor");
  await page.getByLabel("Program", { exact: true }).fill("Mathematics minor");
  await page.getByRole("button", { name: "Add" }).click();
  await shot(page, `${scheme}-personalize-filled`, { full: true });
  await page.getByRole("button", { name: "Review" }).click();
  await page.getByText("Here's how CoursePilot will plan for you").waitFor();
  await shot(page, `${scheme}-personalize-review`, { full: true });
  await page.setViewportSize(VIEWPORTS.narrow);
  await shot(page, `${scheme}-personalize-review-narrow`, { full: true });
  await page.setViewportSize(VIEWPORTS.laptop);
  expect(`${scheme} review says the maximum is never exceeded`, await page.getByText("18 credits, never more").count());
  expect(`${scheme} additional program shown as exploring`, await page.getByText(/Exploring Mathematics minor/).count());
  await page.getByRole("button", { name: "Confirm preferences" }).click();
  await page.getByRole("heading", { name: "Ready to build your plan" }).waitFor({ timeout: 30000 });
  await shot(page, `${scheme}-plan-ready`, { full: true });
  expect(`${scheme} the starting semester is labelled as the plan's, and is a future term`,
    await page.getByLabel("Plan starting semester").count());
  const startOptions = await page.locator("#" + (await page.getByLabel("Plan starting semester").getAttribute("id")) + " option").allTextContents();
  expect(`${scheme} no past semester is offered`, !startOptions.some((o) => /2025|Spring 2026|Fall 2026/.test(o)), startOptions.join(","));
  await page.getByRole("button", { name: "Build my plan" }).click();
  await page.getByText("CoursePilot is building your plan").waitFor();
  await page.waitForTimeout(600);
  await shot(page, `${scheme}-plan-generating`, { audit: true });
  const planReady = (p) => p.getByRole("heading", { name: /semesters? from/ }).waitFor({ timeout: 90000 });
  await planReady(page);
  await shot(page, `${scheme}-plan-result`, { full: true });
  await page.getByText("Why this course, this term").first().click();
  await shot(page, `${scheme}-plan-why`, { full: true });
  await responsive(page, "/plan", `${scheme}-plan`, planReady);
  expect(`${scheme} plan shows a build-schedule handoff`, await page.getByRole("link", { name: /Build schedule for/ }).count());
  const saved = (await api(page, "GET", "/student/plans/current")).json.plan;
  expect(`${scheme} the plan used the confirmed preferences`,
    saved.constraints.pace === "balanced" && Number(saved.constraints.target_credits_per_term) === 16
    && Number(saved.constraints.max_credits_per_term) === 18, JSON.stringify(saved.constraints));
  expect(`${scheme} no semester exceeds the maximum`, saved.result.terms.every((t) => Number(t.credits) <= 18));

  // Planned semester CoursePilot has no sections for -> explicit other term.
  const ctx = (await api(page, "GET", "/student/plans/context")).json;
  const unpublished = saved.result.terms.find((t) => !ctx.loaded_terms.some((l) => l.term_code === t.term_code));
  if (unpublished) {
    await page.goto(`${BASE}/schedule?term=${unpublished.term_code}`);
    await page.getByText(/doesn't have .* sections from Rutgers yet/).waitFor();
    await shot(page, `${scheme}-schedule-unpublished`, { full: true });
    await page.getByRole("button", { name: "Find schedules for " + unpublished.label }).click();
    await page.getByText("Term not published").first().waitFor({ timeout: 30000 });
    await shot(page, `${scheme}-schedule-unpublished-result`, { full: true, audit: false });
    await page.getByRole("button", { name: "Explore another term" }).click();
    const termValue = await page.getByLabel("Term").inputValue();
    expect(`${scheme} exploring another term starts with NO term chosen`, termValue === "", termValue);
    await shot(page, `${scheme}-schedule-custom`, { full: true });
    await page.getByLabel("Term").selectOption("20269");
  } else {
    await page.goto(`${BASE}/schedule?term=${saved.result.terms[0].term_code}`);
  }
  expect(`${scheme} class-time limits start from the preferences`, await page.getByText("Class-time limits start from your planning preferences.").count());
  await page.getByRole("button", { name: /Find schedules/ }).click();
  const schedReady = (p) => p.getByRole("heading", { name: /^Option 1$/ }).or(p.getByText("No valid schedule").first()).first().waitFor({ timeout: 30000 });
  await schedReady(page);
  await shot(page, `${scheme}-schedule-result`, { full: true });
  expect(`${scheme} schedule never claims registration`,
    !(await page.getByText(/you are registered|successfully registered|enrolled you/i).count()));
  expect(`${scheme} schedule never says best`, !(await page.getByText(/\bbest schedule\b/i).count()));
  for (const [vp, size] of Object.entries(VIEWPORTS)) {
    await page.setViewportSize(size);
    await page.waitForTimeout(300);
    await shot(page, `${scheme}-schedule-result-${vp}`, { full: true, audit: vp !== "desktop" });
  }
  await page.setViewportSize(VIEWPORTS.laptop);

  // No valid schedule: a window nothing fits in.
  await page.getByText("Schedule settings").click();
  await page.getByLabel("No classes before").fill("12:00");
  await page.getByLabel("No classes after").fill("13:00");
  await page.getByRole("button", { name: /Find schedules/ }).click();
  await page.getByText("No valid schedule").first().waitFor({ timeout: 30000 });
  await shot(page, `${scheme}-schedule-none`, { full: true });

  // My current schedule: nothing shown until the student confirms a section.
  await page.goto(`${BASE}/schedule`);
  await page.getByRole("tab", { name: "My current schedule" }).waitFor();
  await page.getByText("Choose your sections to see your week.").waitFor({ timeout: 30000 });
  await shot(page, `${scheme}-current-unconfirmed`, { full: true });
  expect(`${scheme} no section is shown before confirmation`, !(await page.getByText("confirmed by you").count()));
  await page.getByRole("button", { name: "Choose my section" }).first().click();
  await page.getByRole("dialog").getByRole("radio").first().waitFor({ timeout: 30000 });
  await shot(page, `${scheme}-current-picker`, { audit: true });
  await page.setViewportSize(VIEWPORTS.narrow);
  await shot(page, `${scheme}-current-picker-narrow`, { audit: true });
  await page.setViewportSize(VIEWPORTS.laptop);
  expect(`${scheme} the picker preselects nothing`, !(await page.getByRole("dialog").getByRole("radio", { checked: true }).count()));
  await page.getByRole("dialog").getByRole("radio").first().check();
  await page.getByRole("button", { name: "Confirm section" }).click();
  await page.getByRole("heading", { name: "My week" }).waitFor({ timeout: 30000 });
  await shot(page, `${scheme}-current-week`, { full: true });
  await page.setViewportSize(VIEWPORTS.mobile);
  await shot(page, `${scheme}-current-week-mobile`, { full: true });
  await page.setViewportSize(VIEWPORTS.laptop);
  expect(`${scheme} the confirmed section is labelled as the student's`, await page.getByText("confirmed by you").count());

  // Settings shows the confirmed preferences, editable without chat.
  await page.goto(`${BASE}/settings`);
  await page.getByRole("heading", { name: "Planning preferences" }).waitFor();
  await shot(page, `${scheme}-settings-preferences`, { full: true });

  // Changing a preference makes the saved plan stale, with the reason.
  const prof = (await api(page, "GET", "/student/planning-profile")).json.profile;
  await api(page, "PUT", "/student/planning-profile", {
    pace: prof.pace, target_credits_per_term: 12, max_credits_per_term: 15, max_courses_per_term: prof.max_courses_per_term,
    max_terms: prof.max_terms, include_summer: prof.include_summer, include_winter: prof.include_winter,
    schedule: prof.schedule, additional_goals: prof.additional_goals });
  await page.goto(`${BASE}/plan`);
  await page.getByText("This plan is out of date").waitFor({ timeout: 30000 });
  await shot(page, `${scheme}-plan-stale`, { full: true });
  expect(`${scheme} stale plan names the cause`, await page.getByText("You changed your planning preferences.").count());
  await page.goto(`${BASE}/home`);
  await page.getByText("Your plan is out of date").first().waitFor();
  await shot(page, `${scheme}-home-stale`, { full: true });

  // Errors: the audit endpoint failing is shown as a system failure.
  await page.route("**/api/cp/student/audit", (r) => r.fulfill({ status: 500, contentType: "application/json",
    body: JSON.stringify({ detail: "Unable to compute a degree audit.", error: { code: "internal_error", message: "x", request_id: "qa-req" } }) }));
  await page.goto(`${BASE}/degree`);
  await page.getByText("Your degree progress couldn't be loaded").waitFor({ timeout: 30000 });
  await shot(page, `${scheme}-degree-error`);
  await page.unroute("**/api/cp/student/audit");
  // Loading skeleton.
  await page.route("**/api/cp/student/audit", async (r) => {
    await new Promise((res) => setTimeout(res, 2500));
    await r.continue().catch(() => {});   // the page may have navigated on
  });
  await page.goto(`${BASE}/degree`);
  await page.waitForTimeout(500);
  await shot(page, `${scheme}-degree-loading`, { audit: false });
  await page.waitForTimeout(2500);
  await page.unroute("**/api/cp/student/audit");

  // Keyboard: tab into the requirement list and open one with Enter.
  await page.goto(`${BASE}/degree`);
  await degreeReady(page);
  await page.locator("details > summary").first().focus();
  await page.keyboard.press("Enter");
  await shot(page, `${scheme}-degree-keyboard`, { audit: false });
  expect(`${scheme} requirement opens from the keyboard`, await page.locator("details[open]").count());

  // Mobile navigation.
  await page.setViewportSize(VIEWPORTS.mobile);
  await page.goto(`${BASE}/plan`);
  await page.getByRole("button", { name: "More" }).click();
  await shot(page, `${scheme}-mobile-more`);
  await page.keyboard.press("Escape");
  await page.setViewportSize(VIEWPORTS.laptop);

  /* ------------------------------------- pending-review program, no record */
  const pending = await signIn(context, `qa721-pend-${stamp}`);
  const pick = await seed(pending, { pending: true, history: false });
  await pending.goto(`${BASE}/degree`);
  await degreeReady(pending);
  await shot(pending, `${scheme}-degree-pending`, { full: true });
  expect(`${scheme} pending program shows the review warning (${pick.program_key})`,
    await pending.getByText("pending human review").count());
  expect(`${scheme} empty record prompts to add courses`, await pending.getByText("No courses on your record yet").count());
  await pending.setViewportSize(VIEWPORTS.narrow);
  await shot(pending, `${scheme}-degree-pending-narrow`, { full: true });
  await pending.setViewportSize(VIEWPORTS.laptop);
  await pending.goto(`${BASE}/schedule`);
  await pending.getByRole("tab", { name: "Plan a semester" }).click();
  await pending.getByRole("tab", { name: "Choose courses" }).waitFor();
  await shot(pending, `${scheme}-schedule-noplan`, { full: true });

  // Sign out: protected pages are closed afterwards.
  await pending.evaluate(async () => { await fetch("/auth/logout", { method: "POST" }); });
  for (const route of ["/degree", "/plan", "/schedule"]) {
    await pending.goto(`${BASE}${route}`);
    await pending.waitForURL(/signin/, { timeout: 15000 });
    expect(`${scheme} ${route} redirects to sign-in after sign-out`, /\/signin/.test(pending.url()));
  }
  await context.close();
}

await writeFile(path.join(OUT, "findings.json"), JSON.stringify({ findings, assertions }, null, 2));
await browser.close();
const issues = findings.filter((f) => f.overflow || f.smallTargets.length || f.axe.length);
console.log(`screens checked: ${findings.length}, with findings: ${issues.length}`);
for (const f of issues) console.log(JSON.stringify(f));
console.log(`assertions: ${assertions.filter((a) => a.ok).length}/${assertions.length} passed`);
for (const a of assertions.filter((x) => !x.ok)) console.log("FAILED", JSON.stringify(a));
