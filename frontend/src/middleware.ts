import { NextResponse, type NextRequest } from "next/server";

import { authConfig } from "@/lib/auth/config";
import { SESSION_COOKIE, readSession } from "@/lib/auth/session";

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
  const session = config.mode === "disabled" ? null
    : await readSession(request.cookies.get(SESSION_COOKIE)?.value, config.sessionSecret);

  if (isProtected(pathname) && !session) {
    const url = new URL("/signin", request.url);
    url.searchParams.set("returnTo", `${pathname}${search}`);
    return NextResponse.redirect(url);
  }
  if (pathname === "/signin" && session) {
    return NextResponse.redirect(new URL("/home", request.url));
  }
  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!_next/|api/|auth/|brand/|icon|apple-icon|manifest|favicon).*)"],
};
