import { NextResponse, type NextRequest } from "next/server";

import { authConfig, safeReturnTo } from "@/lib/auth/config";
import { SESSION_COOKIE, cookieOptions, seal } from "@/lib/auth/session";

export const dynamic = "force-dynamic";

const SUBJECT = /^[A-Za-z0-9._@-]{1,64}$/;
const DEV_SESSION_SECONDS = 8 * 60 * 60;

/**
 * DEVELOPMENT SIGN-IN. Exists only when AUTH_MODE=dev outside production
 * (authConfig refuses it in production), and the API accepts the resulting
 * `dev:` credential only with DEV_AUTH_ENABLED outside production. Not
 * authentication: anyone can type any name. For local work and tests only.
 */
export async function POST(request: NextRequest) {
  const config = authConfig();
  if (config.mode !== "dev") return new NextResponse(null, { status: 404 });
  const form = await request.formData();
  const subject = String(form.get("subject") ?? "").trim();
  const returnTo = safeReturnTo(String(form.get("returnTo") ?? ""));
  if (!SUBJECT.test(subject)) {
    const back = new URL("/signin", request.url);
    back.searchParams.set("error", "dev-subject");
    return NextResponse.redirect(back, 303);
  }
  // Check the API's configured verifier before issuing a browser session.
  // A frontend-only dev switch cannot enable development auth on an OIDC API.
  let error: string | null = null;
  try {
    const check = await fetch(`${config.apiBase}/me`, {
      headers: { authorization: `Bearer dev:${subject}`, accept: "application/json" },
      cache: "no-store", redirect: "manual", signal: AbortSignal.timeout(10_000),
    });
    if (!check.ok) error = check.status === 401 || check.status === 403 ? "session" : "service";
  } catch {
    error = "service";
  }
  if (error) {
    const back = new URL("/signin", request.url);
    back.searchParams.set("error", error);
    back.searchParams.set("returnTo", returnTo);
    const response = NextResponse.redirect(back, 303);
    response.cookies.delete(SESSION_COOKIE);
    return response;
  }
  const exp = Math.floor(Date.now() / 1000) + DEV_SESSION_SECONDS;
  const response = NextResponse.redirect(new URL(returnTo, request.url), 303);
  response.cookies.set(SESSION_COOKIE,
                       await seal({ mode: "dev", credential: `dev:${subject}`, exp },
                                  config.sessionSecret, exp),
                       cookieOptions(false, DEV_SESSION_SECONDS));
  return response;
}
