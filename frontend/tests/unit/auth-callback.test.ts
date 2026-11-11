/**
 * Signing in with more than one account, and every way a callback can fail.
 * A failed callback is ALWAYS a redirect to the sign-in page with a reason
 * (which offers "Try again") - never a bare 500. Fabricated identities only.
 */
import { decodeJwt } from "jose";
import { NextRequest } from "next/server";
import { renderToStaticMarkup } from "react-dom/server";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { authConfig } from "@/lib/auth/config";
import { beginSignIn, resetDiscoveryCache } from "@/lib/auth/oidc";
import { SESSION_COOKIE, TXN_COOKIE, readSession, seal } from "@/lib/auth/session";

import { MockOidc } from "./mock-oidc";

const SECRET = "y".repeat(40);
const APP = "http://localhost:3000";

let idp: MockOidc;
beforeAll(async () => { idp = await new MockOidc().start(); });
afterAll(() => idp.stop());
beforeEach(() => {
  resetDiscoveryCache();
  const env = { NODE_ENV: "test", AUTH_MODE: "oidc", OIDC_ISSUER: idp.issuer, OIDC_CLIENT_ID: idp.clientId,
    OIDC_CLIENT_SECRET: idp.clientSecret, APP_URL: APP, SESSION_SECRET: SECRET, AUTH_PROVIDER_NAME: "Google" };
  for (const [k, v] of Object.entries(env)) vi.stubEnv(k, v);
});
afterEach(() => vi.unstubAllEnvs());

/** Start a sign-in as `sub`; returns the callback request the provider would send back. */
async function attempt(sub: string, cookies: Record<string, string> = {}, claims: Record<string, unknown> = {}) {
  const cfg = authConfig();
  if (cfg.mode !== "oidc") throw new Error(cfg.mode);
  const { url, txn } = await beginSignIn(cfg, "/home");
  const code = idp.approve(url, { sub, ...claims });
  const txnCookie = await seal(txn, SECRET, Math.floor(Date.now() / 1000) + 600);
  const jar = { ...cookies, [TXN_COOKIE]: txnCookie };
  const header = Object.entries(jar).map(([k, v]) => `${k}=${v}`).join("; ");
  return { request: () => new NextRequest(`${APP}/auth/callback?code=${code}&state=${txn.state}`,
                                          { headers: { cookie: header } }),
           code, state: txn.state, txnCookie };
}

async function callback(req: NextRequest) {
  const { GET } = await import("@/app/auth/callback/route");
  return GET(req);
}

const where = (res: Response) => new URL(res.headers.get("location")!);
const sessionSub = async (res: { cookies: { get(n: string): { value: string } | undefined } }) => {
  const c = res.cookies.get(SESSION_COOKIE);
  const s = c ? await readSession(c.value, SECRET) : null;
  return s ? decodeJwt(s.credential).sub : null;
};

describe("signing in", () => {
  it("a brand-new account gets a session and goes on to the app", async () => {
    const res = await callback((await attempt("brand-new-1")).request());
    expect(res.status).toBe(303);
    expect(where(res).pathname).toBe("/home");          // the app layout then routes a new account to setup
    expect(await sessionSub(res)).toBe("brand-new-1");
    expect(res.cookies.get(TXN_COOKIE)?.value).toBe(""); // the transaction is used up
  });

  it("an existing account signs in the same way", async () => {
    await callback((await attempt("returning-1")).request());
    const res = await callback((await attempt("returning-1")).request());
    expect(where(res).pathname).toBe("/home");
    expect(await sessionSub(res)).toBe("returning-1");
  });

  it("switching to another account WITHOUT signing out replaces the session", async () => {
    const first = await callback((await attempt("account-a")).request());
    const sessionA = first.cookies.get(SESSION_COOKIE)!.value;
    const res = await callback((await attempt("account-b", { [SESSION_COOKIE]: sessionA })).request());
    expect(res.status).toBe(303);
    expect(where(res).pathname).toBe("/home");
    expect(await sessionSub(res)).toBe("account-b");
  });

  it("the same email under a different subject is simply another account", async () => {
    const res = await callback((await attempt("other-subject", {}, { email: "student@example.edu" })).request());
    expect(await sessionSub(res)).toBe("other-subject");
  });
});

