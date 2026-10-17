/**
 * Authentication configuration for the CoursePilot web app (Phase 7.1).
 * SERVER-ONLY: imported by route handlers and middleware, never by client code.
 *
 *   browser -> CoursePilot web (Next.js server, a CONFIDENTIAL OIDC client)
 *           -> identity provider (Authorization Code + PKCE)
 *           -> verified ID token, kept in an encrypted httpOnly cookie
 *           -> /api/cp/* proxy adds it as the Bearer credential
 *           -> CoursePilot API verifies it again (signature, iss, aud, exp, iat, sub)
 *
 * Why a server-side client and not a browser-only PKCE client: Google's token
 * endpoint requires the client_secret for web clients, and a token the page's
 * JavaScript never sees cannot be stolen by script (OAuth 2.0 for
 * Browser-Based Apps, "backend for frontend").
 *
 * FAILS CLOSED. Production with missing configuration, or with AUTH_MODE=dev,
 * yields `{ mode: "disabled" }` - every sign-in route refuses. There is no
 * fallback to development sign-in, ever.
 */

/**
 * Least privilege: identity only. `openid` = who the user is (`sub`); `email`
 * = the verified address the identity provider attests (and, with Google,
 * the signed `hd` hosted-domain claim). NOT `profile`, not Gmail, Drive,
 * Calendar, contacts, offline access, nor anything Rutgers. Tested.
 */
export const OIDC_SCOPES = ["openid", "email"] as const;

export type AuthConfig =
  | {
      mode: "oidc";
      issuer: URL;
      clientId: string;
      clientSecret: string | null;
      redirectUri: string;
      sessionSecret: string;
      secureCookies: boolean;
      providerName: string;
      signInLabel: string;
      apiBase: string;
      allowInsecureIssuer: boolean;
    }
  | {
      mode: "dev";
      sessionSecret: string;
      secureCookies: boolean;
      signInLabel: string;
      apiBase: string;
    }
  | { mode: "disabled"; reason: string; apiBase: string };

type Env = Record<string, string | undefined>;

const MIN_SECRET = 32;

export function authConfig(env: Env = process.env): AuthConfig {
  const production = env.NODE_ENV === "production";
  const apiBase = (env.COURSEPILOT_API_URL ?? "http://localhost:8000/api/v1").replace(/\/+$/, "");
  const mode = (env.AUTH_MODE ?? "none").toLowerCase();
  const sessionSecret = env.SESSION_SECRET ?? "";
  const disabled = (reason: string): AuthConfig => ({ mode: "disabled", reason, apiBase });

  if (mode === "dev") {
    if (production) return disabled("development sign-in is refused in production");
    return {
      mode: "dev",
      // A fixed, clearly non-secret key is acceptable ONLY for local dev.
      sessionSecret: sessionSecret || "coursepilot-local-development-only-session-key!",
      secureCookies: false,
      signInLabel: "Continue (development sign-in)",
      apiBase,
    };
  }

  if (mode === "oidc") {
    const missing = ["OIDC_ISSUER", "OIDC_CLIENT_ID", "APP_URL", "SESSION_SECRET"].filter(
      (k) => !env[k],
    );
    if (missing.length) return disabled(`missing configuration: ${missing.join(", ")}`);
    if (sessionSecret.length < MIN_SECRET) return disabled("SESSION_SECRET is too short");
    let issuer: URL;
    let appUrl: URL;
    try {
      issuer = new URL(env.OIDC_ISSUER!);
      appUrl = new URL(env.APP_URL!);
    } catch {
      return disabled("OIDC_ISSUER or APP_URL is not a URL");
    }
    const insecure = issuer.protocol !== "https:";
    if (insecure && production) return disabled("OIDC_ISSUER must use https in production");
    const providerName = env.AUTH_PROVIDER_NAME || "your identity provider";
    return {
      mode: "oidc",
      issuer,
      clientId: env.OIDC_CLIENT_ID!,
      clientSecret: env.OIDC_CLIENT_SECRET || null,
      redirectUri: new URL("/auth/callback", appUrl).toString(),
      sessionSecret,
      secureCookies: appUrl.protocol === "https:",
      providerName,
      // "Continue with Rutgers email" is set here ONLY once the backend's
      // AUTH_INSTITUTION_DOMAINS has been verified from a real sign-in -
      // see docs/AUTHENTICATION.md. Default names the actual provider.
      signInLabel: env.AUTH_SIGN_IN_LABEL || `Continue with ${providerName}`,
      apiBase,
      allowInsecureIssuer: insecure && !production,
    };
  }

  return disabled("AUTH_MODE is not configured");
}

/**
 * Where to go after sign-in. ONLY a same-origin path: "/x", never "//x",
 * "https://x" or "/\\x" - an open redirect would let a link sign someone in and
 * send them to a look-alike site.
 */
export function safeReturnTo(value: string | null | undefined, fallback = "/home"): string {
  if (!value || typeof value !== "string") return fallback;
  if (!value.startsWith("/") || value.startsWith("//") || value.startsWith("/\\")) return fallback;
  if (/[\u0000-\u001f]/.test(value)) return fallback;
  return value;
}
