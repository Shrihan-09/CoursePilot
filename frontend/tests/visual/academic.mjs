// Phase 7.2.2 visual + functional QA: Profile progress and the WebReg import review.
// Not a CI test; needs the local dev-auth stack running (see qa.mjs).
//
//   run: QA_BASE=http://localhost:3000 node tests/visual/academic.mjs <out-dir> <fabricated-webreg.pdf>
//
// Every student is FABRICATED (dev sign-in subject `qa722-*`). The PDF must be
// a FABRICATED positioned WebReg-style schedule with one row that matches a
// loaded SOC section exactly and one that matches nothing. Never pass a real
// schedule. Writes screenshots + findings.json to <out-dir>; nothing in the repo.

import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";

import AxeBuilder from "@axe-core/playwright";
import { chromium } from "@playwright/test";

const [OUT, PDF] = process.argv.slice(2);
if (!OUT || !PDF) throw new Error("usage: academic.mjs <out-dir> <fabricated-webreg.pdf>");
const BASE = process.env.QA_BASE ?? "http://localhost:3000";
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
const pdfBytes = await readFile(PDF);
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

async function shot(page, name, { full = true, audit = true } = {}) {
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
  ["01:198:111", "20259", "A"], ["01:640:151", "20259", "B+"], ["01:198:112", "20261", "B"],
  ["01:198:211", "20269", null, "in_progress"], ["01:198:206", "20269", null, "in_progress"],
];

async function seed(page) {
  const programs = (await api(page, "GET", "/programs")).json.programs;
  const pick = programs.find((p) => p.program_code === "198" && p.degree_type === "BA");
  const version = pick.versions.find((v) => v.support_status !== "unavailable");
  await api(page, "POST", "/student/profile", { program_key: pick.program_key, catalog_year: version.catalog_year });
  for (const [course_code, term_code, grade, status = "completed"] of HISTORY) {
    await api(page, "POST", "/student/courses", { course_code, term_code, status, grade, credit_origin: "rutgers" });
  }
  await api(page, "POST", "/student/onboarding/complete");
}

const responsive = async (page, name, { audit = true } = {}) => {
  for (const [vp, size] of Object.entries(VIEWPORTS)) {
    await page.setViewportSize(size);
    await shot(page, `${name}-${vp}`, { audit: audit && vp !== "desktop" });
  }
  await page.setViewportSize(VIEWPORTS.laptop);
};

async function upload(page, name, mimeType, buffer) {
  await page.locator("input[type=file]").setInputFiles({ name, mimeType, buffer });
}

