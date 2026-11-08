// Prompt 10a follow-up walkthrough (not a CI test; needs the local stack running,
// same as wizard.mjs). Phone width (390px), dark and light. Covers only the
// changed screens: About you ("What year are you?"), Bring your record per
// class standing (first-year, transfer), Musts with a detected retake, and the
// Home "Your grades are in" reminder (shown, then dismissed).
//
//   run: node tests/visual/wizard-followup.mjs <out-dir> [base-url]
//
// Throwaway `qa-fu-*` dev subjects; the only "record" is two fabricated manual
// course entries (Calc I passed, Calc II failed). Exits 1 on an axe violation,
// a horizontal overflow, or a failed check.

import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

import AxeBuilder from "@axe-core/playwright";
import { chromium } from "@playwright/test";

const [OUT, BASE = "http://localhost:3000"] = process.argv.slice(2);
if (!OUT) throw new Error("usage: wizard-followup.mjs <out-dir> [base-url]");
await mkdir(OUT, { recursive: true });
const findings = [];
const failures = [];
const browser = await chromium.launch({ channel: "chrome" });
const PHONE = { width: 390, height: 844 };

async function shot(page, name) {
  await page.waitForTimeout(400);
  await page.screenshot({ path: path.join(OUT, `${name}.png`), fullPage: true });
  const overflow = await page.evaluate(() =>
    document.documentElement.scrollWidth > document.documentElement.clientWidth + 1);
  const axe = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  const violations = axe.violations.map((v) => ({ id: v.id, impact: v.impact, nodes: v.nodes.length,
    sample: v.nodes[0]?.target?.join(" ") }));
  findings.push({ name, overflow, axe: violations });
  if (overflow || violations.length) failures.push(name);
}
const check = (ok, what) => { findings.push({ check: what, ok }); if (!ok) failures.push(what); };
const heading = (page, text) => page.locator("#setup-step-title").filter({ hasText: text }).waitFor({ timeout: 20000 });
const next = (page) => page.getByRole("button", { name: /^Next/ }).click();
const skip = (page) => page.getByRole("button", { name: "Skip for now" }).click();
const pick = (page, text) => page.locator("label", { hasText: text }).first().click();
const cards = (page) => page.locator("[data-card]").evaluateAll((els) => els.map((e) => e.getAttribute("data-card")));

async function start(scheme, who) {
  const context = await browser.newContext({ colorScheme: scheme, viewport: PHONE });
  await context.clearCookies();
  const page = await context.newPage();
  await page.goto(`${BASE}/signin`);
  await page.fill("#subject", `qa-fu-${who}-${scheme}-${Date.now().toString(36)}`);
  await page.click("button[type=submit]");
  await page.waitForURL(/onboarding/);
  await page.getByRole("button", { name: /^Start/ }).click();
  await heading(page, "About you");
  await page.getByPlaceholder("Search programs").fill("computer science");
  await page.locator("label:has(input[name=program])").first().click();
  await pick(page, "Declared");
  return { context, page };
}

async function aboutWith(page, standing, firstTerm) {
  await pick(page, standing);
  await page.getByLabel("First semester at Rutgers").selectOption(firstTerm);
}

