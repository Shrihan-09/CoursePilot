import { NextResponse, type NextRequest } from "next/server";

import { authConfig } from "@/lib/auth/config";
import { SESSION_COOKIE, configuredSession } from "@/lib/auth/session";

/**
 * Protected areas redirect to /signin (with a same-origin returnTo) when there
 * is no valid session. This is navigation UX; the AUTHORITY is the API, which
 * verifies the credential on every request via the /api/cp proxy.
 */
export const PROTECTED_PREFIXES = [
  "/home", "/onboarding", "/settings", "/degree", "/plan", "/schedule", "/courses",
  "/notifications", "/assistant", "/profile",
];

export function isProtected(pathname: string): boolean {
  return PROTECTED_PREFIXES.some((p) => pathname === p || pathname.startsWith(`${p}/`));
}

export async function middleware(request: NextRequest) {
  const { pathname, search } = request.nextUrl;
  const config = authConfig();
  const session = await configuredSession(request.cookies.get(SESSION_COOKIE)?.value, config);

  if (isProtected(pathname) && !session) {
    const url = new URL("/signin", request.url);
    url.searchParams.set("returnTo", `${pathname}${search}`);
    const response = NextResponse.redirect(url);
    if (request.cookies.has(SESSION_COOKIE)) response.cookies.delete(SESSION_COOKIE);
    return response;
  }
  // A sealed cookie does not prove the API accepts its credential. /signin
  // checks /me before forwarding a returning user; failures must stay there.
  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!_next/|api/|auth/|brand/|icon|apple-icon|manifest|favicon).*)"],
};
