import { NextRequest } from "next/server";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SESSION_COOKIE, configuredSession, seal } from "@/lib/auth/session";
import { authConfig, safeReturnTo } from "@/lib/auth/config";

const state = vi.hoisted(() => ({ cookie: undefined as string | undefined }));
vi.mock("next/headers", () => ({ cookies: async () => ({
  get: () => state.cookie ? { value: state.cookie } : undefined,
}) }));
vi.mock("next/navigation", () => ({ redirect: (url: string) => { throw new Error(`redirect:${url}`); } }));

import AppLayout from "@/app/(app)/layout";
import OnboardingPage from "@/app/onboarding/page";
import SignInPage from "@/app/signin/page";
import { GET as recover } from "@/app/auth/session-error/route";
import { POST as devSignIn } from "@/app/auth/dev/route";
import { middleware } from "@/middleware";

const APP = "http://localhost:3000";
const SECRET = "local-test-secret-".repeat(3);
let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(async () => {
  vi.stubEnv("NODE_ENV", "development");
  vi.stubEnv("AUTH_MODE", "dev");
  vi.stubEnv("SESSION_SECRET", SECRET);
  vi.stubEnv("COURSEPILOT_API_URL", "http://api.test/api/v1");
  const exp = Math.floor(Date.now() / 1000) + 600;
  state.cookie = await seal({ mode: "dev", credential: "dev:existing-student", exp }, SECRET, exp);
  fetchMock = vi.fn(async () => new Response(null, { status: 401 }));
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); state.cookie = undefined; });

