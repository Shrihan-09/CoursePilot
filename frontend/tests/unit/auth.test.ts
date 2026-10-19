/**
 * Phase 7.1 web authentication: configuration, least privilege, sessions, the
 * API proxy, protected routes, and the full Authorization Code + PKCE flow
 * against a REAL local OIDC provider (tests/unit/mock-oidc.ts).
 */
import { NextRequest } from "next/server";
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { OIDC_SCOPES, authConfig, safeReturnTo } from "@/lib/auth/config";
import { beginSignIn, completeSignIn, resetDiscoveryCache } from "@/lib/auth/oidc";
import { SESSION_COOKIE, TXN_COOKIE, readSession, seal, unseal } from "@/lib/auth/session";

import { MockOidc } from "./mock-oidc";

const SECRET = "x".repeat(40);
const APP = "http://localhost:3000";

function oidcEnv(idp: MockOidc, over: Record<string, string> = {}) {
  return {
    NODE_ENV: "test", AUTH_MODE: "oidc", OIDC_ISSUER: idp.issuer, OIDC_CLIENT_ID: idp.clientId,
    OIDC_CLIENT_SECRET: idp.clientSecret, APP_URL: APP, SESSION_SECRET: SECRET,
    AUTH_PROVIDER_NAME: "Google", ...over,
  };
}

let idp: MockOidc;
beforeAll(async () => {
  idp = await new MockOidc().start();
});
afterAll(() => idp.stop());
beforeEach(() => resetDiscoveryCache());

// ------------------------------------------------------------------ config

describe("configuration fails closed", () => {
  it("never allows development sign-in in production", () => {
    expect(authConfig({ NODE_ENV: "production", AUTH_MODE: "dev" }).mode).toBe("disabled");
    expect(authConfig({ NODE_ENV: "development", AUTH_MODE: "dev" }).mode).toBe("dev");
  });

  it("refuses missing or weak OIDC configuration instead of falling back", () => {
    expect(authConfig({ NODE_ENV: "production", AUTH_MODE: "oidc" }).mode).toBe("disabled");
    expect(authConfig({ ...oidcEnv(idp), SESSION_SECRET: "short" }).mode).toBe("disabled");
    expect(authConfig({ NODE_ENV: "production" }).mode).toBe("disabled");    // nothing set
  });

  it.each(["OIDC_ISSUER", "OIDC_CLIENT_ID", "APP_URL", "SESSION_SECRET"])(
    "fails closed when only %s is missing (no half-configured sign-in)",
    (name) => {
      const env: Record<string, string | undefined> = { ...oidcEnv(idp) };
      delete env[name];
      expect(authConfig(env).mode).toBe("disabled");
    },
  );

  it("requires an https issuer in production", () => {
    const cfg = authConfig({ ...oidcEnv(idp), NODE_ENV: "production" });
    expect(cfg.mode).toBe("disabled");
  });

  it("names the actual provider unless an institution label is configured", () => {
    const cfg = authConfig(oidcEnv(idp));
    expect(cfg.mode === "oidc" && cfg.signInLabel).toBe("Continue with Google");
  });
});

describe("least privilege", () => {
  it("requests exactly openid + email - no profile, offline, Gmail, Drive, Calendar or Rutgers scope", () => {
    expect([...OIDC_SCOPES]).toEqual(["openid", "email"]);
  });

  it("the authorization request carries only those scopes, PKCE S256, state and nonce", async () => {
    const cfg = authConfig(oidcEnv(idp));
    if (cfg.mode !== "oidc") throw new Error(cfg.mode);
    const { url, txn } = await beginSignIn(cfg, "/home");
    const p = url.searchParams;
    expect(p.get("scope")).toBe("openid email");
    expect(p.get("response_type")).toBe("code");
    expect(p.get("code_challenge_method")).toBe("S256");
    expect(p.get("code_challenge")).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(p.get("state")).toBe(txn.state);
    expect(p.get("nonce")).toBe(txn.nonce);
    expect(p.get("redirect_uri")).toBe(`${APP}/auth/callback`);
    expect(p.has("access_type")).toBe(false);          // no offline access / refresh token
    expect(p.has("prompt")).toBe(false);
    expect(url.href).not.toContain(txn.codeVerifier);  // the verifier never leaves the server
  });
});

describe("open redirects", () => {
  it.each([
    ["/home", "/home"], ["/onboarding?x=1", "/onboarding?x=1"], ["//evil.example", "/home"],
    ["https://evil.example", "/home"], ["/\\evil.example", "/home"], ["", "/home"],
    [null, "/home"], ["javascript:alert(1)", "/home"],
  ])("%s -> %s", (input, out) => {
    expect(safeReturnTo(input as string | null)).toBe(out);
  });
});

// ---------------------------------------------------------------- sessions

