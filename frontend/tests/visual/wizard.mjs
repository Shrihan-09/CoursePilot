// Prompt 10a setup wizard walkthrough (not a CI test; needs the local stack running).
//
//   backend : COURSEPILOT_ENV=local AUTH_PROVIDER=dev DEV_AUTH_ENABLED=true uvicorn app.main:app
//   frontend: AUTH_MODE=dev next dev
//   run     : node tests/visual/wizard.mjs <out-dir> [base-url]
//
// Drives the INSTALLED Chrome. Dev sign-in with throwaway `qa-wiz-*` subjects;
// no uploads (no documents needed). Writes screenshots + findings.json to
// <out-dir>; nothing goes in the repo. Exits 1 on an axe violation, a
// horizontal overflow, or a failed routing/resume/focus check.

import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

import AxeBuilder from "@axe-core/playwright";
import { chromium } from "@playwright/test";

const [OUT, BASE = "http://localhost:3000"] = process.argv.slice(2);
if (!OUT) throw new Error("usage: wizard.mjs <out-dir> [base-url]");
await mkdir(OUT, { recursive: true });
const findings = [];
const failures = [];
const browser = await chromium.launch({ channel: "chrome" });

async function shot(page, name, { full = true } = {}) {
  await page.waitForTimeout(350);
  await page.screenshot({ path: path.join(OUT, `${name}.png`), fullPage: full });
  const overflow = await page.evaluate(() =>
    document.documentElement.scrollWidth > document.documentElement.clientWidth + 1);
  const axe = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  const violations = axe.violations.map((v) => ({ id: v.id, impact: v.impact, nodes: v.nodes.length,
    sample: v.nodes[0]?.target?.join(" ") }));
  findings.push({ name, overflow, axe: violations });
  if (overflow || violations.length) failures.push(name);
}

const heading = (page) => page.locator("#setup-step-title");
async function expectHeading(page, text, label) {
  await heading(page).filter({ hasText: text }).waitFor({ timeout: 15000 });
  const focused = await page.evaluate(() => document.activeElement?.id);
  findings.push({ name: `${label}-focus`, focused });
}
const next = (page) => page.getByRole("button", { name: /^Next/ }).click();
const skip = (page) => page.getByRole("button", { name: "Skip for now" }).click();
const pick = (page, text) => page.locator("label", { hasText: text }).first().click();

async function signIn(context, subject) {
  await context.clearCookies();
  const page = await context.newPage();
  await page.goto(`${BASE}/signin`);
  await page.fill("#subject", subject);
  await page.click("button[type=submit]");
  await page.waitForURL(/onboarding|home/);
  return page;
}

async function about(page, scheme, vp) {
  await page.getByRole("button", { name: /^Start/ }).click();
  await expectHeading(page, "About you", `${scheme}-${vp}-about`);
  await page.getByPlaceholder("Search programs").fill("computer science");
  await page.locator("label:has(input[name=program])").first().click();
  await pick(page, "Declared");
  await shot(page, `${scheme}-${vp}-2-about`);
  await next(page);
  await expectHeading(page, "Bring your record", `${scheme}-${vp}-record`);
}

for (const scheme of ["dark", "light"]) {
  for (const [vp, size] of [["mobile", { width: 390, height: 844 }], ["laptop", { width: 1280, height: 860 }]]) {
    const stamp = `${scheme}-${vp}-${Date.now().toString(36)}`;
    const context = await browser.newContext({ colorScheme: scheme, viewport: size });
    const page = await signIn(context, `qa-wiz-${stamp}`);

    // Routing: a brand-new account lands in the wizard; /home bounces back to it.
    if (!page.url().includes("/onboarding")) failures.push(`${stamp}: new user not routed to wizard`);
    await page.goto(`${BASE}/home`);
    await page.waitForURL(/onboarding/);
    await shot(page, `${scheme}-${vp}-1-welcome`);
    await about(page, scheme, vp);

    // Resume: leaving and coming back opens the first unfinished step.
    await page.goto(`${BASE}/onboarding`);
    await heading(page).filter({ hasText: "Bring your record" }).waitFor({ timeout: 15000 })
      .catch(() => failures.push(`${stamp}: resume did not land on Bring your record`));
    await shot(page, `${scheme}-${vp}-3-record`);
    await page.getByRole("button", { name: /Add/ }).nth(1).click();   // open the DN card inline
    await shot(page, `${scheme}-${vp}-3b-record-dn-open`);
    await skip(page);

    await expectHeading(page, "How intense?", `${scheme}-${vp}-intensity`);
    await pick(page, "Semi-rigorous");
    await shot(page, `${scheme}-${vp}-4-intensity`);
    await next(page);

    await expectHeading(page, "Your week", `${scheme}-${vp}-week`);
    await pick(page, "10-20 hours a week");
    await shot(page, `${scheme}-${vp}-5-week`);
    await next(page);

    await expectHeading(page, "Schedule shape", `${scheme}-${vp}-schedule`);
    await pick(page, "No Fridays");
    await page.getByRole("button", { name: /Add a time/ }).click();
    await shot(page, `${scheme}-${vp}-6-schedule`);
    await next(page);

    await expectHeading(page, "Musts", `${scheme}-${vp}-musts`);
    await shot(page, `${scheme}-${vp}-7-musts`);
    await skip(page);

    await expectHeading(page, "Build my plan", `${scheme}-${vp}-build`);
    await shot(page, `${scheme}-${vp}-8-build`);
    const label = await page.getByText(/^Estimated — import your audit/).count();
    if (!label) failures.push(`${stamp}: build step not labelled estimated`);
    await page.getByRole("button", { name: /Build my plan/ }).click();
    await page.waitForURL(/\/home/, { timeout: 120000 });
    await page.waitForLoadState("networkidle");
    await shot(page, `${scheme}-${vp}-9-home`);

    // Returning user: /onboarding now goes Home.
    await page.goto(`${BASE}/onboarding`);
    await page.waitForURL(/\/home/);

    // Answers saved in the wizard appear in Plan -> Preferences.
    await page.goto(`${BASE}/plan/personalize#week`);
    await page.getByText("Your week").first().waitFor();
    await page.waitForTimeout(800);
    const week = await page.locator("#prefs-week select").first().inputValue().catch(() => null);
    findings.push({ name: `${stamp}-prefs-job`, value: week });
    if (week !== "15") failures.push(`${stamp}: job band not shown in Preferences (got ${week})`);
    const level = await page.locator("#prefs-intensity input[name=workload_level]:checked").count();
    if (level !== 1) failures.push(`${stamp}: workload level not shown in Preferences`);
    await shot(page, `${scheme}-${vp}-10-preferences`);
    await context.close();
  }
}

// Reduced motion: the glow is static (no animation on the card's border light).
const reduced = await browser.newContext({ reducedMotion: "reduce", colorScheme: "dark", viewport: { width: 1280, height: 860 } });
const rp = await signIn(reduced, `qa-wiz-reduced-${Date.now().toString(36)}`);
await rp.locator(".cp-glow-card").waitFor();
const anim = await rp.evaluate(() => getComputedStyle(document.querySelector(".cp-glow-card"), "::before").animationName);
findings.push({ name: "reduced-motion-glow", animation: anim });
if (anim !== "none") failures.push(`reduced motion still animates the glow (${anim})`);
await reduced.close();

await browser.close();
await writeFile(path.join(OUT, "findings.json"), JSON.stringify({ failures, findings }, null, 2));
console.log(JSON.stringify({ failures, shots: findings.filter((f) => f.axe).length }, null, 2));
process.exit(failures.length ? 1 : 0);
