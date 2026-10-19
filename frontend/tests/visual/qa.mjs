// Phase 7.1 visual QA walkthrough (not a CI test; needs the local stack running).
//
//   backend : COURSEPILOT_ENV=local AUTH_PROVIDER=dev DEV_AUTH_ENABLED=true uvicorn app.main:app
//   frontend: AUTH_MODE=dev next dev -p 3000
//   run     : node tests/visual/qa.mjs <out-dir> <fixtures-dir>
//
// Drives the INSTALLED Chrome (no browser download). Uses dev sign-in with
// throwaway `qa-*` subjects and SANITIZED, fabricated transcripts only.
// Writes screenshots + findings.json to <out-dir>; nothing goes in the repo.

import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

import AxeBuilder from "@axe-core/playwright";
import { chromium } from "@playwright/test";

const [OUT, FIX] = process.argv.slice(2);
if (!OUT || !FIX) throw new Error("usage: qa.mjs <out-dir> <fixtures-dir>");
const BASE = "http://localhost:3000";
const VIEWPORTS = {
  narrow: { width: 320, height: 640 },
  mobile: { width: 390, height: 844 },
  tablet: { width: 820, height: 1180 },
  laptop: { width: 1280, height: 800 },
  desktop: { width: 1600, height: 1000 },
};
const findings = [];
await mkdir(OUT, { recursive: true });

const browser = await chromium.launch({ channel: "chrome" });

