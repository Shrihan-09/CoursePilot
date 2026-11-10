import { NextResponse, type NextRequest } from "next/server";

import { safeReturnTo } from "@/lib/auth/config";
import { SESSION_COOKIE, TXN_COOKIE } from "@/lib/auth/session";

export const dynamic = "force-dynamic";

/** One terminal sign-in screen after an API rejection; never auto-login. */
export async function GET(request: NextRequest) {
  const url = new URL("/signin", request.url);
  url.searchParams.set("error", "session");
  url.searchParams.set("returnTo", safeReturnTo(request.nextUrl.searchParams.get("returnTo")));
  const response = NextResponse.redirect(url, 303);
  response.cookies.delete(SESSION_COOKIE);
  response.cookies.delete(TXN_COOKIE);
  return response;
}
