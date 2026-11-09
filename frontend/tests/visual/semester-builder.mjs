// Synthetic UI integration. No student DB, model or real record is accessed.
// Runs an isolated Next mirror; all logs, build output and screenshots go to temp.
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { cp, mkdir, mkdtemp, open, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { spawn, execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { chromium, expect as rawExpect } from "@playwright/test";
const expect = rawExpect.configure({ timeout: 20000 });
const source = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const out = path.resolve(process.argv[2] ?? "");
if (!process.argv[2] || out.toLowerCase().includes("onedrive") || out.startsWith(source)) throw new Error("Output must be outside repo/OneDrive");
await mkdir(out, { recursive: true });
const mirror = await mkdtemp(path.join(tmpdir(), "coursepilot-builder-ui-"));
await cp(source, mirror, { recursive: true, filter: (file) => !path.relative(source, file).split(path.sep).some((p) => p === "node_modules" || p === ".next" || p.startsWith(".env")) });
await symlink(path.join(source, "node_modules"), path.join(mirror, "node_modules"), "junction");

const makeCourse = (course, title, system, role = "filler") => ({ course, title, credits: "3", term_code: "20271", reasons: [], role,
  requirements: system ? [{ requirement_code: system === "major" ? "CS_ELECTIVES" : "CORE_HST", requirement_name: system === "major" ? "CS elective" : "Historical Analysis", requirement_system: system, provisional: false }] : [],
  prerequisite: { state: "satisfied_by_history", conditional_on: [], unknown_reasons: [], raw_text: null },
  offering: { kind: "confirmed_in_term", target_term: "20271", observed_terms: [], same_season_terms: [] },
  workload: { weight: "4", credits: "3", delivery: ["async"], components: {}, contact_hours: "3" }, notes: [] });
const courses = [makeCourse("01:640:152", "Calculus II", "major", "anchor"), makeCourse("01:198:210", "Data Management for Data Science", "major", "major"), makeCourse("01:510:101", "World History", "core")];
const term = { term_code: "20271", label: "Spring 2027", credits: "9", free_elective_credits: "3", courses,
  workload: { level: "balanced", total: "12", budget: "24", contact_hours: "9", credit_floor: "12", credit_max: "18", notes: [], major_related: 2, fit: "Why this fits you: room for your job and two major courses." } };
let plan = { id: "synthetic-plan", revision: 1, created_at: "2026-11-09T00:00:00Z", updated_at: "2026-11-09T00:00:00Z", start_term: "20271",
  freshness: { state: "current", reasons: [] }, constraints: { max_credits_per_term: "18", max_courses_per_term: 6 }, edits: { locked_terms: [] },
  course_states: courses.map((c) => ({ course: c.course, term_code: "20271", state: c.role === "anchor" ? "locked" : "planner_selected", locked_reason: c.role === "anchor" ? "anchor" : null })),
  needs_reselection: [], last_change: null, blocked_courses: [], result: { status: "covers_all_requirements", terms: [term, { ...term, term_code: "20279", label: "Fall 2027", courses: [] }], issues: [], requirements: [], assumptions: [], observations: [], metadata: { planning_engine_version: "synthetic" } } };
const option = (i) => ({ course: `01:730:10${i}`, title: `Introduction to Philosophy ${i}`, credits: "3", requirements: ["Historical Analysis"], requirement_codes: ["CORE_HST"], delivery: ["async"], offering: "confirmed", workload_weight: "3", contact_hours: "3", impact: "Keeps your plan on track." });
const choices = { term_code: "20271", questions: [
  { kind: "already_set", course: courses[0].course, title: courses[0].title, prompt: "Already set: Calculus II (retake)", groups: [], alternatives: [], recommended: [] },
  { kind: "slot", course: courses[2].course, title: courses[2].title, prompt: "Pick a Core course", alternatives: [], recommended: [], groups: [
    { kind: "same_requirement", requirement_code: "CORE_HST", requirement_name: "Historical Analysis", options: [1, 2, 3, 4, 5].map(option) },
    { kind: "different_requirement", requirement_code: "CORE_NS", requirement_name: "Natural Sciences", options: [{ ...option(6), title: "Exploring Biology" }] },
  ] },
] };
const hit = (course, title, extra = {}) => ({ course, title, credits: "3", requirements: [], requirement_codes: [], coverage: "free_elective", coverage_label: "Counts only as free elective; other requirement coverage not mapped", workload_weight: "3", delivery: ["async"], can_add: true, can_pin: false, selected: false, gate: null, open_status: "open", prerequisite: { state: "satisfied_by_history", conditional_on: [] }, recommended: false, ...extra });
const hits = [hit("01:700:101", "Music in Everyday Life"), hit("01:198:344", "Design and Analysis of Algorithms", { can_add: false, gate: "prerequisites_not_met" }), hit("01:730:101", "Introduction to Philosophy", { recommended: true, requirements: ["Historical Analysis"], requirement_codes: ["CORE_HST"], coverage_label: "Historical Analysis" })];
const footer = "CoursePilot is a planning aid; your advisor and Degree Navigator are authoritative.";
const blank = (extra = {}) => ({ text: "Here is a suggestion for your draft.", intent: "search", questions: [], cards: [], proposals: [], sections: [], suggestions: [], activity: [], webreg_term: null, generated_by: "template", footer, ...extra });
const sent = [], previews = [], applies = [], scheduleRequests = [];
let preview;
const server = createServer(async (req, res) => {
  const url = new URL(req.url, "http://localhost"); const route = url.pathname.replace(/^\/api\/v1/, "");
  let body = ""; for await (const b of req) body += b; const data = body ? JSON.parse(body) : {};
  const send = (value, code = 200) => { res.writeHead(code, { "content-type": "application/json" }); res.end(JSON.stringify(value)); };
  if (route === "/me") return send({ sign_in_method: "dev", institution: { status: "verified" }, has_academic_profile: true, onboarding_complete: true });
  if (route.endsWith("/readiness")) return send({ state: "ready" });
  if (route.endsWith("/plans/context")) return send({ earliest_start_term: "20271", start_options: [{ term_code: "20271", label: "Spring 2027" }], latest_recorded_term: "20269" });
  if (route.endsWith("/plans/current")) return send({ plan });
  if (route.endsWith("/plans/current/choices")) return send(choices);
  if (route.endsWith("/planning-profile")) return send({ profile: { schedule: { earliest_start: "09:00", latest_end: null, avoid_days: ["F"], blocked_times: [{ days: ["M"], start: "12:00", end: "13:00" }] }, honors: false }, state: "confirmed" });
  if (route.endsWith("/profile")) return send({ program: { program_name: "Computer Science" } });
  if (route.endsWith("/builder")) {
    let items = data.text ? hits.filter((c) => `${c.course} ${c.title}`.toLowerCase().includes(data.text.toLowerCase())) : hits;
    if (data.still_needed) items = items.filter((c) => c.requirement_codes.length);
    return send({ term_code: "20271", total_offered: 3, matching: items.length, eligible: items.filter((c) => c.can_add).length, gated: items.filter((c) => c.gate).length, selected: 0, items, requirements: [{ code: "CORE_HST", name: "Historical Analysis" }], membership_source: "existing_options" });
  }
  if (route === "/student/assistant") return send({ enabled: true, user_key: "synthetic-builder", conversation_id: "synthetic-chat", remaining_today: 50, daily_limit: 50, history: [], suggestions: [], conversations: [{ id: "synthetic-chat", title: "Builder chat", created_at: "2026-11-09", updated_at: "2026-11-09" }], footer });
  if (route === "/student/assistant/messages") {
    sent.push(data); const c = hits[2];
    const reply = blank({ cards: [{ ...c, term_code: "20271", description: "A synthetic course description.", why: ["Fills Historical Analysis"], offering: "confirmed", action_token: "should-not-be-used" }] });
    res.writeHead(200, { "content-type": "application/x-ndjson" }); return res.end(JSON.stringify({ type: "reply", reply, conversation_id: "synthetic-chat", remaining_today: 49 }) + "\n");
  }
  if (route.endsWith("/changes/preview")) {
    previews.push(data);
    preview = { preview_id: "synthetic-preview", base_revision: 1, valid: true, errors: [], change: data,
      diff: { summary: "Music and Philosophy added; graduation unchanged", big: false, terms: [], forced_moves: [], requirements_lost: [], requirements_gained: ["Historical Analysis"], new_issues: [], last_term_before: "20279", last_term_after: "20279" } };
    return send(preview);
  }
  if (route.endsWith("/changes/apply")) { applies.push(data); plan = { ...plan, revision: 2, last_change: "Built Spring 2027" }; return send(plan); }
  if (route.endsWith("/schedules/generate")) {
    scheduleRequests.push(data);
    if (scheduleRequests.length === 1) return send({ status: "no_valid_schedule", options: [], issues: [{ code: "NO_VALID_SCHEDULE", severity: "blocker", message: "Selected courses conflict with your Monday blocked time." }], metadata: {} });
    return send({ status: "options_found", issues: [], metadata: {}, options: [{ rank: 1, total_credits: "3", reasons: [], issues: [], days: ["T"], score: {}, choices: [{ course: "01:198:210", course_string: "01:198:210", title: "Data Management for Data Science", component: "primary", supplement_code: "", section_number: "01", index_number: "12345", instructors: ["Synthetic Instructor"], availability: { state: "open", freshness: "current" }, restriction: { outcome: "unrestricted" }, meetings: [{ kind: "timed", day: "T", start_minute: 600, end_minute: 680, campus: "CAC", campus_name: "College Avenue", mode_desc: "Lecture", building: "Synthetic Hall", room: "101" }] }] }] });
  }
  if (route === "/student/assistant/actions") throw new Error("Builder must not apply assistant action tokens");
  return send({});
});
await new Promise((r) => server.listen(18012, "127.0.0.1", r));
const log = await open(path.join(out, "frontend-server.log"), "w");
const child = spawn(process.execPath, [path.join(mirror, "node_modules/next/dist/bin/next"), "dev", "-p", "3102"], { cwd: mirror, env: { ...process.env, AUTH_MODE: "dev", COURSEPILOT_API_URL: "http://127.0.0.1:18012/api/v1", NEXT_TELEMETRY_DISABLED: "1" }, windowsHide: true, stdio: ["ignore", log.fd, log.fd] });
let browser, page; const errors = [], checks = [];
const shot = async (name) => { await page.waitForTimeout(450); await page.screenshot({ path: path.join(out, `${name}.png`), fullPage: true }); };
const noOverflow = async () => assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), "No page horizontal overflow");
try {
  for (let i = 0; i < 90; i++) { try { if ((await fetch("http://localhost:3102/signin")).ok) break; } catch {} await new Promise((r) => setTimeout(r, 1000)); }
  browser = await chromium.launch({ channel: "chrome" });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  page = await context.newPage(); page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("http://localhost:3102/signin"); await page.fill("#subject", "builder-synthetic"); await page.locator('input[name="returnTo"]').evaluate((el) => { el.value = "/plan"; }); await page.click('button[type="submit"]'); await page.waitForURL("**/plan");
  const choicesCard = page.getByTestId("your-choices"); await expect(choicesCard).toBeVisible();
  await expect(page.getByTestId("semester-section").nth(1)).not.toHaveAttribute("open", "");
  await expect(page.getByTestId("plan-course").first().locator("h4")).toHaveText("Calculus II");
  await expect(choicesCard).toContainText("CoursePilot picked World History"); await noOverflow(); await shot("plan-desktop");
  await choicesCard.getByRole("button", { name: "Change", exact: true }).click();
  const choiceDialog = page.getByRole("dialog", { name: "Change your choice", exact: true });
  await expect(choiceDialog.getByTestId("option-card")).toHaveCount(3);
  await choiceDialog.getByRole("button", { name: "Show more" }).click(); await expect(choiceDialog.getByTestId("option-card")).toHaveCount(5);
  await choiceDialog.getByRole("button", { name: "Natural Sciences", exact: true }).click(); await expect(choiceDialog).toContainText("Exploring Biology"); await shot("choice-modal");
  await page.keyboard.press("Escape"); await choicesCard.getByRole("button", { name: "Keep", exact: true }).click();
  await expect(choicesCard).toContainText("All set for Spring 2027"); await expect(choicesCard.getByRole("button", { name: "Review" })).toBeVisible();
  checks.push("A0: grouped modal, three initial options, Show more, switch groups, Keep collapses to Review");
  await page.getByRole("button", { name: "Build / change this semester", exact: true }).first().click();
  const builder = page.getByRole("dialog", { name: "Build Spring 2027", exact: true });
  await expect(builder).toBeVisible(); await builder.getByLabel("Still-needed only").uncheck(); await expect(builder.getByTestId("browser-course")).toHaveCount(3);
  const music = builder.getByTestId("browser-course").filter({ hasText: "Music in Everyday Life" }); await expect(music).toContainText("Counts only as free elective"); await music.getByRole("button", { name: "Add", exact: true }).click();
  const gated = builder.getByTestId("browser-course").filter({ hasText: "Design and Analysis" }); await expect(gated.getByRole("button", { name: "Add", exact: true })).toBeDisabled();
  await expect(builder.getByRole("region", { name: "Semester selection" }).getByText("Music in Everyday Life", { exact: true })).toBeVisible(); await noOverflow(); await shot("builder-desktop");
  await builder.getByRole("button", { name: "Auto-pick best sections" }).click(); await expect(builder).toContainText("Selected courses conflict with your Monday blocked time.");
  assert.deepEqual(scheduleRequests[0].preferences.avoid_days, ["F"]);
  assert.equal(scheduleRequests[0].preferences.blocked_times[0].start, "12:00");
  await builder.getByRole("button", { name: "Auto-pick best sections" }).click();
  await expect(builder.getByLabel("Section combination")).toBeVisible(); await shot("builder-calendar");
  checks.push("B: full browser toggle, non-requirement course add, prerequisite gate disabled, calendar conflict feedback");
  await page.keyboard.press("Control+/");
  await expect(page.getByTestId("assistant-context")).toContainText("Builder draft (4 courses)");
  await page.keyboard.press("Escape");
  await builder.getByRole("button", { name: "Ask CoursePilot about my last slot" }).click();
  const assistant = page.getByRole("dialog", { name: "CoursePilot assistant", exact: true }); await expect(assistant).toBeVisible();
  await page.getByRole("button", { name: "Send", exact: true }).click(); await expect(assistant.getByRole("button", { name: "Add to semester" })).toBeVisible();
  assert.equal(sent[0].context_term, "20271"); assert.deepEqual(sent[0].builder_selection, [...courses.map((c) => c.course), "01:700:101"]);
  assert.equal(applies.length, 0); await shot("builder-assistant");
  await assistant.getByRole("button", { name: "Add to semester" }).click(); await expect(assistant).not.toBeVisible();
  await expect(builder.getByRole("region", { name: "Semester selection" })).toContainText("Introduction to Philosophy"); assert.equal(applies.length, 0);
  checks.push("Assistant side panel receives current term and unsaved selection; one-tap suggestion adds to draft without an apply call");
  await builder.getByRole("button", { name: "Preview semester change" }).click(); await expect(page.getByRole("dialog", { name: "Preview your change", exact: true })).toBeVisible();
  assert.equal(previews[0].kind, "set_term"); assert.equal(previews[0].expected_revision, 1); assert.ok(previews[0].courses.includes("01:730:101")); assert.equal(applies.length, 0); await shot("semester-preview");
  await page.getByRole("button", { name: "Confirm", exact: true }).click(); await expect.poll(() => applies.length).toBe(1);
  checks.push("Save sends one set_term preview, shows diff, writes only after Confirm");
  for (const width of [360, 768]) {
    await page.setViewportSize({ width, height: 900 }); await noOverflow(); await shot(`plan-${width}`);
    await page.getByRole("button", { name: "Build / change this semester", exact: true }).first().click(); await expect(builder).toBeVisible(); await noOverflow(); await shot(`builder-${width}`);
    await expect(builder.getByRole("button", { name: "Preview semester change" })).toBeInViewport();
    await builder.getByRole("button", { name: /^Selection \(/ }).click(); await expect(builder.getByRole("region", { name: "Semester selection" })).toBeVisible(); await shot(`builder-selection-${width}`);
    await builder.getByRole("button", { name: "Calendar", exact: true }).click(); await expect(builder.getByRole("region", { name: "Weekly preview" })).toBeVisible(); await noOverflow(); await page.keyboard.press("Escape");
  }
  await page.setViewportSize({ width: 1440, height: 1000 }); await page.evaluate(() => document.documentElement.classList.add("dark")); await shot("plan-dark");
  await page.getByRole("button", { name: "Build / change this semester", exact: true }).first().click(); await noOverflow(); await shot("builder-dark");
  checks.push("360px and tablet plan/builder have no page overflow; dark-mode views captured");
  assert.deepEqual(errors, []); await writeFile(path.join(out, "results.json"), JSON.stringify({ checks, errors, mirror }, null, 2)); console.log(`${checks.length} browser scenarios passed; ${out}`);
} catch (error) {
  if (page) { await page.screenshot({ path: path.join(out, "failure.png") }).catch(() => {}); await writeFile(path.join(out, "failure.txt"), String(error) + "\n" + errors.join("\n") + "\n" + await page.locator("body").innerText()).catch(() => {}); }
  throw error;
} finally {
  await browser?.close();
  if (process.platform === "win32") { try { execFileSync("taskkill", ["/PID", String(child.pid), "/T", "/F"], { windowsHide: true, stdio: "ignore" }); } catch {} } else child.kill("SIGTERM");
  server.closeAllConnections(); server.close(); await log.close();
}
