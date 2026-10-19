import { type NextRequest } from "next/server";

import { authConfig } from "@/lib/auth/config";
import { SESSION_COOKIE, readSession } from "@/lib/auth/session";

export const dynamic = "force-dynamic";

/**
 * The browser's ONLY path to the CoursePilot API (Phase 7.1).
 *
 *   /api/cp/<path>  ->  <COURSEPILOT_API_URL>/<path>
 *
 * The credential comes from the sealed session cookie and nowhere else: an
 * Authorization header or cookie sent by the page is DROPPED, so script can
 * neither choose nor see the credential. The request body is passed through
 * unchanged - which identity it acts for is decided by the API from the
 * credential (bodies that name an owner are a 422 there).
 */
const SEGMENT = /^[A-Za-z0-9._~-]+$/;
const FORWARDED_REQUEST_HEADERS = ["content-type", "accept"];
const FORWARDED_RESPONSE_HEADERS = ["content-type", "x-request-id", "retry-after"];

function json(status: number, code: string, message: string) {
  return Response.json({ detail: message, error: { code, message, request_id: null } },
                       { status });
}

async function proxy(request: NextRequest, { params }: { params: Promise<{ path: string[] }> }) {
  const config = authConfig();
  if (config.mode === "disabled") return json(503, "authentication_error", "Sign-in is unavailable.");
  const session = await readSession(request.cookies.get(SESSION_COOKIE)?.value,
                                    config.sessionSecret);
  if (!session) return json(401, "authentication_error", "Authentication required.");

  const { path } = await params;
  if (!path.length || path.some((s) => !SEGMENT.test(s) || s === "." || s === "..")) {
    return json(404, "not_found", "Not found.");
  }
  const target = `${config.apiBase}/${path.join("/")}${request.nextUrl.search}`;
  const headers = new Headers({ authorization: `Bearer ${session.credential}` });
  for (const name of FORWARDED_REQUEST_HEADERS) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  const hasBody = !["GET", "HEAD"].includes(request.method);
  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: request.method,
      headers,
      body: hasBody ? await request.arrayBuffer() : undefined,
      cache: "no-store",
      redirect: "manual",
    });
  } catch {
    return json(502, "internal_error", "The CoursePilot service is unreachable.");
  }
  const out = new Headers();
  for (const name of FORWARDED_RESPONSE_HEADERS) {
    const value = upstream.headers.get(name);
    if (value) out.set(name, value);
  }
  return new Response(upstream.body, { status: upstream.status, headers: out });
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
