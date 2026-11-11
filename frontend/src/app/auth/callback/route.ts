import { NextResponse, type NextRequest } from "next/server";

import { authConfig, safeReturnTo } from "@/lib/auth/config";
import { completeSignIn } from "@/lib/auth/oidc";
import {
  SESSION_COOKIE,
  TXN_COOKIE,
  type AuthTransaction,
  cookieOptions,
  seal,
  unseal,
} from "@/lib/auth/session";

export const dynamic = "force-dynamic";

/** Back to the sign-in page with a friendly reason (it offers "Try again"). */
function fail(request: NextRequest, code: string) {
  const signin = new URL("/signin", request.url);
  signin.searchParams.set("error", code);
  const response = NextResponse.redirect(signin, 303);
  response.cookies.delete(TXN_COOKIE);
  return response;
}

/**
 * The provider sends the browser back here with a one-time code.
 *
 * Never a bare 500: EVERY failure - a missing or expired transaction, a
 * replayed code, a state from an abandoned attempt, a provider error, or
 * anything unexpected - becomes a redirect to the sign-in page with a reason
 * and a "Try again". Only the error CLASS is logged, never a code, token,
 * cookie or claim. A new sign-in replaces any existing session, so signing in
 * as another account without signing out first is just a sign-in.
 */
export async function GET(request: NextRequest) {
  try {
    return await handle(request);
  } catch (error) {
    console.error("auth_callback_unexpected", (error as Error)?.name ?? typeof error);
    return fail(request, "failed");
  }
}

async function handle(request: NextRequest) {
  const config = authConfig();
  if (config.mode !== "oidc") return fail(request, "unavailable");
  const txn = await unseal<AuthTransaction>(request.cookies.get(TXN_COOKIE)?.value,
                                            config.sessionSecret);
  if (!txn) return fail(request, "expired");
  if (request.nextUrl.searchParams.get("error") === "access_denied") return fail(request, "cancelled");

  let signedIn: { idToken: string; exp: number };
  try {
    // A plain URL: oauth4webapi rejects Next.js' NextURL wrapper.
    signedIn = await completeSignIn(config, new URL(request.url), txn);
  } catch (error) {
    // State mismatch, bad nonce, a used code, provider error: one opaque outcome.
    console.error("auth_callback_failed", (error as Error)?.name ?? typeof error);
    return fail(request, "failed");
  }
  const now = Math.floor(Date.now() / 1000);
  const response = NextResponse.redirect(new URL(safeReturnTo(txn.returnTo), request.url), 303);
  response.cookies.delete(TXN_COOKIE);
  response.cookies.set(SESSION_COOKIE,
                       await seal({ mode: "oidc", credential: signedIn.idToken, exp: signedIn.exp },
                                  config.sessionSecret, signedIn.exp),
                       cookieOptions(config.secureCookies, Math.max(0, signedIn.exp - now)));
  return response;
}