describe("a failed callback is friendly, never a 500", () => {
  it("no transaction (expired, or opened in another browser) -> 'expired'", async () => {
    const res = await callback(new NextRequest(`${APP}/auth/callback?code=x&state=y`));
    expect(res.status).toBe(303);
    expect(where(res).searchParams.get("error")).toBe("expired");
    expect(res.cookies.get(SESSION_COOKIE)).toBeUndefined();
  });

  it("a forged state -> 'failed', and no session", async () => {
    const a = await attempt("forged");
    const res = await callback(new NextRequest(`${APP}/auth/callback?code=${a.code}&state=forged`,
      { headers: { cookie: `${TXN_COOKIE}=${a.txnCookie}` } }));
    expect(where(res).searchParams.get("error")).toBe("failed");
    expect(res.cookies.get(SESSION_COOKIE)).toBeUndefined();
  });

  it("a leftover state from an abandoned attempt -> 'failed', and the next attempt works", async () => {
    const abandoned = await attempt("left-over");
    const current = await attempt("left-over");
    // The abandoned attempt's redirect arrives carrying the CURRENT transaction cookie.
    const res = await callback(new NextRequest(
      `${APP}/auth/callback?code=${abandoned.code}&state=${abandoned.state}`,
      { headers: { cookie: `${TXN_COOKIE}=${current.txnCookie}` } }));
    expect(where(res).searchParams.get("error")).toBe("failed");
    expect(await sessionSub(await callback((await attempt("left-over")).request()))).toBe("left-over");
  });

  it("the callback replayed with a used code (refresh / back) -> friendly, existing session kept", async () => {
    const a = await attempt("replayed");
    const ok = await callback(a.request());
    const session = ok.cookies.get(SESSION_COOKIE)!.value;
    // The transaction cookie was deleted by the first callback: the replay has only the session.
    const replay = await callback(new NextRequest(`${APP}/auth/callback?code=${a.code}&state=${a.state}`,
      { headers: { cookie: `${SESSION_COOKIE}=${session}` } }));
    expect(replay.status).toBe(303);
    expect(where(replay).searchParams.get("error")).toBe("expired");
    expect(replay.cookies.get(SESSION_COOKIE)).toBeUndefined();   // not cleared, not replaced
    // ...and even with the transaction cookie still present, the used code is refused.
    const again = await callback(a.request());
    expect(where(again).searchParams.get("error")).toBe("failed");
  });

  it("the provider says the person cancelled -> 'cancelled'", async () => {
    const a = await attempt("cancelled");
    const res = await callback(new NextRequest(`${APP}/auth/callback?error=access_denied&state=${a.state}`,
      { headers: { cookie: `${TXN_COOKIE}=${a.txnCookie}` } }));
    expect(where(res).searchParams.get("error")).toBe("cancelled");
  });

  it("anything unexpected inside the callback -> 'failed', not a thrown 500", async () => {
    const prepared = await attempt("unexpected");
    const session = await import("@/lib/auth/session");
    const spy = vi.spyOn(session, "seal").mockRejectedValueOnce(new TypeError("boom"));
    const quiet = vi.spyOn(console, "error").mockImplementation(() => {});
    const res = await callback(prepared.request());
    expect(res.status).toBe(303);
    expect(where(res).searchParams.get("error")).toBe("failed");
    expect(quiet).toHaveBeenCalledWith("auth_callback_unexpected", "TypeError");
    spy.mockRestore(); quiet.mockRestore();
  });

  it("an exception before the token exchange (reading config/cookies) -> 'failed', not a thrown 500", async () => {
    const config = await import("@/lib/auth/config");
    const spy = vi.spyOn(config, "authConfig").mockImplementationOnce(() => { throw new RangeError("broken"); });
    const quiet = vi.spyOn(console, "error").mockImplementation(() => {});
    const res = await callback(new NextRequest(`${APP}/auth/callback?code=x&state=y`));
    expect(res.status).toBe(303);
    expect(where(res).searchParams.get("error")).toBe("failed");
    expect(quiet).toHaveBeenCalledWith("auth_callback_unexpected", "RangeError");
    spy.mockRestore(); quiet.mockRestore();
  });

  it("misconfiguration (sign-in unavailable) -> friendly, not thrown", async () => {
    vi.stubEnv("SESSION_SECRET", "short");
    const res = await callback(new NextRequest(`${APP}/auth/callback?code=x&state=y`));
    expect(where(res).searchParams.get("error")).toBe("unavailable");
  });
});

describe("a refused account (institution check)", () => {
  it("clears the session and explains what to use", async () => {
    const { GET } = await import("@/app/auth/refused/route");
    const res = await GET(new NextRequest(`${APP}/auth/refused?reason=institution`,
      { headers: { cookie: `${SESSION_COOKIE}=whatever` } }));
    expect(res.status).toBe(303);
    expect(where(res).pathname).toBe("/signin");
    expect(where(res).searchParams.get("error")).toBe("institution");
    expect(res.cookies.get(SESSION_COOKIE)?.value).toBe("");
  });

  it("an unknown reason is shown as a general refusal", async () => {
    const { GET } = await import("@/app/auth/refused/route");
    const res = await GET(new NextRequest(`${APP}/auth/refused?reason=<script>`));
    expect(where(res).searchParams.get("error")).toBe("account");
  });
});

describe("the sign-in page explains and offers Try again", () => {
  const render = async (error: string) => {
    const { default: SignInPage } = await import("@/app/signin/page");
    const el = await SignInPage({ searchParams: Promise.resolve({ error }) });
    return renderToStaticMarkup(el).replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/\s+/g, " ");
  };

  it.each(["expired", "failed", "cancelled", "provider", "account"])("'%s' offers Try again", async (code) => {
    expect(await render(code)).toContain("Try again");
  });

  it("a refused institution account is told to use the Rutgers account", async () => {
    const t = await render("institution");
    expect(t).toContain("Sign in with your Rutgers Google account");
    expect(t).toContain("Try again");
  });

  it("an unknown error code still shows a friendly message", async () => {
    expect(await render("whatever")).toContain("Sign-in couldn't be completed");
  });
});
