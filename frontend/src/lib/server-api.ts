import "server-only";

import { cookies } from "next/headers";

import { authConfig } from "@/lib/auth/config";
import { SESSION_COOKIE, type Session, readSession } from "@/lib/auth/session";

/**
 * Server components' view of the session and the API (Phase 7.1). Same rule
 * as the proxy: the credential comes only from the sealed cookie.
 */
export async function currentSession(): Promise<Session | null> {
  const config = authConfig();
  if (config.mode === "disabled") return null;
  const store = await cookies();
  return readSession(store.get(SESSION_COOKIE)?.value, config.sessionSecret);
}

export async function serverGet<T>(path: string): Promise<{ status: number; data: T | null }> {
  const config = authConfig();
  const session = await currentSession();
  if (!session) return { status: 401, data: null };
  try {
    const response = await fetch(`${config.apiBase}${path}`, {
      headers: { authorization: `Bearer ${session.credential}`, accept: "application/json" },
      cache: "no-store",
    });
    return { status: response.status,
             data: response.ok ? ((await response.json()) as T) : null };
  } catch {
    return { status: 502, data: null };
  }
}
