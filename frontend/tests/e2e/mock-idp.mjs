// A local OIDC provider for the END-TO-END sign-in check (Phase 7.1).
//
//   node tests/e2e/mock-idp.mjs <port>
//
// Discovery, an /authorize endpoint that auto-approves (as if the user signed
// in at Google), a token endpoint that VERIFIES PKCE, the client secret and the
// redirect URI, RS256 ID tokens, and a JWKS the CoursePilot API verifies
// against. Test-only: it authenticates nobody.

import { createHash, randomUUID } from "node:crypto";
import { createServer } from "node:http";

import { SignJWT, exportJWK, generateKeyPair } from "jose";

const port = Number(process.argv[2] ?? 4010);
const issuer = `http://127.0.0.1:${port}`;
const CLIENT_ID = "coursepilot-web-e2e";
const CLIENT_SECRET = "e2e-client-secret";
const { privateKey, publicKey } = await generateKeyPair("RS256", { extractable: true });
const jwk = { ...(await exportJWK(publicKey)), kid: "e2e", use: "sig", alg: "RS256" };
const codes = new Map();
const requestedScopes = [];
// Who "signs in at the provider" next (several accounts in one browser).
// Set with GET /next-user?sub=...&email=... ; test-only.
let nextUser = { sub: "e2e-subject-1", email: "e2e.student@example.edu" };

const json = (res, status, body) => {
  res.writeHead(status, { "content-type": "application/json" });
  res.end(JSON.stringify(body));
};

createServer(async (req, res) => {
  const url = new URL(req.url, issuer);
  if (url.pathname === "/.well-known/openid-configuration") {
    return json(res, 200, {
      issuer, authorization_endpoint: `${issuer}/authorize`, token_endpoint: `${issuer}/token`,
      jwks_uri: `${issuer}/jwks`, response_types_supported: ["code"],
      subject_types_supported: ["public"], id_token_signing_alg_values_supported: ["RS256"],
      code_challenge_methods_supported: ["S256"],
    });
  }
  if (url.pathname === "/jwks") return json(res, 200, { keys: [jwk] });
  if (url.pathname === "/scopes") return json(res, 200, { requested: requestedScopes });
  if (url.pathname === "/next-user") {
    nextUser = { sub: url.searchParams.get("sub") ?? nextUser.sub,
                 email: url.searchParams.get("email") ?? nextUser.email,
                 // Google's hosted-domain claim; absent for personal accounts.
                 hd: url.searchParams.get("hd") || null };
    return json(res, 200, { ok: true });
  }
  if (url.pathname === "/authorize") {
    const p = url.searchParams;
    requestedScopes.push(p.get("scope"));
    if (p.get("client_id") !== CLIENT_ID || p.get("code_challenge_method") !== "S256") {
      return json(res, 400, { error: "invalid_request" });
    }
    const code = randomUUID();
    codes.set(code, { challenge: p.get("code_challenge"), nonce: p.get("nonce"),
                      redirect: p.get("redirect_uri"), user: nextUser });
    const back = new URL(p.get("redirect_uri"));
    back.searchParams.set("code", code);
    back.searchParams.set("state", p.get("state"));
    res.writeHead(302, { location: back.toString() });
    return res.end();
  }
  if (url.pathname === "/token" && req.method === "POST") {
    let raw = "";
    for await (const chunk of req) raw += chunk;
    const form = new URLSearchParams(raw);
    const issued = codes.get(form.get("code"));
    codes.delete(form.get("code"));
    if (!issued) return json(res, 400, { error: "invalid_grant" });
    const challenge = createHash("sha256").update(form.get("code_verifier") ?? "").digest("base64url");
    if (challenge !== issued.challenge || form.get("redirect_uri") !== issued.redirect) {
      return json(res, 400, { error: "invalid_grant" });
    }
    if (form.get("client_id") !== CLIENT_ID || form.get("client_secret") !== CLIENT_SECRET) {
      return json(res, 401, { error: "invalid_client" });
    }
    const idToken = await new SignJWT({ nonce: issued.nonce, email: issued.user.email,
                                        email_verified: true,
                                        ...(issued.user.hd ? { hd: issued.user.hd } : {}) })
      .setProtectedHeader({ alg: "RS256", kid: "e2e" }).setIssuer(issuer).setAudience(CLIENT_ID)
      .setSubject(issued.user.sub).setIssuedAt().setExpirationTime("1h").sign(privateKey);
    return json(res, 200, { access_token: "opaque", token_type: "Bearer", expires_in: 3600,
                            id_token: idToken });
  }
  json(res, 404, { error: "not_found" });
}).listen(port, "127.0.0.1", () => console.log(`mock idp ${issuer}`));