describe("sealed session cookies", () => {
  it("round-trips, and rejects tampering, another key, and expiry", async () => {
    const exp = Math.floor(Date.now() / 1000) + 600;
    const token = await seal({ mode: "oidc", credential: "abc.def.ghi", exp }, SECRET, exp);
    expect((await readSession(token, SECRET))?.credential).toBe("abc.def.ghi");
    expect(token).not.toContain("abc.def.ghi");                 // encrypted, not just signed
    expect(await readSession(`${token.slice(0, -4)}AAAA`, SECRET)).toBeNull();
    expect(await readSession(token, "y".repeat(40))).toBeNull();
    expect(await readSession(token, SECRET, exp + 1)).toBeNull();
    expect(await unseal(undefined, SECRET)).toBeNull();
  });
});

// ------------------------------------------------- full flow (real provider)

async function runSignIn(over: Record<string, string> = {}, claims?: Record<string, unknown>) {
  const cfg = authConfig(oidcEnv(idp, over));
  if (cfg.mode !== "oidc") throw new Error(cfg.mode);
  const { url, txn } = await beginSignIn(cfg, "/onboarding");
  const code = idp.approve(url, claims);
  const callback = new URL(`${APP}/auth/callback`);
  callback.searchParams.set("code", code);
  callback.searchParams.set("state", txn.state);
  return { cfg, txn, callback };
}

describe("Authorization Code + PKCE against a real provider", () => {
  it("completes, sending the PKCE verifier and client secret to the token endpoint", async () => {
    const { cfg, txn, callback } = await runSignIn();
    const done = await completeSignIn(cfg, callback, txn);
    expect(done.idToken.split(".")).toHaveLength(3);
    const sent = idp.tokenRequests.at(-1)!;
    expect(sent.get("code_verifier")).toBe(txn.codeVerifier);
    expect(sent.get("grant_type")).toBe("authorization_code");
  });

  it("rejects a callback whose state was not the one issued (CSRF)", async () => {
    const { cfg, txn, callback } = await runSignIn();
    callback.searchParams.set("state", "forged");
    await expect(completeSignIn(cfg, callback, txn)).rejects.toThrow();
  });

  it("rejects a code replayed with another transaction's verifier (PKCE)", async () => {
    const { cfg, txn, callback } = await runSignIn();
    await expect(completeSignIn(cfg, callback, { ...txn, codeVerifier: "a".repeat(64) }))
      .rejects.toThrow();
  });

  it("rejects an ID token whose nonce doesn't match (replay)", async () => {
    const { cfg, txn, callback } = await runSignIn();
    await expect(completeSignIn(cfg, callback, { ...txn, nonce: "other" })).rejects.toThrow();
  });

  it("rejects an ID token from another issuer", async () => {
    idp.overrideIss = "https://evil.example";
    try {
      const { cfg, txn, callback } = await runSignIn();
      await expect(completeSignIn(cfg, callback, txn)).rejects.toThrow();
    } finally {
      idp.overrideIss = null;
    }
  });
});

// --------------------------------------------- route handlers end to end

describe("route handlers", () => {
  it("login sets a sealed transaction cookie and redirects to the provider", async () => {
    vi.stubEnv("AUTH_MODE", "oidc");
    for (const [k, v] of Object.entries(oidcEnv(idp))) vi.stubEnv(k, v);
    const { GET } = await import("@/app/auth/login/route");
    const res = await GET(new NextRequest(`${APP}/auth/login?returnTo=//evil.example`));
    expect(res.status).toBe(303);
    const location = new URL(res.headers.get("location")!);
    expect(location.origin).toBe(idp.issuer);
    const txnCookie = res.cookies.get(TXN_COOKIE);
    expect(txnCookie?.httpOnly).toBe(true);
    const txn = await unseal<{ returnTo: string; state: string }>(txnCookie!.value, SECRET);
    expect(txn?.returnTo).toBe("/home");                     // the open redirect was refused
    expect(txn?.state).toBe(location.searchParams.get("state"));

    // ...and the callback turns the provider's code into an httpOnly session.
    const code = idp.approve(location);
    const { GET: callback } = await import("@/app/auth/callback/route");
    const cb = new NextRequest(`${APP}/auth/callback?code=${code}&state=${txn!.state}`,
                               { headers: { cookie: `${TXN_COOKIE}=${txnCookie!.value}` } });
    const done = await callback(cb);
    expect(done.status).toBe(303);
    expect(new URL(done.headers.get("location")!).pathname).toBe("/home");
    const session = done.cookies.get(SESSION_COOKIE);
    expect(session?.httpOnly).toBe(true);
    expect(session?.sameSite).toBe("lax");
    expect((await readSession(session!.value, SECRET))?.mode).toBe("oidc");
    vi.unstubAllEnvs();
  });

  it("callback without its transaction cookie fails closed", async () => {
    for (const [k, v] of Object.entries(oidcEnv(idp))) vi.stubEnv(k, v);
    const { GET } = await import("@/app/auth/callback/route");
    const res = await GET(new NextRequest(`${APP}/auth/callback?code=x&state=y`));
    expect(new URL(res.headers.get("location")!).searchParams.get("error")).toBe("expired");
    expect(res.cookies.get(SESSION_COOKIE)).toBeUndefined();
    vi.unstubAllEnvs();
  });

  it("development sign-in does not exist in production", async () => {
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("AUTH_MODE", "dev");
    const { POST } = await import("@/app/auth/dev/route");
    const form = new FormData();
    form.set("subject", "anyone");
    const res = await POST(new NextRequest(`${APP}/auth/dev`, { method: "POST", body: form }));
    expect(res.status).toBe(404);
    expect(res.cookies.get(SESSION_COOKIE)).toBeUndefined();
    vi.unstubAllEnvs();
  });

  it("logout deletes the session", async () => {
    const { POST } = await import("@/app/auth/logout/route");
    const res = await POST(new NextRequest(`${APP}/auth/logout`, { method: "POST" }));
    expect(res.cookies.get(SESSION_COOKIE)?.value).toBe("");
    expect(new URL(res.headers.get("location")!).pathname).toBe("/signin");
  });
});