for (const scheme of ["light", "dark"]) {
  const stamp = `${scheme}-${Date.now().toString(36)}`;
  const context = await browser.newContext({ colorScheme: scheme, viewport: VIEWPORTS.laptop });
  const page = await signIn(context, `qa722-${stamp}`);
  await seed(page);

  /* ------------------------------------------------------------ Profile */
  await page.goto(`${BASE}/profile`);
  await page.getByText("They do not establish eligibility to graduate.").waitFor({ timeout: 30000 });
  await responsive(page, `${scheme}-profile`);
  const progress = (await api(page, "GET", "/student/progress")).json;
  expect(`${scheme} progress never assesses graduation`, progress.graduation_eligibility === "not_assessed");
  expect(`${scheme} three explicit progress scopes`,
    progress.metrics.map((m) => m.scope).join() === "modeled_overall,primary_program,school_core",
    progress.metrics.map((m) => m.scope).join());
  const major = progress.metrics.find((m) => m.scope === "primary_program");
  expect(`${scheme} in-progress work is conditional, above completed`,
    Number(major.projected_current.percent) > Number(major.completed.percent),
    `${major.completed.percent} -> ${major.projected_current.percent}`);
  expect(`${scheme} no graduation claim on the page`,
    !(await page.getByText(/eligible to graduate|ready to graduate|on track to graduate|you can graduate/i).count()));
  for (const metric of progress.metrics.filter((m) => m.availability === "unavailable")) {
    expect(`${scheme} unavailable scope shows no percentage (${metric.scope})`, metric.completed.percent === null);
  }
  await page.getByText("How this percentage is measured").first().click();
  await page.getByText("Coverage and limitations").click();
  await shot(page, `${scheme}-profile-expanded`);
  await page.setViewportSize(VIEWPORTS.narrow);
  await shot(page, `${scheme}-profile-expanded-narrow`);
  await page.setViewportSize(VIEWPORTS.laptop);

  /* ----------------------------------------------- WebReg import review */
  const before = (await api(page, "GET", "/student/courses")).json.entries.length;
  await page.goto(`${BASE}/schedule`);
  await page.getByRole("heading", { name: "Import my registered schedule" }).waitFor({ timeout: 30000 });
  await shot(page, `${scheme}-import-empty`);

  // Not a PDF / no positioned layout: refused, no OCR, manual path stays open.
  await upload(page, "notes.pdf", "application/pdf", Buffer.from("not a pdf at all"));
  await page.getByText("This PDF could not be read safely.").waitFor({ timeout: 30000 });
  await shot(page, `${scheme}-import-refused`);

  await page.reload();
  await page.getByRole("heading", { name: "Import my registered schedule" }).waitFor({ timeout: 30000 });
  await upload(page, "fabricated-schedule.pdf", "application/pdf", pdfBytes);
  await page.getByText(/Review every row\. This review expires/).waitFor({ timeout: 30000 });
  await responsive(page, `${scheme}-import-review`);
  const staged = (await api(page, "GET", "/student/schedule-imports")).json.imports.find((i) => i.state === "extracted");
  const detail = (await api(page, "GET", `/student/schedule-imports/${staged.id}`)).json;
  const states = detail.rows.map((r) => r.match_status).sort().join();
  expect(`${scheme} one exact match and one no-match`, states === "EXACT_MATCH,NO_MATCH", states);
  expect(`${scheme} nothing is recorded before confirmation`,
    (await api(page, "GET", "/student/registrations")).json.registrations.length === 0);
  expect(`${scheme} unmatched row explains no meetings are invented`,
    await page.getByText("does not invent meetings").count());
  expect(`${scheme} match states are worded for students`,
    await page.getByText("Exact match with Rutgers section data").count()
    && await page.getByText("No matching Rutgers section data").count()
    && !(await page.getByText(/SOC METADATA|metadata_unavailable|EXACT_MATCH/).count()));

  await page.getByRole("button", { name: "Confirm reviewed registrations" }).click();
  await page.getByRole("heading", { name: "Confirmed imported registrations" }).waitFor({ timeout: 30000 });
  await page.getByText(/week, from your confirmed registrations/).waitFor({ timeout: 30000 });
  await responsive(page, `${scheme}-import-confirmed`);
  const regs = (await api(page, "GET", "/student/registrations")).json.registrations;
  expect(`${scheme} two registrations confirmed`, regs.length === 2, String(regs.length));
  expect(`${scheme} only the exact SOC match carries meetings`,
    regs.filter((r) => r.metadata_state === "available" && r.normalized_meetings.length > 0).length === 1
    && regs.filter((r) => r.metadata_state === "unavailable" && r.normalized_meetings.length === 0).length === 1);
  expect(`${scheme} the undrawn registration is disclosed`,
    await page.getByText("1 registration without public meeting data is listed below, not drawn.").count());
  const after = (await api(page, "GET", "/student/courses")).json.entries.length;
  expect(`${scheme} import never changes academic history`, before === after, `${before} -> ${after}`);
  const matched = regs.find((r) => r.metadata_state === "available");
  expect(`${scheme} the current list shows the imported section, labelled as imported`,
    await page.getByText("imported from your schedule PDF, confirmed by you").count()
    && await page.getByText(`Section ${matched.section_number}, index ${matched.index_number}`).count());
  expect(`${scheme} the import does not write a section confirmation`,
    (await api(page, "GET", "/student/current-schedule")).json.courses.every((c) => c.confirmed.length === 0));
  expect(`${scheme} import never claims to register`,
    !(await page.getByText(/you are registered|successfully registered|enrolled you/i).count()));

  /* --------------------------------------------- another student's view */
  const other = await signIn(context, `qa722-other-${stamp}`);
  await seed(other);
  const foreign = await api(other, "GET", `/student/schedule-imports/${staged.id}`);
  expect(`${scheme} another student cannot read the import`, foreign.status === 404, String(foreign.status));
  expect(`${scheme} another student sees no registrations`,
    (await api(other, "GET", "/student/registrations")).json.registrations.length === 0);
  await context.close();
}

await writeFile(path.join(OUT, "findings.json"), JSON.stringify({ findings, assertions }, null, 2));
await browser.close();
const issues = findings.filter((f) => f.overflow || f.smallTargets.length || f.axe.length);
console.log(`screens checked: ${findings.length}, with findings: ${issues.length}`);
for (const f of issues) console.log(JSON.stringify(f));
console.log(`assertions: ${assertions.filter((a) => a.ok).length}/${assertions.length} passed`);
for (const a of assertions.filter((x) => !x.ok)) console.log("FAILED", JSON.stringify(a));