async function check(page, name) {
  const overflow = await page.evaluate(() =>
    document.documentElement.scrollWidth > document.documentElement.clientWidth + 1);
  const small = await page.evaluate(() => [...document.querySelectorAll(
    "a[href], button:not([disabled]), input:not([type=hidden]), select, [role=tab], [role=radio]")]
    .filter((el) => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
      // The label is the target for a checkbox/radio; visually hidden skip links are exempt.
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
  await page.waitForTimeout(250);
  await page.screenshot({ path: path.join(OUT, `${name}.png`), fullPage: full });
  if (audit) await check(page, name);
}

async function signIn(context, subject) {
  await context.clearCookies();          // one account at a time
  const page = await context.newPage();
  await page.goto(`${BASE}/signin`);
  await page.fill("#subject", subject);
  await page.click("button[type=submit]");
  await page.waitForURL(/onboarding|home/);
  return page;
}

async function chooseCs(page) {
  await page.getByRole("button", { name: /get started/i }).click();
  await page.getByRole("radio").first().waitFor();
  await page.getByPlaceholder("Search programs").fill("computer science");
  await page.getByRole("radio").first().click();
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByRole("heading", { name: /add the courses/i }).waitFor();
}

async function upload(page, file) {
  await page.getByRole("button", { name: /upload unofficial transcript/i }).click();
  await page.setInputFiles("#transcript-file", path.join(FIX, file));
}

for (const scheme of ["light", "dark"]) {
  const stamp = `${scheme}-${Date.now().toString(36)}`;
  const context = await browser.newContext({ colorScheme: scheme, viewport: VIEWPORTS.laptop });

  // Sign-in at every width.
  const anon = await context.newPage();
  for (const [vp, size] of Object.entries(VIEWPORTS)) {
    await anon.setViewportSize(size);
    await anon.goto(`${BASE}/signin`);
    await shot(anon, `${scheme}-signin-${vp}`, { audit: vp === "mobile" || vp === "laptop" });
  }
  // Keyboard focus is visible.
  await anon.setViewportSize(VIEWPORTS.laptop);
  await anon.goto(`${BASE}/signin`);
  await anon.keyboard.press("Tab");
  await anon.keyboard.press("Tab");
  await shot(anon, `${scheme}-signin-focus`, { audit: false });
  await anon.close();

  // Onboarding: standard transcript through to completion.
  const page = await signIn(context, `qa-${stamp}`);
  await shot(page, `${scheme}-onboarding-welcome`);
  await page.getByRole("button", { name: /get started/i }).click();
  await page.getByRole("radio").first().waitFor();
  await shot(page, `${scheme}-onboarding-program`, { full: true });
  await page.getByPlaceholder("Search programs").fill("computer science");
  await page.getByRole("radio").first().click();
  await shot(page, `${scheme}-onboarding-program-selected`);
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByRole("heading", { name: /add the courses/i }).waitFor();
  await shot(page, `${scheme}-onboarding-history`);
  await page.getByRole("button", { name: /upload unofficial transcript/i }).click();
  await shot(page, `${scheme}-onboarding-upload`);
  await page.setInputFiles("#transcript-file", path.join(FIX, "standard.pdf"));
  await page.getByRole("heading", { name: /review your transcript/i }).waitFor({ timeout: 60000 });
  await shot(page, `${scheme}-review-needs-you`, { full: true });
  for (const [vp, size] of Object.entries(VIEWPORTS)) {
    await page.setViewportSize(size);
    await page.reload();
    await page.getByRole("heading", { name: /review your transcript/i }).waitFor({ timeout: 60000 });
    await shot(page, `${scheme}-review-${vp}`, { full: true, audit: vp !== "desktop" });
  }
  await page.setViewportSize(VIEWPORTS.laptop);
  await page.getByRole("tab", { name: /all/i }).click();
  await shot(page, `${scheme}-review-all`, { full: true });

  // Resolve: mark ungraded as in progress, fix the A- grade by leaving it out, exclude the unknown.
  await page.getByRole("button", { name: /mark as in progress/i }).click();
  await page.waitForTimeout(1500);
  await page.getByRole("tab", { name: /needs you/i }).click();
  for (let i = 0; i < 6; i++) {
    const leaveOut = page.getByRole("button", { name: "Leave out" }).first();
    if (!(await leaveOut.count())) break;
    await leaveOut.click();
    await page.waitForTimeout(700);
  }
  await shot(page, `${scheme}-review-ready`, { full: true });
  await page.getByRole("button", { name: /confirm and save/i }).click();
  await page.getByRole("heading", { name: /you.re set up/i }).waitFor({ timeout: 30000 });
  await shot(page, `${scheme}-onboarding-complete`);
  await page.getByRole("button", { name: /go to coursepilot/i }).click();
  await page.waitForURL(/\/home/);
  await page.getByRole("heading", { name: /your plan at a glance/i }).waitFor();
  for (const [vp, size] of Object.entries(VIEWPORTS)) {
    await page.setViewportSize(size);
    await page.goto(`${BASE}/home`);
    await page.getByRole("heading", { name: /your plan at a glance/i }).waitFor();
    await shot(page, `${scheme}-home-${vp}`, { full: true, audit: vp !== "desktop" });
  }
  await page.setViewportSize(VIEWPORTS.mobile);
  await page.getByRole("button", { name: "More" }).click();
  await shot(page, `${scheme}-mobile-more-sheet`, { audit: false });
  await page.keyboard.press("Escape");
  for (const vp of ["mobile", "laptop"]) {
    await page.setViewportSize(VIEWPORTS[vp]);
    await page.goto(`${BASE}/settings`);
    await page.getByRole("heading", { name: "Settings", level: 1 }).waitFor();
    await page.waitForTimeout(600);
    await shot(page, `${scheme}-settings-${vp}`, { full: true });
  }

  // Error, empty, dense and interleaved states on fresh accounts.
  const p2 = await signIn(context, `qa-${stamp}-b`);
  await chooseCs(p2);
  await p2.getByRole("button", { name: /enter courses yourself/i }).click();
  await shot(p2, `${scheme}-manual-empty`, { full: true });
  await p2.fill("input.code", "01:999:000");
  await p2.getByRole("button", { name: "Add course" }).click();
  await p2.getByText(/no course with this code/i).waitFor();
  await shot(p2, `${scheme}-manual-error`, { full: true });
  await p2.getByRole("button", { name: "Back" }).click();
  await upload(p2, "not-a-pdf.png");
  await p2.getByText(/isn.t a PDF/i).waitFor();
  await shot(p2, `${scheme}-upload-error`);
  await p2.setInputFiles("#transcript-file", path.join(FIX, "dense.pdf"));
  await p2.getByRole("heading", { name: /review your transcript/i }).waitFor({ timeout: 60000 });
  await p2.getByRole("tab", { name: /all/i }).click();
  await shot(p2, `${scheme}-review-dense-laptop`, { full: true });
  await p2.setViewportSize(VIEWPORTS.narrow);
  await shot(p2, `${scheme}-review-dense-narrow`, { full: true });

  const p3 = await signIn(context, `qa-${stamp}-c`);
  await chooseCs(p3);
  await upload(p3, "interleaved.pdf");
  await p3.getByRole("heading", { name: /review your transcript/i }).waitFor({ timeout: 60000 });
  await shot(p3, `${scheme}-review-interleaved`, { full: true });

  // Loading skeletons: hold the programs request.
  const p4 = await signIn(context, `qa-${stamp}-d`);
  await p4.route("**/api/cp/programs", async (route) => {
    await new Promise((r) => setTimeout(r, 4000));
    await route.continue();
  });
  await p4.getByRole("button", { name: /get started/i }).click();
  await shot(p4, `${scheme}-program-loading`, { audit: false });

  // Motion language.
  const d = await context.newPage();
  await d.goto(`${BASE}/design`);
  await d.waitForTimeout(1800);
  await shot(d, `${scheme}-design`, { full: true });
  await context.close();

  const reduced = await browser.newContext({ colorScheme: scheme, reducedMotion: "reduce",
                                             viewport: VIEWPORTS.mobile });
  const r = await reduced.newPage();
  await r.goto(`${BASE}/design`);
  await r.waitForTimeout(1000);
  await shot(r, `${scheme}-design-reduced-mobile`, { full: true, audit: false });
  await reduced.close();
}

await browser.close();
await writeFile(path.join(OUT, "findings.json"), JSON.stringify(findings, null, 2));
const bad = findings.filter((f) => f.overflow || f.axe.length || f.smallTargets.length);
console.log(`${findings.length} audited screens; ${bad.length} with findings`);
for (const f of bad) console.log(JSON.stringify(f));
