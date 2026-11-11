// Launch-prep screenshots: the Coursenav header, the affiliation footer and the
// assistant's 250-character counter. Synthetic: a mock API, no model, no real
// record or database. A temporary copy keeps Next build output outside OneDrive
// and is deleted afterwards.
// node tests/visual/launch-prep.mjs <outside-repo-output-folder>
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { cp, lstat, mkdir, mkdtemp, open, rm, rmdir, symlink, writeFile } from "node:fs/promises";
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
await mkdir(out, { recursive: true });
const mirror = await mkdtemp(path.join(tmpdir(), "coursenav-launch-ui-"));
await cp(source, mirror, { recursive: true, filter: (file) => !path.relative(source, file).split(path.sep)
  .some((part) => part === "node_modules" || part === ".next" || part.startsWith(".env")) });
const modules = path.join(mirror, "node_modules");
await symlink(path.join(source, "node_modules"), modules, "junction");

const DISCLAIMER = "Coursenav is a student project and is not affiliated with or endorsed by Rutgers University.";
const footer = "Coursenav is a planning aid; your advisor and Degree Navigator are authoritative.";
const server = createServer(async (req, res) => {
  const url = new URL(req.url, "http://localhost");
  const route = url.pathname.replace(/^\/api\/v1/, "");
  for await (const _ of req) { /* drain */ }
  const send = (value, code = 200) => { res.writeHead(code, { "content-type": "application/json" }); res.end(JSON.stringify(value)); };
  if (route === "/me") return send({ sign_in_method: "dev", institution: { status: "verified" }, has_academic_profile: true, onboarding_complete: true });
  if (route === "/student/assistant") return send({ user_key: "fabricated-user", conversation_id: "chat-1",
    conversations: [{ id: "chat-1", title: "New chat", archived: false, created_at: new Date().toISOString(), updated_at: new Date().toISOString() }],
    enabled: true, reason: null, daily_limit: 50, max_input_chars: 250, remaining_today: 50, suggestions: [], history: [], footer });
  if (route.includes("readiness")) return send({ state: "needs_academic_history" });
  if (route.includes("planning-context") || route.endsWith("/plans/context")) return send({ earliest_start_term: "20271", start_options: [{ term_code: "20271", label: "Spring 2027" }], latest_recorded_term: null });
  if (route.endsWith("/plans/current")) return send({ plan: null });
  if (route.includes("planning-profile")) return send({ profile: null });
  return send({});
});
await new Promise((resolve) => server.listen(18013, "127.0.0.1", resolve));
const log = await open(path.join(out, "frontend-server.log"), "w");
const child = spawn(process.execPath, [path.join(mirror, "node_modules/next/dist/bin/next"), "dev", "-p", "3103"], {
  cwd: mirror, env: { ...process.env, AUTH_MODE: "dev", COURSEPILOT_API_URL: "http://127.0.0.1:18013/api/v1", NEXT_TELEMETRY_DISABLED: "1" },
  windowsHide: true, stdio: ["ignore", log.fd, log.fd],
});
let browser;
const errors = [];
const checks = [];
try {
  for (let i = 0; i < 120; i++) {
    try { if ((await fetch("http://localhost:3103/signin")).ok) break; } catch {}
    await new Promise((r) => setTimeout(r, 1000));
  }
  browser = await chromium.launch({ channel: "chrome" });
  const page = await (await browser.newContext({ viewport: { width: 1280, height: 800 } })).newPage();
  page.on("pageerror", (e) => errors.push(e.message));

  // Sign-in page: name, title and footer.
  await page.goto("http://localhost:3103/signin");
  await expect(page.getByRole("heading", { name: "Welcome to Coursenav" })).toBeVisible();
  await expect(page).toHaveTitle(/Coursenav/);
  await expect(page.locator("footer.site-footer")).toHaveText(DISCLAIMER);
  await page.screenshot({ path: path.join(out, "signin-with-footer.png"), fullPage: true });
  checks.push("Sign-in: Coursenav heading, <title>, affiliation footer");

  await page.fill("#subject", "launch-synthetic");
  await page.locator('input[name="returnTo"]').evaluate((el) => { el.value = "/plan"; });
  await page.click('button[type="submit"]');
  await page.waitForURL("**/plan");

  // Header: wordmark in the sidebar, assistant button in the top bar.
  await expect(page.getByRole("heading", { name: "Your semester plan" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Coursenav home" }).first()).toBeVisible();
  await page.screenshot({ path: path.join(out, "header-desktop.png"), clip: { x: 0, y: 0, width: 1280, height: 160 } });
  checks.push("Header: Coursenav wordmark and home link");

  // Footer at the bottom of an app page.
  const siteFooter = page.locator("footer.site-footer");
  await siteFooter.scrollIntoViewIfNeeded();
  await expect(siteFooter).toHaveText(DISCLAIMER);
  await page.screenshot({ path: path.join(out, "footer-desktop.png") });
  checks.push("App page footer (desktop)");

  // Assistant panel: live counter, then over the limit.
  await page.evaluate(() => window.scrollTo(0, 0));
  const trigger = page.getByRole("button", { name: "Ask Coursenav (Ctrl+/)", exact: true });
  await expect(trigger).toHaveAttribute("data-pulse", "true"); // set client-side: hydrated
  await page.waitForLoadState("networkidle");
  const panel = page.getByRole("dialog", { name: "Coursenav assistant", exact: true });
  await trigger.click();
  await expect(panel).toBeVisible({ timeout: 60000 });
  const settle = () => panel.evaluate((el) => Promise.all(el.getAnimations({ subtree: true }).map((a) => a.finished)));
  const input = panel.getByLabel("Message the assistant");
  const send = panel.getByRole("button", { name: "Send", exact: true });
  await expect(panel.locator("#assistant-input-count")).toHaveText("0/250");
  await input.fill("Can I move Calc II to the fall and still graduate on time? I'd also like something online for Arts and Humanities.");
  await expect(panel.locator("#assistant-input-count")).toHaveText("114/250");
  await expect(send).toBeEnabled();
  await settle();
  await page.screenshot({ path: path.join(out, "assistant-panel-counter.png") });
  await input.fill("x".repeat(270));
  await expect(panel.locator("#assistant-input-count")).toHaveText("20 over the 250-character limit");
  await expect(send).toBeDisabled();
  await page.screenshot({ path: path.join(out, "assistant-panel-over-limit.png") });
  checks.push("Panel counter 114/250 with Send enabled; 270 chars -> '20 over', Send disabled");

  // Phone: the footer clears the fixed tab bar.
  await page.keyboard.press("Escape");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  const box = await siteFooter.locator("p").boundingBox();
  const bar = await page.locator('nav[aria-label="Primary"]').last().boundingBox();
  const fab = await page.getByRole("button", { name: "Open the assistant" }).boundingBox();
  assert.ok(box && bar && box.y + box.height <= bar.y, "footer text sits above the tab bar");
  assert.ok(fab && box.y + box.height <= fab.y, "footer text sits above the floating Ask button");
  await page.screenshot({ path: path.join(out, "footer-phone.png") });
  checks.push("Phone footer clears the fixed tab bar");

  assert.deepEqual(errors, []);
  await writeFile(path.join(out, "results.json"), JSON.stringify({ checks, errors }, null, 2));
  console.log(`${checks.length} checks passed; screenshots in ${out}`);
} catch (error) {
  for (const p of browser?.contexts().flatMap((c) => c.pages()) ?? []) {
    await p.screenshot({ path: path.join(out, "failure.png") }).catch(() => {});
    const body = await p.locator("body").innerText().catch(() => "");
    await writeFile(path.join(out, "failure.txt"), [String(error), ...errors, body].join("\n")).catch(() => {});
  }
  throw error;
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
