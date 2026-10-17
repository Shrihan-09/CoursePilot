import { NextResponse, type NextRequest } from "next/server";

import { SESSION_COOKIE, TXN_COOKIE } from "@/lib/auth/session";

export const dynamic = "force-dynamic";

/**
 * Signs out of CoursePilot: the session cookie is deleted. It does NOT sign the
 * person out of their identity provider (their Google/Rutgers session is theirs
 * to manage); the sign-in page says so.
 */
export async function POST(request: NextRequest) {
  const response = NextResponse.redirect(new URL("/signin?signed_out=1", request.url), 303);
  response.cookies.delete(SESSION_COOKIE);
  response.cookies.delete(TXN_COOKIE);
  return response;
}