describe("rejected session redirect chain", () => {
  it.each(["/home", "/onboarding"])("%s reaches sign-in once even with a valid sealed but API-rejected cookie", async (path) => {
    const render = () => path === "/home" ? AppLayout({ children: null }) : OnboardingPage();
    await expect(render()).rejects.toThrow(`redirect:/auth/session-error?returnTo=${encodeURIComponent(path)}`);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    // Before clearing, middleware must not bounce sign-in back to /home.
    const stale = await middleware(new NextRequest(`${APP}/signin`, {
      headers: { cookie: `${SESSION_COOKIE}=${state.cookie}` },
    }));
    expect(stale.headers.get("location")).toBeNull();

    const response = await recover(new NextRequest(`${APP}/auth/session-error?returnTo=${encodeURIComponent(path)}`));
    expect(response.cookies.get(SESSION_COOKIE)?.value).toBe("");
    const destination = new URL(response.headers.get("location")!);
    expect(destination.pathname).toBe("/signin");
    state.cookie = undefined;
    const signin = await middleware(new NextRequest(destination));
    expect(signin.headers.get("location")).toBeNull();
    const html = renderToStaticMarkup(await SignInPage({ searchParams: Promise.resolve(Object.fromEntries(destination.searchParams)) }));
    expect(html).toContain("Your session wasn");
    expect(html).toContain("Development sign-in");
    expect(fetchMock).toHaveBeenCalledTimes(1); // no automatic retry or provider login
  });

  it("a direct sign-in visit with a rejected cookie renders the error instead of redirecting", async () => {
    const html = renderToStaticMarkup(await SignInPage({ searchParams: Promise.resolve({}) }));
    expect(html).toContain("Your session wasn");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it.each([403, 500, 502])("an account check returning %i cannot send the app or wizard into a navigation loop", async (status) => {
    fetchMock.mockResolvedValue(new Response(null, { status }));
    await expect(AppLayout({ children: null })).rejects.toThrow("couldn't check your account");
    await expect(OnboardingPage()).rejects.toThrow("couldn't check your account");
    const html = renderToStaticMarkup(await SignInPage({ searchParams: Promise.resolve({}) }));
    expect(html).toContain("check your account right now");
  });

  it("a backend outage shows recovery rather than treating setup as unfinished", async () => {
    fetchMock.mockRejectedValue(new TypeError("connection refused"));
    await expect(AppLayout({ children: null })).rejects.toThrow("couldn't check your account");
    await expect(OnboardingPage()).rejects.toThrow("couldn't check your account");
  });

  it.each([true, false])("only an accepted account selects setup vs app (complete=%s)", async (complete) => {
    fetchMock.mockImplementation(async () => Response.json({ onboarding_complete: complete, has_academic_profile: true }));
    const destination = complete ? "/home" : "/onboarding";
    await expect(SignInPage({ searchParams: Promise.resolve({}) })).rejects.toThrow(`redirect:${destination}`);
    if (complete) {
      await expect(OnboardingPage()).rejects.toThrow("redirect:/home");
      await expect(AppLayout({ children: null })).resolves.toBeTruthy();
    } else {
      await expect(AppLayout({ children: null })).rejects.toThrow("redirect:/onboarding");
      await expect(OnboardingPage()).resolves.toBeTruthy();
    }
  });

  it("changed secrets or auth modes reject and clear the old cookie", async () => {
    const config = authConfig();
    if (config.mode !== "dev") throw new Error("test config");
    expect(await configuredSession(state.cookie, { ...config, sessionSecret: "another-key" })).toBeNull();
    // The same encryption key cannot turn a dev credential into OIDC identity.
    const oidc = authConfig({ NODE_ENV: "development", AUTH_MODE: "oidc", OIDC_ISSUER: "https://idp.test",
      OIDC_CLIENT_ID: "test", APP_URL: APP, SESSION_SECRET: SECRET });
    expect(await configuredSession(state.cookie, oidc)).toBeNull();
    vi.stubEnv("SESSION_SECRET", "changed-key");
    const result = await middleware(new NextRequest(`${APP}/home`, {
      headers: { cookie: `${SESSION_COOKIE}=${state.cookie}` },
    }));
    expect(new URL(result.headers.get("location")!).pathname).toBe("/signin");
    expect(result.cookies.get(SESSION_COOKIE)?.value).toBe("");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each(["/signin", "/signin?returnTo=/home", "/auth/login", "/auth/session-error"])("refuses auth return destinations: %s", (path) => {
    expect(safeReturnTo(path)).toBe("/home");
  });
});

describe("development verifier preflight", () => {
  it.each([401, 500])("API rejection %i cannot issue a new browser session", async (status) => {
    fetchMock.mockResolvedValue(new Response(null, { status }));
    const form = new FormData(); form.set("subject", "existing-student"); form.set("returnTo", "/degree");
    const response = await devSignIn(new NextRequest(`${APP}/auth/dev`, { method: "POST", body: form }));
    const destination = new URL(response.headers.get("location")!);
    expect(destination.pathname).toBe("/signin");
    expect(destination.searchParams.get("error")).toBe(status === 401 ? "session" : "service");
    expect(response.cookies.get(SESSION_COOKIE)?.value).toBe("");
    const html = renderToStaticMarkup(await SignInPage({ searchParams: Promise.resolve(Object.fromEntries(destination.searchParams)) }));
    expect(html).toContain("Development sign-in");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("an accepting API receives the exact selected subject and the session keeps that identity", async () => {
    fetchMock.mockResolvedValue(Response.json({ has_academic_profile: true, onboarding_complete: true }));
    const form = new FormData(); form.set("subject", "existing-student"); form.set("returnTo", "/degree");
    const response = await devSignIn(new NextRequest(`${APP}/auth/dev`, { method: "POST", body: form }));
    const sent = fetchMock.mock.calls[0]?.[1] as RequestInit | undefined;
    expect(new Headers(sent?.headers).get("authorization")).toBe("Bearer dev:existing-student");
    const session = await configuredSession(response.cookies.get(SESSION_COOKIE)?.value, authConfig());
    expect(session?.credential).toBe("dev:existing-student");
    expect(new URL(response.headers.get("location")!).pathname).toBe("/degree");
  });
});

it("simultaneous browser API rejections start cookie recovery only once", async () => {
  vi.resetModules();
  const replace = vi.fn();
  vi.stubGlobal("window", { location: { pathname: "/degree", search: "", replace } });
  const { api } = await import("@/lib/api");
  await Promise.allSettled([api.me(), api.me()]);
  expect(replace).toHaveBeenCalledExactlyOnceWith("/auth/session-error?returnTo=%2Fdegree");
});
