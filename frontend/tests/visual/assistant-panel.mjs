// Synthetic browser integration: no model, real record or database is used.
// A temporary copy keeps Next build output and screenshots outside OneDrive.
// node tests/visual/assistant-panel.mjs <outside-repo-output-folder>
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { cp, mkdir, mkdtemp, open, readFile, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { spawn, execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { chromium, expect as baseExpect } from "@playwright/test";

const expect = baseExpect.configure({ timeout: 20000 });
const source = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const out = path.resolve(process.argv[2] ?? "");
if (!process.argv[2] || out.toLowerCase().includes("onedrive") || out.startsWith(source)) {
  throw new Error("Output must be outside the repository and OneDrive");
}
await mkdir(out, { recursive: true });
const mirror = await mkdtemp(path.join(tmpdir(), "coursepilot-panel-ui-"));
await cp(source, mirror, { recursive: true, filter: (file) => !path.relative(source, file).split(path.sep)
  .some((part) => part === "node_modules" || part === ".next" || part.startsWith(".env")) });
await symlink(path.join(source, "node_modules"), path.join(mirror, "node_modules"), "junction");
const fixtureDir = path.join(mirror, "src/app/(app)/panel-options");
await mkdir(fixtureDir, { recursive: true });
await writeFile(path.join(fixtureDir, "page.tsx"), `"use client";
import { useState } from "react";
import { CourseOptionsSheet, usePlanChanges } from "@/features/plan/plan-changes";
export default function Fixture() {
 const [target,setTarget]=useState<{course:string;term:string}|null>(null);
 const changes=usePlanChanges();
 return <><h1>Fabricated course options</h1><button onClick={()=>setTarget({course:"01:082:105",term:"20271"})}>Course Options</button>
 <CourseOptionsSheet target={target} onClose={()=>setTarget(null)} changes={changes}/></>;
}`);

const footer = "CoursePilot is a planning aid; your advisor and Degree Navigator are authoritative.";
const blank = (extra = {}) => ({ text: "Here are your options.", intent: "guided_swap", questions: [], cards: [], proposals: [],
  sections: [], suggestions: [], activity: [], webreg_term: null, applied_summary: null, undo_available: false,
  generated_by: "template", footer, ...extra });
const cards = ["01:700:101", "01:730:105"].map((course, i) => ({ course,
  title: i ? "Introduction to Philosophy" : "Introduction to Music", credits: 3, term_code: "20271",
  description: "A fabricated catalog description for this synthetic browser fixture.", description_source: "Synthetic catalog",
  why: ["async", "fills AHp"], requirements: ["Arts and Humanities"], requirement_codes: ["AHp"], delivery: ["async"],
  workload_weight: .8, contact_hours: 3, open_status: "open", offering: "historically offered",
  replaces: "01:082:105", action_token: `choose-${i}` }));
let chats = [{ id: "chat-1", title: "New chat", archived: false, created_at: new Date().toISOString(), updated_at: new Date().toISOString() }];
const histories = new Map([["chat-1", []]]);
let remaining = 50;
let userKey = "fabricated-user-a";
const sent = [];
const server = createServer(async (req, res) => {
  const url = new URL(req.url, "http://localhost");
  const route = url.pathname.replace(/^\/api\/v1/, "");
  let body = ""; for await (const chunk of req) body += chunk;
  const data = body ? JSON.parse(body) : {};
  const send = (value, code = 200) => { res.writeHead(code, { "content-type": "application/json" }); res.end(JSON.stringify(value)); };
  if (route === "/me") return send({ sign_in_method: "dev", institution: { status: "verified" }, has_academic_profile: true, onboarding_complete: true });
  if (route === "/student/assistant" && req.method === "GET") {
    const active = url.searchParams.get("conversation_id") ?? chats[0]?.id;
    return send({ user_key: userKey, conversation_id: active, conversations: chats, enabled: true, reason: null,
      daily_limit: 50, remaining_today: remaining, suggestions: [], history: histories.get(active) ?? [], footer });
  }
  if (route === "/student/assistant/conversations" && req.method === "POST") {
    const c = { id: `chat-${Date.now()}`, title: "New chat", archived: false, created_at: new Date().toISOString(), updated_at: new Date().toISOString() };
    chats.unshift(c); histories.set(c.id, []); return send(c, 201);
  }
  if (route.startsWith("/student/assistant/conversations/")) {
    const id = route.split("/").at(-1); const c = chats.find((c) => c.id === id);
    if (!c) return send({ detail: "Conversation not found" }, 404);
    if (req.method === "PATCH") { assert.match(req.headers["content-type"] ?? "", /json/); Object.assign(c, data); return send(c); }
    chats = chats.filter((c) => c.id !== id); histories.delete(id); return send({ deleted: true });
  }
  if (route === "/student/assistant/messages") {
    sent.push(data);
    const c = chats.find((c) => c.id === data.conversation_id);
    assert.ok(c, "message uses selected conversation");
    if (c.title === "New chat") c.title = (data.text || "Show options").split(/\s+/).slice(0, 6).join(" ");
    const reply = data.answers || data.chip ? blank({ cards }) : blank({ text: "What would you like instead?", questions: [
      { id: "instead", prompt: "What should it count toward?", chips: [{ label: "Same requirement", value: "same", question: "instead" }] },
      { id: "format", prompt: "Any format preference?", chips: [{ label: "Async / online", value: "online", question: "format" }] },
    ] });
    histories.get(c.id).push({ id: `user-${Date.now()}`, role: "user", content: data.text || "Show options", reply: null },
      { id: `reply-${Date.now()}`, role: "assistant", content: reply.text, reply });
    res.writeHead(200, { "content-type": "application/x-ndjson" });
    return res.end(JSON.stringify({ type: "reply", reply, conversation_id: c.id, remaining_today: --remaining }) + "\n");
  }
  if (route === "/student/assistant/actions") {
    if (data.token.startsWith("choose")) return send({ reply: blank({ proposals: [{ title: "Review this swap", change: {}, preview_id: "preview-1",
      summary: "Art History moves out; Music fills AHp. Graduation unchanged.", diff: {}, big: true,
      reason: "second_tap", action_token: "confirm-swap" }] }) });
    return send({ reply: blank({ text: "Your confirmed swap is saved.", applied_summary: "Art History replaced by Music.", undo_available: true, webreg_term: "20271" }) });
  }
  if (route.endsWith("/undo")) return send({});
  if (route.endsWith("/webreg")) return send({ term_code: "20271", label: "Spring 2027", items: [{ course: "01:700:101", title: "Music", index_number: "12345", section_number: "01", meetings: ["Online"], open_status: "open", warnings: [] }],
    index_numbers: ["12345"], without_section: [], warnings: [], steps: ["Open WebReg", "Paste index numbers", "Submit"], webreg_url: "https://sims.rutgers.edu/webreg/", note: "CoursePilot does not register you." });
  if (route.includes("/options")) return send({ course: "01:082:105", title: "Art History", term_code: "20271", state: "planner_selected", locked: false, locked_reason: null, groups: [], requirements: ["Arts and Humanities"], move_terms: [], not_available: [], can_remove: false });
  if (route.includes("readiness")) return send({ state: "needs_academic_history" });
  if ((route.includes("planning-context") || route.endsWith("/plans/context"))) return send({ earliest_start_term: "20271", start_options: [{ term_code: "20271", label: "Spring 2027" }], latest_recorded_term: null });
  if (route.endsWith("/plans/current")) return send({ plan: null });
  if (route.includes("planning-profile")) return send({ profile: null });
  return send({});
});
await new Promise((resolve) => server.listen(18011, "127.0.0.1", resolve));
const log = await open(path.join(out, "frontend-server.log"), "w");
const child = spawn(process.execPath, [path.join(mirror, "node_modules/next/dist/bin/next"), "dev", "-p", "3101"], {
  cwd: mirror, env: { ...process.env, AUTH_MODE: "dev", COURSEPILOT_API_URL: "http://127.0.0.1:18011/api/v1", NEXT_TELEMETRY_DISABLED: "1" },
  windowsHide: true, stdio: ["ignore", log.fd, log.fd],
});
let browser;
let activePage;
const errors = [];
const checks = [];
try {
  for (let i = 0; i < 90; i++) {
    try { if ((await fetch("http://localhost:3101/signin")).ok) break; } catch {}
    await new Promise((r) => setTimeout(r, 1000));
  }
  browser = await chromium.launch({ channel: "chrome" });
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await context.newPage();
  activePage = page;
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("http://localhost:3101/signin");
  await page.fill("#subject", "panel-synthetic");
  await page.locator('input[name="returnTo"]').evaluate((el) => { el.value = "/plan"; });
  await page.click('button[type="submit"]');
  await page.waitForURL("**/plan");
  const trigger = page.getByRole("button", { name: "Ask CoursePilot (Ctrl+/)", exact: true });
  await expect(trigger).toHaveAttribute("data-pulse", "true");
  await expect(trigger).toHaveCSS("animation-name", "assistant-breathe");
  await trigger.click();
  const panel = page.getByRole("dialog", { name: "CoursePilot assistant", exact: true });
  await expect(panel).toBeVisible();
  await expect(panel).toHaveCSS("width", "400px");
  await expect(page.getByTestId("assistant-context")).toContainText("Spring 2027");
  await expect(trigger).toHaveAttribute("data-pulse", "false");
  await expect(trigger).toHaveCSS("animation-name", "none");
  checks.push("Header glow stops pulsing after first open; 400px panel receives Plan term");
  await page.reload();
  await expect(panel).toBeVisible();
  await expect(trigger).toHaveAttribute("data-pulse", "false");
  await page.keyboard.press("Escape"); await expect(panel).not.toBeVisible();
  await page.keyboard.press("Control+/"); await expect(panel).toBeVisible();
  await page.keyboard.press("Control+/"); await expect(panel).not.toBeVisible();
  checks.push("Session restoration, Escape, keyboard toggle, and per-user first-open persistence");

  await page.goto("http://localhost:3101/panel-options");
  await page.getByRole("button", { name: "Course Options", exact: true }).click();
  await expect(page.getByTestId("options-sheet")).toBeVisible();
  await page.getByRole("button", { name: "Ask CoursePilot about alternatives" }).click();
  await expect(panel).toBeVisible();
  await expect(page.getByLabel("Message the assistant")).toHaveValue("Help me pick something instead of 01:082:105");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(page.getByText("What should it count toward?", { exact: true })).toBeVisible();
  assert.equal(sent[0].context_term, "20271");
  await page.getByRole("radio", { name: "Same requirement" }).click();
  await page.getByRole("radio", { name: "Async / online" }).click();
  await page.getByRole("button", { name: "Show options", exact: true }).click();
  await expect(page.getByTestId("option-card")).toHaveCount(2);
  const carousel = page.getByRole("region", { name: "Course options" });
  assert.ok(await carousel.evaluate((el) => el.scrollWidth > el.clientWidth));
  assert.ok(await panel.evaluate((el) => el.scrollWidth <= el.clientWidth + 1));
  await page.screenshot({ path: path.join(out, "desktop-guided-swap.png") });
  await panel.getByRole("link", { name: "Expand" }).click();
  await page.waitForURL("**/assistant");
  await expect(page.getByTestId("option-card")).toHaveCount(2);
  await expect(page.getByRole("button", { name: "Choose this", exact: true }).first()).toBeVisible();
  checks.push("Options prefill, guided clarify/carousel, narrow overflow, Expand preserves live conversation and card tokens");
  await trigger.click();
  await panel.getByRole("button", { name: "Choose this", exact: true }).first().click();
  await expect(page.getByTestId("proposal-card")).toBeVisible();
  assert.ok(await panel.evaluate((el) => el.scrollWidth <= el.clientWidth + 1));
  await panel.getByRole("button", { name: "Confirm", exact: true }).click();
  await expect(panel.getByRole("button", { name: "Undo", exact: true })).toBeVisible();
  await panel.getByRole("button", { name: "Undo", exact: true }).click();
  await expect(panel.getByRole("region", { name: "Ready for WebReg" })).toBeVisible();
  await expect(panel.getByRole("button", { name: "Copy all index numbers" })).toBeVisible();
  await expect(panel.getByText(/messages left today/)).toContainText("48");
  await panel.getByRole("region", { name: "Ready for WebReg" }).scrollIntoViewIfNeeded();
  await page.screenshot({ path: path.join(out, "desktop-confirm-webreg.png") });
  checks.push("Choose this, big-effect Confirm, clickable in-dialog Undo, WebReg, daily cap counter");
  await panel.getByRole("button", { name: "History", exact: true }).click();
  await expect(page.getByRole("list", { name: "Chat history" })).toContainText("Help me pick something instead of");
  await page.getByLabel(/Chat menu:/).first().click();
  await page.getByRole("button", { name: "Rename", exact: true }).click();
  await page.getByLabel("Chat title").fill("Spring choices");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByRole("list", { name: "Chat history" })).toContainText("Spring choices");
  await page.screenshot({ path: path.join(out, "history.png") });
  await panel.getByRole("button", { name: "New chat", exact: true }).click();
  await expect(panel.getByTestId("option-card")).toHaveCount(0);
  await panel.getByRole("button", { name: "History", exact: true }).click();
  await page.getByLabel(/Chat menu: New chat/).click();
  await page.getByRole("button", { name: "Delete", exact: true }).click();
  await page.getByRole("button", { name: "Delete chat", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "Delete this chat?" })).not.toBeVisible();
  checks.push("History titles/timestamps, rename, New chat isolation and confirmed deletion");
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(panel).toHaveCSS("width", "390px");
  await expect(panel).toHaveCSS("height", "844px");
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await expect(panel.getByLabel("Message the assistant")).toBeVisible();
  await page.screenshot({ path: path.join(out, "phone-panel.png") });
  await page.getByRole("button", { name: "Close assistant", exact: true }).click();
  const pos = await trigger.boundingBox(); assert.ok(pos.x > 320 && pos.y < 20);
  userKey = "fabricated-user-b";
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("http://localhost:3101/plan");
  await expect(trigger).toHaveAttribute("data-pulse", "true");
  await expect(trigger).toHaveCSS("animation-name", "none");
  await trigger.click();
  await expect(panel).toHaveCSS("animation-name", "none");
  await expect(trigger).toHaveAttribute("data-pulse", "false");
  checks.push("Phone fullscreen/header placement; another user has independent discovery; reduced motion disables pulse and slide");
  assert.deepEqual(errors, []);
  await writeFile(path.join(out, "results.json"), JSON.stringify({ checks, errors, mirror }, null, 2));
  console.log(`${checks.length} browser scenarios passed; output: ${out}`);
} catch (error) {
  if (activePage) {
    await activePage.screenshot({ path: path.join(out, "failure.png") }).catch(() => {});
    await writeFile(path.join(out, "failure.txt"), String(error) + "\n" + errors.join("\n") + "\n" + await activePage.locator("body").innerText()).catch(() => {});
  }
  throw error;
} finally {
  await browser?.close();
  if (process.platform === "win32") {
    try { execFileSync("taskkill", ["/PID", String(child.pid), "/T", "/F"], { windowsHide: true, stdio: "ignore" }); } catch {}
  } else child.kill("SIGTERM");
  server.closeAllConnections(); server.close(); await log.close();
}
