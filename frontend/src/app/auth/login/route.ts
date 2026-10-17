import { NextResponse, type NextRequest } from "next/server";

import { authConfig, safeReturnTo } from "@/lib/auth/config";
import { beginSignIn } from "@/lib/auth/oidc";
import { TXN_COOKIE, TXN_MAX_AGE, cookieOptions, seal } from "@/lib/auth/session";

export const dynamic = "force-dynamic";

/** Starts sign-in: PKCE + state + nonce, then off to the identity provider. */
export async function GET(request: NextRequest) {
  const config = authConfig();
  const returnTo = safeReturnTo(request.nextUrl.searchParams.get("returnTo"));
  const signin = new URL("/signin", request.url);
  signin.searchParams.set("returnTo", returnTo);

  if (config.mode !== "oidc") {
    if (config.mode === "disabled") signin.searchParams.set("error", "unavailable");
    return NextResponse.redirect(signin, 303);
  }
  try {
    const { url, txn } = await beginSignIn(config, returnTo);
    const response = NextResponse.redirect(url, 303);
    const expires = Math.floor(Date.now() / 1000) + TXN_MAX_AGE;
    response.cookies.set(TXN_COOKIE, await seal(txn, config.sessionSecret, expires),
                         cookieOptions(config.secureCookies, TXN_MAX_AGE));
    return response;
  } catch (error) {
    // The provider is unreachable or misconfigured. Never echo its error.
    console.error("auth_login_failed", (error as Error)?.name);
    signin.searchParams.set("error", "provider");
    return NextResponse.redirect(signin, 303);
  }
}