for (const scheme of ["dark", "light"]) {
  // ---- First-year: WebReg leads; transcript + DN under "Later"; AP reveals DN.
  {
    const { context, page } = await start(scheme, "fy");
    await aboutWith(page, "First-year", "20269");
    await shot(page, `${scheme}-1-about-standing`);
    await next(page);
    await heading(page, "Bring your record");
    const order = await cards(page);
    check(JSON.stringify(order) === JSON.stringify(["webreg", "transcript", "dn"]), `${scheme} first-year card order (${order})`);
    check(!(await page.locator("details[data-later]").evaluate((d) => d.open)), `${scheme} Later collapsed`);
    await shot(page, `${scheme}-2-record-first-year`);
    await page.getByRole("button", { name: "Upload your Degree Navigator audit now" }).click();
    const revealed = await cards(page);
    check(revealed[1] === "dn", `${scheme} AP line reveals DN (${revealed})`);
    await page.locator("details[data-later] summary").click();
    await shot(page, `${scheme}-3-record-first-year-ap-later-open`);
    await context.close();
  }
  // ---- Transfer: DN first.
  {
    const { context, page } = await start(scheme, "tr");
    await aboutWith(page, "Transfer", "20261");
    await next(page);
    await heading(page, "Bring your record");
    const order = await cards(page);
    check(order[0] === "dn", `${scheme} transfer DN first (${order})`);
    await shot(page, `${scheme}-4-record-transfer`);
    await context.close();
  }
  // ---- Sophomore with a failed Calc II: Musts lists the retake; Home reminder.
  {
    const { context, page } = await start(scheme, "so");
    await aboutWith(page, "Sophomore", "20259");
    await next(page);
    await heading(page, "Bring your record");
    for (const body of [
      { course_code: "01:640:151", term_code: "20259", status: "completed", grade: "A" },
      { course_code: "01:640:152", term_code: "20261", status: "completed", grade: "F" },
    ]) {
      const r = await page.request.post(`${BASE}/api/cp/student/courses`, { data: body });
      check(r.ok(), `${scheme} add ${body.course_code} (${r.status()})`);
    }
    await next(page);
    for (const t of ["How intense?", "Your week", "Schedule shape"]) { await heading(page, t); await skip(page); }
    await heading(page, "Musts");
    // The list comes from a plan run on the server: wait for it, not for any alert.
    await page.locator("[data-retake]").or(page.getByText(/No retakes needed|couldn.t be checked/))
      .first().waitFor({ timeout: 120000 });
    const retake = await page.locator("[data-retake='01:640:152']").count();
    check(retake === 1, `${scheme} Calc II listed as a retake`);
    const asap = page.locator("[data-retake='01:640:152'] label", { hasText: "ASAP (Recommended)" });
    check(await asap.count() === 1, `${scheme} ASAP option shown`);
    await shot(page, `${scheme}-5-musts-retake`);
    // Choose the first selectable non-ASAP option, then save (Next).
    const options = page.locator("[data-retake='01:640:152'] label:has(input:not([disabled]))");
    const n = await options.count();
    if (n > 1) await options.nth(1).click();
    await shot(page, `${scheme}-6-musts-retake-chosen`);
    await next(page);
    await heading(page, "Build my plan");
    const profile = await (await page.request.get(`${BASE}/api/cp/student/planning-profile`)).json();
    const pin = (profile.profile?.pins ?? []).find((p) => p.course === "01:640:152");
    findings.push({ name: `${scheme}-retake-pin`, pin });
    check(Boolean(pin?.retake), `${scheme} retake choice saved as a pin`);
    await page.getByRole("button", { name: /Build my plan/ }).click();
    await page.waitForURL(/\/home/, { timeout: 180000 });
    await page.waitForLoadState("networkidle");
    const reminder = page.getByText(/Your grades are in/);
    await reminder.first().waitFor({ timeout: 20000 }).catch(() => {});
    check(await reminder.count() > 0, `${scheme} grades reminder on Home`);
    await shot(page, `${scheme}-7-home-reminder`);
    await page.getByRole("button", { name: "Dismiss" }).click();
    await page.waitForTimeout(800);
    check(await page.getByText(/Your grades are in/).count() === 0, `${scheme} reminder dismissed`);
    await page.reload();
    await page.waitForLoadState("networkidle");
    check(await page.getByText(/Your grades are in/).count() === 0, `${scheme} reminder stays dismissed`);
    await page.goto(`${BASE}/plan/personalize#musts`);
    await page.locator("[data-retake='01:640:152']").waitFor({ timeout: 120000 }).catch(() => {});
    check(await page.locator("[data-retake='01:640:152']").count() === 1, `${scheme} retake editable in Preferences`);
    await shot(page, `${scheme}-8-preferences-musts`);
    await context.close();
  }
}

await browser.close();
await writeFile(path.join(OUT, "findings.json"), JSON.stringify({ failures, findings }, null, 2));
console.log(JSON.stringify({ failures, shots: findings.filter((f) => f.axe).length }, null, 2));
process.exit(failures.length ? 1 : 0);
