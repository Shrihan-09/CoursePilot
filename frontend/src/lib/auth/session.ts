/**
 * Sealed cookies (Phase 7.1). SERVER-ONLY.
 *
 * The session cookie holds the verified credential the API needs - an OIDC ID
 * token, or a dev credential in development - encrypted (JWE, dir + A256GCM,
 * key derived from SESSION_SECRET) and httpOnly, so page JavaScript can
 * neither read nor forge it. Nothing academic is ever stored in it.
 *
 * The session ends when the ID token expires (Google: one hour). The proxy then
 * answers 401 and the app re-runs sign-in; with an existing provider session
 * that is a redirect, not a password prompt. No refresh token is requested
 * (no offline access): identity only.
 */

import { EncryptJWT, jwtDecrypt } from "jose";

export const SESSION_COOKIE = "cp_session";
export const TXN_COOKIE = "cp_auth_txn";
export const TXN_MAX_AGE = 10 * 60;

export interface Session {
  mode: "oidc" | "dev";
  /** The bearer credential the API verifies (ID token or `dev:<subject>`). */
  credential: string;
  /** Unix seconds. */
  exp: number;
}

export interface AuthTransaction {
  state: string;
  nonce: string;
  codeVerifier: string;
  returnTo: string;
}

async function key(secret: string): Promise<Uint8Array> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(secret));
  return new Uint8Array(digest);
}

export async function seal(payload: object, secret: string, expiresAt: number): Promise<string> {
  return new EncryptJWT({ ...payload })
    .setProtectedHeader({ alg: "dir", enc: "A256GCM" })
    .setIssuedAt()
    .setExpirationTime(expiresAt)
    .encrypt(await key(secret));
}

export async function unseal<T>(token: string | undefined, secret: string): Promise<T | null> {
  if (!token) return null;
  try {
    const { payload } = await jwtDecrypt(token, await key(secret));
    return payload as T;
  } catch {
    // Tampered, expired, or sealed with another key: no session.
    return null;
  }
}

export async function readSession(
  token: string | undefined,
  secret: string,
  now = Math.floor(Date.now() / 1000),
): Promise<Session | null> {
  const s = await unseal<Session>(token, secret);
  if (!s || (s.mode !== "oidc" && s.mode !== "dev") || typeof s.credential !== "string") {
    return null;
  }
  return s.exp > now ? s : null;
}

export function cookieOptions(secure: boolean, maxAge: number) {
  return { httpOnly: true, secure, sameSite: "lax" as const, path: "/", maxAge };
}

/** Display-only claims, read from the sealed (already verified) ID token. */
export function displayIdentity(session: Session): { email: string | null } {
  if (session.mode === "dev") return { email: session.credential.replace(/^dev:/, "") };
  try {
    // Edge-safe base64url decode (middleware imports this module).
    const part = (session.credential.split(".")[1] ?? "").replace(/-/g, "+").replace(/_/g, "/");
    const bytes = Uint8Array.from(atob(part.padEnd(Math.ceil(part.length / 4) * 4, "=")),
                                  (ch) => ch.charCodeAt(0));
    const json = JSON.parse(new TextDecoder().decode(bytes));
    return { email: typeof json.email === "string" ? json.email : null };
  } catch {
    return { email: null };
  }
}
