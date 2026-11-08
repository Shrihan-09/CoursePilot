// Prompt 11 Part A walkthrough (not a CI test; needs a local stack with
// development sign-in, like academic.mjs). Captures the "Your choices" card,
// the Options sheet, a swap preview and search results for the A3c example
// queries, at laptop width (and the Options sheet / preview at phone width).
//
// Run only against an isolated CI-mode backend with the test DB guard installed,
// DATABASE_URL/DATABASE_URL_SYNC pointing to coursepilot_test, and a frontend
// configured to proxy that backend. Set COURSEPILOT_VISUAL_DATABASE=coursepilot_test.
//   run: node tests/visual/plan-options.mjs <out-dir> <test-frontend-base-url>
//
// A throwaway `qa11a-*` dev subject with a FABRICATED CS B.S. record (no real
// person): CS 111/112/205, Calc I, Expository Writing passed; Calc II failed
// (a retake anchor); CS 211 in progress. Exits 1 on an axe violation, a
// horizontal overflow, or a failed check.

import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

import AxeBuilder from "@axe-core/playwright";
import { chromium } from "@playwright/test";

const [OUT, BASE] = process.argv.slice(2);
if (!OUT || !BASE || !process.env.COURSEPILOT_VISUAL_DATABASE?.endsWith("_test")) {
  throw new Error("Provide an explicit test frontend URL and COURSEPILOT_VISUAL_DATABASE ending in _test; never run this against dev.");
}
await mkdir(OUT, { recursive: true });
const findings = [];
const failures = [];
const LAPTOP = { width: 1280, height: 900 };
const PHONE = { width: 390, height: 844 };
const EASY_HARD = /\b(easy|easier|hard|harder|difficult)\b/i;
const QUERIES = [
  "a different AHo course in Spring 2027, online",
  "another CS elective that's not 14:332",
  "something about AI that counts for my major",
  "a writing course that also fills diversity",
  "Fridays off: what Core courses fit?",
];

const browser = await chromium.launch({ channel: "chrome" });

async function audit(page, name) {
  const overflow = await page.evaluate(() =>
    document.documentElement.scrollWidth > document.documentElement.clientWidth + 1);
  const axe = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  const violations = axe.violations.map((v) => ({ id: v.id, impact: v.impact, nodes: v.nodes.length,
    sample: v.nodes[0]?.target?.join(" ") }));
  findings.push({ name, overflow, axe: violations });
  if (overflow || violations.length) failures.push(name);
}

async function shot(page, name, target = null) {
  await page.waitForTimeout(700);
  if (target) await target.screenshot({ path: path.join(OUT, `${name}.png`) });
  else await page.screenshot({ path: path.join(OUT, `${name}.png`), fullPage: false });
  await audit(page, name);
}
const check = (ok, what) => { findings.push({ check: what, ok }); if (!ok) failures.push(what); };

async function api(page, method, url, body) {
  return page.evaluate(async ([m, u, b]) => {
    const r = await fetch(`/api/cp${u}`, { method: m, headers: b ? { "content-type": "application/json" } : {},
                                           body: b ? JSON.stringify(b) : undefined });
    let json = null;
    try { json = await r.json(); } catch { /* 204 */ }
    return { status: r.status, json };
  }, [method, url, body ?? null]);
}

const HISTORY = [
  ["01:198:111", "20259", "A"], ["01:640:151", "20259", "B+"], ["01:355:101", "20259", "A"],
  ["01:198:112", "20261", "B"], ["01:198:205", "20261", "B+"], ["01:640:152", "20261", "F"],
  ["01:198:206", "20269", "B"],
  ["01:198:211", "20269", null, "in_progress"],
];

async function seed(page) {
  const health = await api(page, "GET", "/health");
  if (health.json?.environment !== "ci") throw new Error("Visual fixture seeding requires a CI-mode test backend.");
  const programs = (await api(page, "GET", "/programs")).json.programs;
  const pick = programs.find((p) => p.program_code === "198" && p.degree_type === "BS");
  const version = pick.versions.find((v) => v.support_status !== "unavailable");
  check((await api(page, "POST", "/student/profile", { program_key: pick.program_key,
    catalog_year: version.catalog_year })).status < 300, "profile created");
  for (const [course_code, term_code, grade, status = "completed"] of HISTORY) {
    const r = await api(page, "POST", "/student/courses", { course_code, term_code, status, grade, credit_origin: "rutgers" });
    check(r.status < 300, `course ${course_code} recorded (${r.status})`);
  }
  await api(page, "POST", "/student/onboarding/complete");
  const prefs = await api(page, "PUT", "/student/planning-profile", {
    workload_level: "semi_rigorous", filler_preference: "flexible", max_courses_per_term: 6, pace: "balanced" });
  check(prefs.status === 200, `planning preferences confirmed (${prefs.status})`);
  const plan = await api(page, "POST", "/student/plans", {});
  check(plan.status === 200, `plan built (${plan.status})`);
  return plan.json;
}

