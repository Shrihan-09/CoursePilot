/**
 * A minimal, REAL OIDC provider for tests: discovery, an authorization-code
 * token endpoint that VERIFIES PKCE and the client secret, RS256 ID tokens.
 * Plain http on localhost (the client allows insecure issuers only outside
 * production).
 */
import { createHash, randomUUID } from "node:crypto";
import { createServer, type Server } from "node:http";
import type { AddressInfo } from "node:net";

import { SignJWT, exportJWK, generateKeyPair } from "jose";

export interface IssuedCode {
  codeChallenge: string;
  nonce: string;
  clientId: string;
  redirectUri: string;
  claims?: Record<string, unknown>;
}

export class MockOidc {
  server!: Server;
  issuer = "";
  clientId = "coursepilot-web";
  clientSecret = "test-client-secret";
  codes = new Map<string, IssuedCode>();
  tokenRequests: URLSearchParams[] = [];
  private privateKey!: CryptoKey;
  private jwk!: Record<string, unknown>;
  /** Override what the token endpoint signs (for wrong-issuer tests). */
  overrideIss: string | null = null;

  async start(): Promise<this> {
    const { privateKey, publicKey } = await generateKeyPair("RS256", { extractable: true });
    this.privateKey = privateKey as CryptoKey;
    this.jwk = { ...(await exportJWK(publicKey)), kid: "k1", use: "sig", alg: "RS256" };
    this.server = createServer((req, res) => void this.handle(req, res));
    await new Promise<void>((r) => this.server.listen(0, "127.0.0.1", r));
    this.issuer = `http://127.0.0.1:${(this.server.address() as AddressInfo).port}`;
    return this;
  }

  stop() {
    this.server.close();
  }

  /** Simulates the user approving sign-in at the provider: returns a code. */
  approve(authorizeUrl: URL, claims?: Record<string, unknown>): string {
    const code = randomUUID();
    this.codes.set(code, {
      codeChallenge: authorizeUrl.searchParams.get("code_challenge")!,
      nonce: authorizeUrl.searchParams.get("nonce")!,
      clientId: authorizeUrl.searchParams.get("client_id")!,
      redirectUri: authorizeUrl.searchParams.get("redirect_uri")!,
      claims,
    });
    return code;
  }

  private async handle(req: import("node:http").IncomingMessage, res: import("node:http").ServerResponse) {
    const url = new URL(req.url!, this.issuer);
    const send = (status: number, body: unknown) => {
      res.writeHead(status, { "content-type": "application/json" });
      res.end(JSON.stringify(body));
    };
    if (url.pathname === "/.well-known/openid-configuration") {
      return send(200, {
        issuer: this.issuer,
        authorization_endpoint: `${this.issuer}/authorize`,
        token_endpoint: `${this.issuer}/token`,
        jwks_uri: `${this.issuer}/jwks`,
        response_types_supported: ["code"],
        subject_types_supported: ["public"],
        id_token_signing_alg_values_supported: ["RS256"],
        code_challenge_methods_supported: ["S256"],
        authorization_response_iss_parameter_supported: false,
      });
    }
    if (url.pathname === "/jwks") return send(200, { keys: [this.jwk] });
    if (url.pathname === "/token" && req.method === "POST") {
      let raw = "";
      for await (const chunk of req) raw += chunk;
      const form = new URLSearchParams(raw);
      this.tokenRequests.push(form);
      const issued = this.codes.get(form.get("code") ?? "");
      if (!issued) return send(400, { error: "invalid_grant" });
      this.codes.delete(form.get("code")!);
      const verifier = form.get("code_verifier") ?? "";
      const challenge = createHash("sha256").update(verifier).digest("base64url");
      if (challenge !== issued.codeChallenge) return send(400, { error: "invalid_grant" });
      if (form.get("client_secret") !== this.clientSecret || form.get("client_id") !== issued.clientId) {
        return send(401, { error: "invalid_client" });
      }
      if (form.get("redirect_uri") !== issued.redirectUri) return send(400, { error: "invalid_grant" });
      const idToken = await new SignJWT({
        nonce: issued.nonce, email: "student@example.edu", email_verified: true, ...issued.claims,
      })
        .setProtectedHeader({ alg: "RS256", kid: "k1" })
        .setIssuer(this.overrideIss ?? this.issuer)
        .setAudience(this.clientId)
        .setSubject("subject-123")
        .setIssuedAt()
        .setExpirationTime("1h")
        .sign(this.privateKey);
      return send(200, { access_token: "opaque", token_type: "Bearer", expires_in: 3600, id_token: idToken });
    }
    send(404, { error: "not_found" });
  }
}
