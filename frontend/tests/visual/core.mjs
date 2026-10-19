// Phase 7.2 visual + functional QA: Home, Degree, What-if, Plan, Schedule.
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
  await page.waitForTimeout(300);
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

const HISTORY = [
  ["01:198:111", "20259", "A"], ["01:640:151", "20259", "B+"], ["01:355:101", "20259", "A"],
  ["01:198:112", "20261", "B"], ["01:640:152", "20261", "B"], ["01:198:205", "20261", "B+"],
];

async function seed(page, { pending = false, history = true } = {}) {
  const programs = (await api(page, "GET", "/programs")).json.programs;
  const pick = pending
    ? programs.find((p) => p.versions.some((v) => v.support_status === "pending_review"))
    : programs.find((p) => p.program_code === "198" && p.degree_type === "BA");
  const version = pick.versions.find((v) => v.support_status !== "unavailable");
  await api(page, "POST", "/student/profile", { program_key: pick.program_key, catalog_year: version.catalog_year });
  if (history) {
    for (const [course_code, term_code, grade] of HISTORY) {
      await api(page, "POST", "/student/courses", { course_code, term_code, status: "completed", grade, credit_origin: "rutgers" });
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
  const page = await signIn(context, `qa72-${stamp}`);
  await seed(page);

  await responsive(page, "/home", `${scheme}-home`, (p) => p.getByText("Next step").waitFor());
  expect(`${scheme} home suggests building a plan`, await page.getByText("Build your semester plan").count());

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

  // Plan: empty, generating, result
  await page.goto(`${BASE}/plan`);
  await page.getByText("No plan yet").waitFor();
  await shot(page, `${scheme}-plan-empty`, { full: true });
  await page.setViewportSize(VIEWPORTS.narrow);
  await shot(page, `${scheme}-plan-empty-narrow`, { full: true });
  await page.setViewportSize(VIEWPORTS.laptop);
  await page.getByRole("button", { name: "Generate plan" }).click();
  await page.getByText("CoursePilot is building your plan").waitFor();
  await page.waitForTimeout(600);
  await shot(page, `${scheme}-plan-generating`, { audit: true });
  const planReady = (p) => p.getByRole("heading", { name: /semesters? from/ }).waitFor({ timeout: 60000 });
  await planReady(page);
  await shot(page, `${scheme}-plan-result`, { full: true });
  await page.getByText("Why this course, this term").first().click();
  await shot(page, `${scheme}-plan-why`, { full: true });
  await responsive(page, "/plan", `${scheme}-plan`, planReady);
  expect(`${scheme} plan shows a build-schedule handoff`, await page.getByRole("link", { name: /Build schedule for/ }).count());

  // Schedule from the plan
  const firstTerm = (await api(page, "GET", "/student/plans/current")).json.plan.result.terms[0];
  await page.goto(`${BASE}/schedule?term=${firstTerm.term_code}`);
  await page.getByRole("button", { name: /Find schedules/ }).waitFor();
  await shot(page, `${scheme}-schedule-start`, { full: true });
  await page.getByRole("button", { name: /Find schedules/ }).click();
  const schedReady = (p) => p.getByRole("heading", { name: /^Option 1$/ }).waitFor({ timeout: 30000 });
  await schedReady(page);
  await shot(page, `${scheme}-schedule-result`, { full: true });
  expect(`${scheme} schedule never claims registration`,
    !(await page.getByText(/you are registered|successfully registered|enrolled you/i).count()));
  expect(`${scheme} schedule states CoursePilot doesn't register`, await page.getByText(/doesn't register you/).count());
  expect(`${scheme} schedule never says best`, !(await page.getByText(/\bbest schedule\b/i).count()));
  for (const [vp, size] of Object.entries(VIEWPORTS)) {
    await page.setViewportSize(size);
    await page.waitForTimeout(300);
    await shot(page, `${scheme}-schedule-result-${vp}`, { full: true, audit: vp !== "desktop" });
  }
  await page.setViewportSize(VIEWPORTS.mobile);
  const tabs = page.getByRole("tablist", { name: "Day" }).getByRole("tab");
  if (await tabs.count() > 1) await tabs.nth(1).click();
  await shot(page, `${scheme}-schedule-mobile-day`, { full: false });
  await page.setViewportSize(VIEWPORTS.laptop);
  const optionButtons = page.getByRole("button", { name: "2", exact: true });
  if (await optionButtons.count()) {
    await optionButtons.first().click();
    await page.getByRole("heading", { name: /^Option 2$/ }).waitFor();
    await shot(page, `${scheme}-schedule-option2`, { full: true, audit: false });
  }

  // No valid schedule: a window nothing fits in.
  await page.getByText("Schedule settings").click();
  await page.getByLabel("No classes before").fill("12:00");
  await page.getByLabel("No classes after").fill("13:00");
  await page.getByRole("button", { name: /Find schedules/ }).click();
  await page.getByText("No valid schedule").first().waitFor({ timeout: 30000 });
  await shot(page, `${scheme}-schedule-none`, { full: true });

  // A plan semester whose term Rutgers hasn't published.
  const plan = (await api(page, "GET", "/student/plans/current")).json.plan;
  const ctx = (await api(page, "GET", "/student/plans/context")).json;
  const unpublished = plan.result.terms.find((t) => !ctx.loaded_terms.some((l) => l.term_code === t.term_code));
  if (unpublished) {
    await page.goto(`${BASE}/schedule?term=${unpublished.term_code}`);
    await page.getByText(/hasn't published/).waitFor();
    await shot(page, `${scheme}-schedule-unpublished`, { full: true });
    await page.getByRole("button", { name: "Find schedules for " + unpublished.label }).click();
    await page.getByText("Term not published").first().waitFor({ timeout: 30000 });
    await shot(page, `${scheme}-schedule-unpublished-result`, { full: true, audit: false });
    await page.getByRole("button", { name: "Explore another term" }).click();
    const termValue = await page.getByLabel("Term").inputValue();
    expect(`${scheme} exploring another term starts with NO term chosen`, termValue === "", termValue);
    await shot(page, `${scheme}-schedule-custom`, { full: true });
  }

  // Stale plan: change the record, then look.
  await api(page, "POST", "/student/courses", { course_code: "01:198:206", term_code: "20261", status: "completed", grade: "B", credit_origin: "rutgers" });
  await page.goto(`${BASE}/plan`);
  await page.getByText("This plan is out of date").waitFor({ timeout: 30000 });
  await shot(page, `${scheme}-plan-stale`, { full: true });
  expect(`${scheme} stale plan names the cause`, await page.getByText("Your academic record changed.").count());
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
  const pending = await signIn(context, `qa72-pend-${stamp}`);
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
