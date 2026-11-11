// Several accounts in one browser, end to end: browser -> web (OIDC client) ->
// mock provider -> web -> real API (PostgreSQL *_test only).
//
//   node tests/e2e/signin-accounts.mjs <outside-repo-output-folder> <postgres-url-ending-in-_test> [institution]
//
// With "institution", the API requires a Google hosted domain (hd) of
// rutgers.edu (AUTH_REQUIRE_INSTITUTION); without it the check is off.
//
// Scenarios: a brand-new account, the same account again, switching to a
// brand-new account WITHOUT signing out, a leftover state from an abandoned
// attempt, the callback replayed (refresh / back), an email reused under a
// different subject, and a forged / expired state. Every response is checked
// for a 5xx; the servers' logs are reduced to exception names and stack frames
// (never codes, tokens, cookies or emails).
import assert from "node:assert/strict";
import { spawn, execFileSync } from "node:child_process";
import { cp, lstat, mkdir, mkdtemp, open, readFile, rm, rmdir, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { randomBytes } from "node:crypto";
import { chromium } from "@playwright/test";
import { jwtDecrypt, decodeJwt } from "jose";

const here = path.dirname(fileURLToPath(import.meta.url));
const frontend = path.resolve(here, "../..");
const repo = path.resolve(frontend, "..");
const out = path.resolve(process.argv[2] ?? "");
const database = process.argv[3] ?? "";
if (!process.argv[2] || out.toLowerCase().includes("onedrive") || out.startsWith(repo)) {
  throw new Error("Output must be outside the repository and OneDrive");
}
if (!/\/[A-Za-z0-9_]+_test$/.test(database)) throw new Error("Give a PostgreSQL URL whose database ends in _test");
await mkdir(out, { recursive: true });

const IDP = 4031, API = 8031, WEB = 3131;
const SESSION_SECRET = randomBytes(32).toString("hex");
const dbName = database.split("/").pop();
const institution = process.argv[4] === "institution";
const issuer = `http://127.0.0.1:${IDP}`;
const web = `http://localhost:${WEB}`;
const procs = [];
const logs = {};

async function start(name, cmd, args, opts) {
  logs[name] = path.join(out, `${name}.log`);
  const fh = await open(logs[name], "w");
  const child = spawn(cmd, args, { ...opts, windowsHide: true, stdio: ["ignore", fh.fd, fh.fd] });
  procs.push({ child, fh });
  return child;
}
async function waitFor(url, tries = 150) {
  for (let i = 0; i < tries; i++) {
    try { const r = await fetch(url); if (r.status < 500) return; } catch {}
    await new Promise((r) => setTimeout(r, 1000));
  }
  throw new Error(`not up: ${new URL(url).origin}`);
}

// The web app runs from a copy, so it never shares .next with a running dev server.
const mirror = await mkdtemp(path.join(tmpdir(), "coursepilot-signin-"));
await cp(frontend, mirror, { recursive: true, filter: (f) => !path.relative(frontend, f).split(path.sep)
  .some((p) => p === "node_modules" || p === ".next" || p.startsWith(".env")) });
const modules = path.join(mirror, "node_modules");
await symlink(path.join(frontend, "node_modules"), modules, "junction");

const results = [];
const allHops = [];
let browser;
try {
  await start("idp", process.execPath, [path.join(here, "mock-idp.mjs"), String(IDP)], { cwd: frontend });
  const py = process.env.BACKEND_PYTHON
    || path.join(repo, "backend", ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
  await start("api", py, ["-m", "uvicorn", "app.main:app", "--port", String(API), "--app-dir", path.join(repo, "backend")], {
    cwd: path.join(repo, "backend"),
    env: { ...process.env, COURSEPILOT_ENV: "local", AUTH_PROVIDER: "oidc", DEV_AUTH_ENABLED: "false",
      OIDC_ISSUER: issuer, OIDC_AUDIENCE: "coursepilot-web-e2e", OIDC_JWKS_URI: `${issuer}/jwks`,
      DATABASE_URL: database, DATABASE_URL_SYNC: database, TEST_DATABASE_URL: database,
      LLM_PROVIDER: "echo", ASSISTANT_PROVIDER: "fake", EXPLANATION_PROVIDER: "none", OPENAI_API_KEY: "",
      RATE_LIMIT_ENABLED: "false", PYTHONIOENCODING: "utf-8",
      AUTH_REQUIRE_INSTITUTION: institution ? "true" : "false", AUTH_INSTITUTION_DOMAINS: "rutgers.edu",
      AUTH_INSTITUTION_CLAIM: "hd" },
  });
  await start("web", process.execPath, [path.join(mirror, "node_modules/next/dist/bin/next"), "dev", "-p", String(WEB)], {
    cwd: mirror,
    env: { ...process.env, AUTH_MODE: "oidc", OIDC_ISSUER: issuer, OIDC_CLIENT_ID: "coursepilot-web-e2e",
      OIDC_CLIENT_SECRET: "e2e-client-secret", APP_URL: web, SESSION_SECRET,
      COURSEPILOT_API_URL: `http://127.0.0.1:${API}/api/v1`, NEXT_TELEMETRY_DISABLED: "1" },
  });
  await waitFor(`${issuer}/.well-known/openid-configuration`);
  await waitFor(`http://127.0.0.1:${API}/api/v1/health`);
  await waitFor(`${web}/signin`);

  browser = await chromium.launch({ channel: "chrome" });
  const run = Date.now().toString(36);
  const user = (n, hd = institution ? "rutgers.edu" : "") =>
    ({ sub: `e2e-${run}-${n}`, email: `student.${n}.${run}@example.edu`, hd });
  const as = (u) => fetch(`${issuer}/next-user?sub=${encodeURIComponent(u.sub)}&email=${encodeURIComponent(u.email)}`
                          + `&hd=${encodeURIComponent(u.hd ?? "")}`);

  /** Sign in through the real redirects; returns where the browser ends up and every 5xx seen. */
  async function signIn(page, u, returnTo = "/home") {
    await as(u);
    const failures = [];
    const seen = (r) => { if (r.status() >= 500) failures.push(`${r.status()} ${new URL(r.url()).pathname}`); };
    page.on("response", seen);
    let callback = null;
    const grab = (req) => { if (new URL(req.url()).pathname === "/auth/callback") callback = req.url(); };
    page.on("request", grab);
    const hops = [];
    const hop = (req) => { if (req.isNavigationRequest()) hops.push(new URL(req.url()).origin + new URL(req.url()).pathname); };
    page.on("request", hop);
    try {
      await page.goto(`${web}/auth/login?returnTo=${encodeURIComponent(returnTo)}`, { waitUntil: "networkidle" });
    } catch (e) {
      failures.push(`navigation: ${String(e.message).split(/\r?\n/)[0].replace(/\?[^"\s]*/g, "")}`);
    }
    page.off("response", seen); page.off("request", grab); page.off("request", hop);
    if (failures.length) failures.push(`hops: ${hops.join(" > ")}`);
    allHops.push(hops.join(" > "));
    return { path: new URL(page.url()).pathname, search: new URL(page.url()).search, failures, callback,
             body: (await page.locator("body").innerText()).slice(0, 400) };
  }
  async function me(page) {
    const r = await page.request.get(`${web}/api/cp/me`);
    return { status: r.status() };
  }
  /** Whose session this browser holds: the sealed cookie, opened with the test's own secret. */
  async function sessionSub(ctx) {
    const c = (await ctx.cookies()).find((x) => x.name === "cp_session");
    if (!c) return null;
    const key = new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(SESSION_SECRET)));
    const { payload } = await jwtDecrypt(c.value, key);
    return decodeJwt(payload.credential).sub ?? null;
  }
  /** How many accounts exist for a subject (the test database only). */
  function accounts(sub) {
    return Number(execFileSync("docker", ["exec", "coursepilot-db", "psql", "-U", "coursepilot", "-d", dbName, "-tAc",
      `select count(*) from user_account where external_subject = '${sub.replace(/'/g, "")}'`]).toString().trim());
  }
  const record = (name, ok, detail) => { results.push({ name, ok, ...detail }); console.log(`${ok ? "PASS" : "FAIL"} ${name}`); };

  // 1. A brand-new account lands in the setup wizard.
  const ctxA = await browser.newContext();
  const pA = await ctxA.newPage();
  const A = user("a");
  const a1 = await signIn(pA, A);
  const meA = await me(pA);
  record("brand-new account signs in and lands in setup", a1.failures.length === 0 && a1.path === "/onboarding"
         && meA.status === 200 && (await sessionSub(ctxA)) === A.sub && accounts(A.sub) === 1,
         { landed: a1.path, failures: a1.failures, me: meA.status });

  // 2. The same account again (signed out first): no new account, no error.
  await pA.goto(`${web}/auth/logout`, { waitUntil: "networkidle" }).catch(() => {});
  await ctxA.clearCookies();
  const a2 = await signIn(pA, A);
  const meA2 = await me(pA);
  record("existing account signs in again", a2.failures.length === 0 && meA2.status === 200
         && (await sessionSub(ctxA)) === A.sub && accounts(A.sub) === 1, { landed: a2.path, failures: a2.failures });

  // 3. Switch to a brand-new account B WITHOUT signing out (A's session cookie present).
  const B = user("b");
  const b1 = await signIn(pA, B);
  const meB = await me(pA);
  const subB = await sessionSub(ctxA);
  record("switching to a brand-new account without signing out", b1.failures.length === 0 && b1.path === "/onboarding"
         && meB.status === 200 && subB === B.sub && accounts(B.sub) === 1,
         { landed: b1.path, failures: b1.failures, me: meB.status, sessionIsB: subB === B.sub, body: b1.failures.length ? b1.body : undefined });

  // 4. The callback replayed (refresh / back button) with the same code.
  const replay = [];
  pA.on("response", (r) => { if (r.status() >= 500) replay.push(`${r.status()} ${new URL(r.url()).pathname}`); });
  await pA.goto(b1.callback, { waitUntil: "networkidle" });
  const meAfterReplay = await me(pA);
  record("callback replayed with a used code", replay.length === 0 && !/Internal Server Error/.test(await pA.locator("body").innerText()),
         { landed: new URL(pA.url()).pathname + new URL(pA.url()).search.replace(/(code|state)=[^&]+/g, "$1=…"),
           failures: replay, stillB: (await sessionSub(ctxA)) === B.sub, me: meAfterReplay.status });
  pA.removeAllListeners("response");

  // 5. A leftover state from an abandoned attempt: attempt 1's provider redirect
  //    completes after attempt 2 replaced the transaction cookie.
  const ctxC = await browser.newContext();
  const pC = await ctxC.newPage();
  const C = user("c");
  await as(C);
  const first = await pC.request.get(`${web}/auth/login?returnTo=/home`, { maxRedirects: 0 });
  const authorize1 = first.headers()["location"];
  await pC.request.get(`${web}/auth/login?returnTo=/home`, { maxRedirects: 0 });   // attempt 2: new txn cookie
  const stale = [];
  pC.on("response", (r) => { if (r.status() >= 500) stale.push(`${r.status()} ${new URL(r.url()).pathname}`); });
  await pC.goto(authorize1, { waitUntil: "networkidle" });
  const staleBody = await pC.locator("body").innerText();
  record("leftover state from an abandoned attempt", stale.length === 0 && /Try again/i.test(staleBody),
         { landed: new URL(pC.url()).pathname, failures: stale, friendly: /Try again/i.test(staleBody) });
  //    ...and a fresh attempt afterwards still works.
  const c2 = await signIn(pC, C);
  record("signing in after a failed attempt works", c2.failures.length === 0 && c2.path === "/onboarding",
         { landed: c2.path, failures: c2.failures });

  // 6. An email already used by another account, under a different subject.
  const ctxD = await browser.newContext();
  const pD = await ctxD.newPage();
  const d1 = await signIn(pD, { sub: `e2e-${run}-d`, email: A.email, hd: A.hd });
  record("same email, different subject: a separate new account", d1.failures.length === 0 && d1.path === "/onboarding"
         && accounts(`e2e-${run}-d`) === 1 && accounts(A.sub) === 1, { landed: d1.path, failures: d1.failures });

  // 7. Institution membership from the signed hd claim: a personal account
  //    (no hd) and another domain; with the check ON they get a friendly
  //    "use your institution account" page (never a 500), with it OFF they sign in.
  for (const [label, hd] of [["no hd claim (personal account)", ""], ["hd=gmail.com", "gmail.com"],
                             ["hd=rutgers.edu", "rutgers.edu"]]) {
    const ctx = await browser.newContext();
    const pg = await ctx.newPage();
    const r = await signIn(pg, user(`inst-${hd || "none"}`, hd));
    const body = await pg.locator("body").innerText();
    const allowed = !institution || hd === "rutgers.edu";
    const ok = r.failures.length === 0 && !/Internal Server Error/.test(body) && (allowed
      ? r.path === "/onboarding"
      : /institution|Rutgers/i.test(body) && /Try again|another account|Sign out/i.test(body));
    record(`institution ${institution ? "ON" : "OFF"}: ${label}`, ok,
           { landed: r.path, failures: r.failures, refusedFriendly: !allowed ? /institution|Rutgers/i.test(body) : undefined,
             text: ok ? undefined : body.replace(/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+/g, "<email>").slice(0, 300) });
    if (!allowed) await pg.screenshot({ path: path.join(out, `institution-refused-${hd || "none"}.png`) });
    await ctx.close();
  }

  // 8. A forged / expired state with no transaction cookie.
  const ctxE = await browser.newContext();
  const pE = await ctxE.newPage();
  const forged = [];
  pE.on("response", (r) => { if (r.status() >= 500) forged.push(`${r.status()} ${new URL(r.url()).pathname}`); });
  await pE.goto(`${web}/auth/callback?code=forged&state=forged`, { waitUntil: "networkidle" });
  const forgedBody = await pE.locator("body").innerText();
  record("forged or expired state", forged.length === 0 && /Try again/i.test(forgedBody),
         { landed: new URL(pE.url()).pathname, failures: forged, friendly: /Try again/i.test(forgedBody) });
  await pE.screenshot({ path: path.join(out, "failed-callback.png") });
  await pA.goto(`${web}/onboarding`, { waitUntil: "networkidle" });
  await pA.screenshot({ path: path.join(out, "new-account-setup.png") });
} finally {
  await browser?.close();
  for (const { child } of procs) {
    if (process.platform === "win32") {
      try { execFileSync("taskkill", ["/PID", String(child.pid), "/T", "/F"], { windowsHide: true, stdio: "ignore" }); } catch {}
    } else child.kill("SIGTERM");
  }
  for (const { fh } of procs) await fh.close().catch(() => {});
  // Exception names and stack frames only - never codes, tokens, cookies or emails.
  const errors = {};
  for (const [name, file] of Object.entries(logs)) {
    const text = await readFile(file, "utf-8").catch(() => "");
    errors[name] = text.split(/\r?\n/)
      .filter((l) => /Error|Exception|Traceback|^\s+at |^\s+File "|⨯|unhandled/i.test(l))
      .map((l) => l.replace(/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+/g, "<email>")
                  .replace(/eyJ[\w-]+\.[\w-]+(\.[\w-]+)?/g, "<token>")
                  .replace(/(code|state|nonce|token|cookie|secret)=[^\s&"]+/gi, "$1=<redacted>"))
      .slice(0, 80);
  }
  await writeFile(path.join(out, "results.json"), JSON.stringify({ institution, results, hops: allHops, errors }, null, 2));
  if ((await lstat(modules).catch(() => null))?.isSymbolicLink()) await rmdir(modules);
  assert.equal(await lstat(modules).catch(() => null), null, "junction removed before deleting the copy");
  await rm(mirror, { recursive: true, force: true, maxRetries: 3 }).catch(() => {});
}
const failed = results.filter((r) => !r.ok);
console.log(`${results.length - failed.length}/${results.length} scenarios passed; details in ${out}`);
process.exit(failed.length ? 1 : 0);
