/**
 * OIDC Authorization Code + PKCE (S256) via oauth4webapi (Phase 7.1). SERVER-ONLY.
 *
 * Provider-agnostic: everything comes from the issuer's discovery document.
 * Checks performed on the callback (oauth4webapi, spec-conformant):
 *   state (CSRF), the authorization response's issuer (mix-up), the PKCE
 *   verifier (code injection), nonce (replay), and the ID token's
 *   iss / aud / exp / iat. The ID token arrives over TLS directly from the
 *   token endpoint (OIDC Core 3.1.3.7); the CoursePilot API then verifies its
 *   SIGNATURE against the provider's JWKS on every request.
 */

import * as oauth from "oauth4webapi";

import { OIDC_SCOPES, type AuthConfig } from "./config";
import type { AuthTransaction } from "./session";

type OidcConfig = Extract<AuthConfig, { mode: "oidc" }>;

const discovery = new Map<string, Promise<oauth.AuthorizationServer>>();

function httpOptions(config: OidcConfig) {
  return config.allowInsecureIssuer ? { [oauth.allowInsecureRequests]: true } : {};
}

export function authorizationServer(config: OidcConfig): Promise<oauth.AuthorizationServer> {
  const cacheKey = config.issuer.href;
  let as = discovery.get(cacheKey);
  if (!as) {
    as = oauth
      .discoveryRequest(config.issuer, { algorithm: "oidc", ...httpOptions(config) })
      .then((response) => oauth.processDiscoveryResponse(config.issuer, response));
    as.catch(() => discovery.delete(cacheKey));
    discovery.set(cacheKey, as);
  }
  return as;
}

function client(config: OidcConfig): oauth.Client {
  return { client_id: config.clientId };
}

function clientAuth(config: OidcConfig): oauth.ClientAuth {
  return config.clientSecret ? oauth.ClientSecretPost(config.clientSecret) : oauth.None();
}

export async function beginSignIn(
  config: OidcConfig,
  returnTo: string,
): Promise<{ url: URL; txn: AuthTransaction }> {
  const as = await authorizationServer(config);
  if (!as.authorization_endpoint) throw new Error("issuer has no authorization endpoint");
  const codeVerifier = oauth.generateRandomCodeVerifier();
  const txn: AuthTransaction = {
    state: oauth.generateRandomState(),
    nonce: oauth.generateRandomNonce(),
    codeVerifier,
    returnTo,
  };
  const url = new URL(as.authorization_endpoint);
  url.searchParams.set("client_id", config.clientId);
  url.searchParams.set("redirect_uri", config.redirectUri);
  url.searchParams.set("response_type", "code");
  url.searchParams.set("scope", OIDC_SCOPES.join(" "));
  url.searchParams.set("code_challenge", await oauth.calculatePKCECodeChallenge(codeVerifier));
  url.searchParams.set("code_challenge_method", "S256");
  url.searchParams.set("state", txn.state);
  url.searchParams.set("nonce", txn.nonce);
  return { url, txn };
}

export interface CompletedSignIn {
  idToken: string;
  exp: number;
}

export async function completeSignIn(
  config: OidcConfig,
  callbackUrl: URL,
  txn: AuthTransaction,
): Promise<CompletedSignIn> {
  const as = await authorizationServer(config);
  const c = client(config);
  const params = oauth.validateAuthResponse(as, c, callbackUrl, txn.state);
  const response = await oauth.authorizationCodeGrantRequest(
    as, c, clientAuth(config), params, config.redirectUri, txn.codeVerifier, httpOptions(config),
  );
  const result = await oauth.processAuthorizationCodeResponse(as, c, response, {
    expectedNonce: txn.nonce,
    requireIdToken: true,
  });
  const claims = oauth.getValidatedIdTokenClaims(result);
  if (!claims || !result.id_token || typeof claims.exp !== "number") {
    throw new Error("no ID token");
  }
  return { idToken: result.id_token, exp: claims.exp };
}

/** For tests: forget cached discovery documents. */
export function resetDiscoveryCache() {
  discovery.clear();
}