describe("protected routes", () => {
  it("redirect to sign-in with a same-origin returnTo, and let a valid session through", async () => {
    vi.stubEnv("NODE_ENV", "development");
    vi.stubEnv("AUTH_MODE", "dev");
    vi.stubEnv("SESSION_SECRET", SECRET);
    const { middleware } = await import("@/middleware");
    const out = await middleware(new NextRequest(`${APP}/onboarding?step=2`));
    const loc = new URL(out.headers.get("location")!);
    expect(loc.pathname).toBe("/signin");
    expect(loc.searchParams.get("returnTo")).toBe("/onboarding?step=2");

    const exp = Math.floor(Date.now() / 1000) + 600;
    const cookie = await seal({ mode: "dev", credential: "dev:s", exp }, SECRET, exp);
    const ok = await middleware(new NextRequest(`${APP}/home`,
                                                { headers: { cookie: `${SESSION_COOKIE}=${cookie}` } }));
    expect(ok.headers.get("location")).toBeNull();
    vi.unstubAllEnvs();
  });
});

// ------------------------------------------------------------- the proxy

describe("API proxy", () => {
  it("uses ONLY the sealed credential, drops page-supplied credentials, refuses traversal", async () => {
    vi.stubEnv("NODE_ENV", "development");
    vi.stubEnv("AUTH_MODE", "dev");
    vi.stubEnv("SESSION_SECRET", SECRET);
    vi.stubEnv("COURSEPILOT_API_URL", "http://api.test/api/v1");
    const seen: { url: string; headers: Headers; body: string | null }[] = [];
    const fetchMock = vi.fn(async (url: string, init: RequestInit) => {
      seen.push({ url, headers: new Headers(init.headers),
                  body: init.body ? new TextDecoder().decode(init.body as ArrayBuffer) : null });
      return new Response(JSON.stringify({ ok: true }), {
        status: 200, headers: { "content-type": "application/json", "x-request-id": "r1",
                                "set-cookie": "evil=1" } });
    });
    vi.stubGlobal("fetch", fetchMock);
    const { POST, GET } = await import("@/app/api/cp/[...path]/route");
    const ctx = (path: string[]) => ({ params: Promise.resolve({ path }) });

    // No session: 401, the API is never called.
    const anon = await GET(new NextRequest(`${APP}/api/cp/me`), ctx(["me"]));
    expect(anon.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();

    const exp = Math.floor(Date.now() / 1000) + 600;
    const cookie = await seal({ mode: "dev", credential: "dev:me", exp }, SECRET, exp);
    const res = await POST(
      new NextRequest(`${APP}/api/cp/student/profile`, {
        method: "POST",
        headers: { cookie: `${SESSION_COOKIE}=${cookie}`, authorization: "Bearer dev:someone-else",
                   "content-type": "application/json" },
        body: JSON.stringify({ program_key: "sas-198-ba", catalog_year: "2026-2027",
                               student_id: "someone-elses" }),
      }),
      ctx(["student", "profile"]),
    );
    expect(res.status).toBe(200);
    expect(res.headers.get("set-cookie")).toBeNull();          // API cookies never reach the page
    const call = seen.at(-1)!;
    expect(call.url).toBe("http://api.test/api/v1/student/profile");
    expect(call.headers.get("authorization")).toBe("Bearer dev:me");   // NOT the page's header
    expect(call.headers.get("cookie")).toBeNull();
    // The body is passed through untouched: the API rejects owner fields (422).
    expect(call.body).toContain("student_id");

    for (const bad of [["..", "admin"], ["student", "..", "x"], ["a%2Fb"]]) {
      const r = await GET(new NextRequest(`${APP}/api/cp/x`,
                                          { headers: { cookie: `${SESSION_COOKIE}=${cookie}` } }),
                          ctx(bad));
      expect(r.status).toBe(404);
    }
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
  });
});