const context = await browser.newContext({ colorScheme: "light", viewport: LAPTOP });
const page = await context.newPage();
await page.goto(`${BASE}/signin`);
await page.fill("#subject", `qa11a-${Date.now().toString(36)}`);
await page.click("button[type=submit]");
await page.waitForURL(/onboarding|home/);
const plan = await seed(page);
const first = plan.result.terms[0];
findings.push({ plan: plan.result.terms.map((t) => [t.term_code, t.courses.map((c) => `${c.course}:${c.role}`)]) });
// A filler (two option groups) - in the first semester if there is one.
const all = plan.result.terms.flatMap((t) => t.courses);
const filler = first.courses.find((c) => c.role === "filler") ?? all.find((c) => c.role === "filler")
  ?? all.find((c) => c.requirements.some((r) => r.requirement_code === "CS_ELECTIVES")) ?? first.courses.at(-1);
check(Boolean(filler), "a course to show options for");

/* -------------------------------------------------- Your choices */
await page.goto(`${BASE}/plan`);
const card = page.getByTestId("your-choices");
await card.waitFor({ timeout: 60000 });
await page.getByText(/You have room for one|which one\?/).first().waitFor({ timeout: 60000 });
await shot(page, "1-your-choices", card);
await shot(page, "1b-plan-page");
check(!EASY_HARD.test(await card.innerText()), "no easy/hard wording in Your choices");

/* -------------------------------------------------- Options sheet */
await page.getByRole("button", { name: `Options for ${filler.course}` }).click();
const sheet = page.getByTestId("options-sheet");
await sheet.waitFor({ timeout: 60000 });
await shot(page, "2-options-sheet");
const sheetText = await sheet.innerText();
check(/Same requirement|A different requirement instead/.test(sheetText), "options are grouped");
check(!EASY_HARD.test(sheetText), "no easy/hard wording in Options");

/* -------------------------------------------------- swap preview */
await sheet.getByRole("button", { name: "Choose" }).first().click();
const preview = page.getByTestId("change-preview");
await preview.waitFor({ timeout: 60000 });
await shot(page, "3-swap-preview");
check(/Nothing is saved until you confirm/.test(await page.locator("dialog[open]").last().innerText()),
  "preview says nothing is saved");
await page.getByRole("button", { name: "Not now" }).click();
await page.keyboard.press("Escape");

/* -------------------------------------------------- phone width */
await page.setViewportSize(PHONE);
await page.goto(`${BASE}/plan`);
await page.getByTestId("your-choices").waitFor({ timeout: 60000 });
findings.push({ phoneWide: await page.evaluate(() => [...document.querySelectorAll("body *")]
  .filter((e) => e.getBoundingClientRect().right > document.documentElement.clientWidth + 1)
  .slice(0, 8).map((e) => `${e.tagName}.${(e.className?.baseVal ?? e.className ?? "").toString().slice(0, 80)}:${Math.round(e.getBoundingClientRect().right)}`)) });
await page.getByRole("button", { name: `Options for ${filler.course}` }).click();
await page.getByTestId("options-sheet").waitFor({ timeout: 60000 });
await shot(page, "4-options-sheet-phone");
await page.keyboard.press("Escape");
await page.setViewportSize(LAPTOP);

/* -------------------------------------------------- A3c search */
await page.goto(`${BASE}/plan`);
const search = page.getByTestId("course-search");
await search.waitFor({ timeout: 60000 });
for (const [i, q] of QUERIES.entries()) {
  await page.fill("#course-search", q);
  await search.getByRole("button", { name: "Search" }).click();
  await search.getByText("Looking for:").waitFor({ timeout: 60000 });
  await page.waitForResponse((r) => r.url().includes("/plans/current/search"), { timeout: 5000 }).catch(() => {});
  await page.waitForTimeout(600);
  const results = await search.getByTestId("option-card").count();
  findings.push({ query: q, results, text: (await search.innerText()).slice(0, 600) });
  check(!EASY_HARD.test(await search.innerText()), `no easy/hard wording for "${q}"`);
  await search.scrollIntoViewIfNeeded();
  await shot(page, `5-search-${i + 1}`, search);
}

await writeFile(path.join(OUT, "findings.json"), JSON.stringify(findings, null, 2));
await browser.close();
console.log(JSON.stringify({ failures }, null, 2));
process.exit(failures.length ? 1 : 0);
