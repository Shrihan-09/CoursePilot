import { NextResponse, type NextRequest } from "next/server";

import { SESSION_COOKIE, TXN_COOKIE } from "@/lib/auth/session";

export const dynamic = "force-dynamic";

/** Reasons the API can refuse a verified sign-in; anything else is "account". */
const REASONS = new Set(["institution", "account"]);

/**
 * The API refused this signed-in account (403: not an accepted institution
 * account). The session is cleared - keeping it would leave the person in an
 * app that refuses every request - and the sign-in page explains what to use.
 * GET, because it is reached by a server-side redirect from a page.
 */
export async function GET(request: NextRequest) {
  const reason = request.nextUrl.searchParams.get("reason") ?? "";
  const signin = new URL("/signin", request.url);
  signin.searchParams.set("error", REASONS.has(reason) ? reason : "account");
  const response = NextResponse.redirect(signin, 303);
  response.cookies.delete(SESSION_COOKIE);
  response.cookies.delete(TXN_COOKIE);
  return response;
}
