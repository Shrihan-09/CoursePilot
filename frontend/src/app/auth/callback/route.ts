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

/** The provider sends the browser back here with a one-time code. */
export async function GET(request: NextRequest) {
  const config = authConfig();
  const signin = new URL("/signin", request.url);
  if (config.mode !== "oidc") {
    signin.searchParams.set("error", "unavailable");
    return NextResponse.redirect(signin, 303);
  }
  const txn = await unseal<AuthTransaction>(request.cookies.get(TXN_COOKIE)?.value,
                                            config.sessionSecret);
  const fail = (code: string) => {
    signin.searchParams.set("error", code);
    const response = NextResponse.redirect(signin, 303);
    response.cookies.delete(TXN_COOKIE);
    return response;
  };
  if (!txn) return fail("expired");
  if (request.nextUrl.searchParams.get("error") === "access_denied") return fail("cancelled");

  try {
    // A plain URL: oauth4webapi rejects Next.js' NextURL wrapper.
    const { idToken, exp } = await completeSignIn(config, new URL(request.url), txn);
    const now = Math.floor(Date.now() / 1000);
    const response = NextResponse.redirect(new URL(safeReturnTo(txn.returnTo), request.url), 303);
    response.cookies.delete(TXN_COOKIE);
    response.cookies.set(SESSION_COOKIE,
                         await seal({ mode: "oidc", credential: idToken, exp },
                                    config.sessionSecret, exp),
                         cookieOptions(config.secureCookies, Math.max(0, exp - now)));
    return response;
  } catch (error) {
    // State mismatch, bad nonce, provider error: one opaque outcome, and only
    // the error CLASS is logged - never a token, code or claim.
    console.error("auth_callback_failed", (error as Error)?.name);
    return fail("failed");
  }
}
