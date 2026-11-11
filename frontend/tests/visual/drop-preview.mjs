// A student's drop, in the browser: the preview dialog for "Remove" (a pure
// drop), "Suggest a replacement" (suggestions only - nothing is applied) and
// the per-term "Keep it at N credits" confirm. Synthetic by default: a mock
// API serves a fabricated preview. Pass a preview JSON (the shape of
// POST /student/plans/current/changes/preview) to render a specific one; it is
// read at run time and never copied into the repository.
// node tests/visual/drop-preview.mjs <outside-repo-output-folder> [preview.json]
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { cp, lstat, mkdir, mkdtemp, open, readFile, rm, rmdir, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { spawn, execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { chromium, expect as baseExpect } from "@playwright/test";

const expect = baseExpect.configure({ timeout: 30000 });
const source = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const out = path.resolve(process.argv[2] ?? "");
if (!process.argv[2] || out.toLowerCase().includes("onedrive") || out.startsWith(source)) {
  throw new Error("Output must be outside the repository and OneDrive");
}
const FABRICATED = {
  preview_id: "p-drop", base_revision: 4, valid: true, applied: false, errors: [],
  expires_at: new Date(Date.now() + 3600e3).toISOString(),
  change: { kind: "remove", course: "01:111:222", term_code: "20271" },
  diff: {
    summary: "Spring 2027: 18 → 15 credits. Writing Across Fields is now unmet. Graduation unchanged.",
    big: true, graduation_before: "20291", graduation_after: "20291", last_term_before: "20289", last_term_after: "20289",
    requirements_lost: ["Writing Across Fields"], requirements_gained: [], new_issues: [], forced_moves: [],
    unmet: [{ code: "CORE_WX", name: "Writing Across Fields" }], dropped_course: "01:111:222", dropped_term: "20271",
    terms: [{ term_code: "20271", label: "Spring 2027", added: [], removed: ["01:111:222"],
      before: [{ course: "01:111:222", title: "Stories of the Sea in Film", credits: "3", state: "planner_selected" }],
      after: [], credits_before: "18", credits_after: "15", workload_before: "24", workload_after: "21", budget: "17.5",
      credit_floor: "16", below_minimum: true, notes: [],
      warnings: ["15 credits is below your 16-credit minimum for Spring 2027.", "Workload 21 is over your 17.5 budget for Spring 2027."] }],
  },
};
const preview = process.argv[3] ? JSON.parse(await readFile(process.argv[3], "utf-8")) : FABRICATED;
const dropped = preview.diff.dropped_course;
const term = preview.diff.dropped_term;

await mkdir(out, { recursive: true });
const mirror = await mkdtemp(path.join(tmpdir(), "coursepilot-drop-ui-"));
await cp(source, mirror, { recursive: true, filter: (file) => !path.relative(source, file).split(path.sep)
  .some((part) => part === "node_modules" || part === ".next" || part.startsWith(".env")) });
const modules = path.join(mirror, "node_modules");
await symlink(path.join(source, "node_modules"), modules, "junction");
const fixtureDir = path.join(mirror, "src/app/(app)/drop-fixture");
await mkdir(fixtureDir, { recursive: true });
await writeFile(path.join(fixtureDir, "page.tsx"), `"use client";
import { ChangePreviewDialog, usePlanChanges } from "@/features/plan/plan-changes";
export default function Fixture() {
  const changes = usePlanChanges();
  return <><h1>Drop preview fixture</h1>
    <button onClick={() => changes.request({ kind: "remove", course: ${JSON.stringify(dropped)}, term_code: ${JSON.stringify(term)} })}>Remove</button>
    <ChangePreviewDialog changes={changes} /></>;
}`);

const calls = [];
const server = createServer(async (req, res) => {
  const url = new URL(req.url, "http://localhost");
  const route = url.pathname.replace(/^\/api\/v1/, "");
  let body = ""; for await (const chunk of req) body += chunk;
  calls.push({ method: req.method, route, body: body ? JSON.parse(body) : null });
  const send = (value, code = 200) => { res.writeHead(code, { "content-type": "application/json" }); res.end(JSON.stringify(value)); };
  if (route === "/me") return send({ sign_in_method: "dev", institution: { status: "verified" }, has_academic_profile: true, onboarding_complete: true });
  if (route.endsWith("/changes/preview")) return send(preview);
  if (route.endsWith("/plans/current/search")) return send({
    interpretation: { term_code: term, requirement_names: preview.diff.unmet.filter((u) => u.code !== "DEGREE_CREDITS").map((u) => u.name),
      topic: null, online: false, async_only: false, in_person: false, campus: null, earliest: null, latest: null,
      avoid_days: [], exclude: [], notes: [], did_you_mean: [] },
    results: [{ course: "01:999:101", title: "A Fabricated Writing Seminar", credits: 3, requirements: ["Fabricated requirement"],
      offering: "confirmed", delivery: ["in person"], why: "Fills the unmet requirement", planned_term: null }],
    not_available: [] });
  return send({});
});
await new Promise((resolve) => server.listen(18014, "127.0.0.1", resolve));
const log = await open(path.join(out, "frontend-server.log"), "w");
const child = spawn(process.execPath, [path.join(mirror, "node_modules/next/dist/bin/next"), "dev", "-p", "3104"], {
  cwd: mirror, env: { ...process.env, AUTH_MODE: "dev", COURSEPILOT_API_URL: "http://127.0.0.1:18014/api/v1", NEXT_TELEMETRY_DISABLED: "1" },
  windowsHide: true, stdio: ["ignore", log.fd, log.fd],
});
let browser;
const errors = [];
try {
  for (let i = 0; i < 120; i++) {
    try { if ((await fetch("http://localhost:3104/signin")).ok) break; } catch {}
    await new Promise((r) => setTimeout(r, 1000));
  }
  browser = await chromium.launch({ channel: "chrome" });
  const page = await (await browser.newContext({ viewport: { width: 1280, height: 900 } })).newPage();
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("http://localhost:3104/signin");
  await page.fill("#subject", "drop-synthetic");
  await page.locator('input[name="returnTo"]').evaluate((el) => { el.value = "/drop-fixture"; });
  await page.click('button[type="submit"]');
  await page.waitForURL("**/drop-fixture");
  await page.waitForLoadState("networkidle");
  const dialog = page.getByRole("dialog");
  await expect(async () => {
    if (!(await dialog.isVisible())) await page.getByRole("button", { name: "Remove", exact: true }).click();
    await expect(dialog).toBeVisible({ timeout: 3000 });
  }).toPass({ timeout: 60000 });
  await expect(dialog).toContainText(preview.diff.summary);
  await expect(dialog).not.toContainText("Added:");
  const sp = preview.diff.terms.find((t) => t.term_code === term);
  const keep = `Keep it at ${Number(sp.credits_after)} credits for this term`;
  if (sp.below_minimum) await expect(dialog.getByRole("button", { name: keep })).toBeVisible();
  for (const w of sp.warnings) await expect(dialog).toContainText(w);
  await dialog.evaluate((el) => Promise.all(el.getAnimations({ subtree: true }).map((a) => a.finished)));
  await page.screenshot({ path: path.join(out, "drop-preview.png") });

  await dialog.getByRole("button", { name: "Suggest a replacement", exact: true }).click();
  await expect(dialog.getByText("Suggested replacements")).toBeVisible();
  await expect(dialog.getByText("A Fabricated Writing Seminar")).toBeVisible();
  await dialog.getByText("A Fabricated Writing Seminar").scrollIntoViewIfNeeded();
  await page.screenshot({ path: path.join(out, "drop-suggestions.png") });
  // Suggestions only: no apply, no swap preview until a course is chosen.
  assert.ok(!calls.some((c) => c.route.endsWith("/changes/apply")), "nothing applied");
  assert.ok(!calls.some((c) => c.route.endsWith("/changes/preview") && c.body?.kind !== "remove"), "no swap requested");
  const search = calls.find((c) => c.route.endsWith("/plans/current/search"));
  assert.ok(search && Array.isArray(search.body.requirements), "searched for the unmet requirement");
  assert.deepEqual(errors, []);
  await writeFile(path.join(out, "results.json"), JSON.stringify({ ok: true, errors }, null, 2));
  console.log(`drop preview checks passed; screenshots in ${out}`);
} finally {
  await browser?.close();
  if (process.platform === "win32") {
    try { execFileSync("taskkill", ["/PID", String(child.pid), "/T", "/F"], { windowsHide: true, stdio: "ignore" }); } catch {}
  } else child.kill("SIGTERM");
  server.closeAllConnections(); server.close(); await log.close();
  // Remove the node_modules JUNCTION first (never its target), then the copy.
  if ((await lstat(modules).catch(() => null))?.isSymbolicLink()) await rmdir(modules);
  assert.equal(await lstat(modules).catch(() => null), null, "junction removed before deleting the mirror");
  await rm(mirror, { recursive: true, force: true, maxRetries: 3 }).catch(() => {});
}
