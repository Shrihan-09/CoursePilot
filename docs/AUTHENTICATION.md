# Authentication and trust boundaries

CoursePilot keeps three questions separate:

| | The question | Answered by | What it grants |
|---|---|---|---|
| **1. Authentication** | *Who is this CoursePilot user?* | An OIDC identity provider (Phase 7.1) | A CoursePilot account. Nothing academic. |
| **2. CoursePilot academic data** | *What academic information has this student explicitly given CoursePilot?* | The student: an uploaded transcript they review and confirm, or courses they enter (Phase 7.0) | The student's own record, used by CoursePilot's deterministic engines |
| **3. Institutional authorization** | *What Rutgers data or actions has the student explicitly authorized through an official integration?* | **Nothing today.** No official Rutgers integration exists. | Nothing |

Signing in answers question 1 and nothing else. In particular:

- signing in with a Rutgers-associated account imports no academic data;
- it grants no access to any Rutgers system;
- it never makes CoursePilot an agent of the student at Rutgers.

---

## 1. Authentication: "Who is this CoursePilot user?"

### The flow

```
browser ──► CoursePilot web (Next.js server = confidential OIDC client)
                │  Authorization Code + PKCE (S256) + state + nonce
                ▼
          identity provider (e.g. Google)  ◄── the person signs in THERE
                │  one-time code
                ▼
          CoursePilot web exchanges the code (client secret + PKCE verifier)
                │  ID token, stored in an ENCRYPTED, httpOnly, SameSite=Lax cookie
                ▼
browser ──► /api/cp/*  (same-origin proxy: adds the ID token as Bearer)
                ▼
          CoursePilot API verifies the token AGAIN, on every request:
          signature (provider JWKS) · iss · aud · exp · iat · sub
                ▼
          account = (provider, ISSUER, subject)   ← created on first sign-in
```

**Why a server-side client, not a browser-only one:**

- Google's token endpoint requires the client secret for web clients.
- A token that page JavaScript never sees cannot be stolen by script. This is
  the "backend for frontend" pattern from *OAuth 2.0 for Browser-Based Apps*.
- The proxy drops any `Authorization` header or cookie the page sends. The
  credential comes only from the sealed cookie.

### Least privilege

The only scopes requested are `openid email`, and a test asserts that exact
list.

| Scope | Why |
|---|---|
| `openid` | Who the user is: a stable `sub` for the issuer. |
| `email` | The provider's verified-email assertion. With Google this comes with the signed `hd` (hosted domain) claim used for institution checks. |

CoursePilot does **not** request:

- `profile`;
- offline access or refresh tokens;
- Gmail, Drive, Calendar or contacts;
- anything Rutgers.

After verification the API keeps only `hd` and `email_verified`, and only for
the institution policy. It does not keep the email address: people are
identified by `(issuer, sub)`, and no address is stored.

### Accounts are provider-agnostic

`user_account` is keyed by `(identity_provider, identity_issuer, external_subject)`.
A subject is unique only within its issuer, so a provider can be added or
changed later without one provider's user landing on another provider's
account. The native iOS client will use the same OIDC flow, as a public
client with PKCE, against the same API.

### Sessions

- The session lasts as long as the ID token (Google: 1 hour).
- After that the API answers 401 and the app runs sign-in again. With an
  existing provider session that is a redirect, not a password prompt.
- Signing out deletes the CoursePilot session. It does not sign the person
  out of their provider.

### Fails closed

- **Web:** with no or incomplete configuration, or `AUTH_MODE=dev` in
  production, every sign-in route refuses. Production also requires an
  `https` issuer.
- **API:** with no configured verifier, every credential gets 401. Dev
  credentials are refused in production. **There is no fallback to dev
  sign-in.**

### Development sign-in

`AUTH_MODE=dev` (web) with `AUTH_PROVIDER=dev` and `DEV_AUTH_ENABLED=true` (API)
lets anyone sign in under any name. It is for local development and tests
only, it says so on screen, and both halves refuse it in production.

### Rutgers email: what has, and has not, been verified

**Investigated, from public sources:**

- **ScarletMail is Google Workspace for Education.** Students sign in to it
  through Rutgers CAS with NetID and Duo, so CoursePilot never sees a Rutgers
  password.
- **Google documents the `hd` claim** as the value to check for domain
  restrictions, and warns against using the email domain. CoursePilot
  follows that: membership is decided from the signed `hd` claim only, never
  from an email address.
- **ScarletMail accounts are kept by alumni.** A ScarletMail identity
  therefore proves Rutgers affiliation, **not current enrollment**.

**Exercised in local development:** one ScarletMail-backed Google account
signed in successfully through the real Google flow (see "Verified, and how").
That shows a ScarletMail identity can use "Continue with Google". It does not
establish an institution rule.

**Not verified:**

- No public source states the `hd` value a ScarletMail identity carries, and
  the real sign-in deliberately did not read it. CoursePilot stores no `hd`
  value.
- One account would not be enough evidence for a rule covering every Rutgers
  identity in any case.
- So `AUTH_INSTITUTION_DOMAINS` is empty by default, and the button names the
  actual provider: **"Continue with Google"**.
- Signing in with Google never establishes current Rutgers enrollment.

**To enable "Continue with Rutgers email" correctly:**

1. Sign in once with a consenting ScarletMail account, and read the `hd` claim
   of its verified ID token.
2. Set `AUTH_INSTITUTION_DOMAINS=<that value>` on the API.
3. Optionally set `AUTH_REQUIRE_INSTITUTION=true`. Non-members are then refused
   before any account is created.
4. Set `AUTH_SIGN_IN_LABEL="Continue with Rutgers email"` on the web app.

Even then, the UI must not claim enrollment.

### CoursePilot never

- asks for, stores or proxies a Rutgers password;
- automates CAS, WebReg, Degree Navigator or any Rutgers sign-in;
- scrapes an authenticated Rutgers session;
- treats signing in as permission to read anything from Rutgers or Google.

### Configuration

| Web (`frontend`) | API (`backend`) |
|---|---|
| `AUTH_MODE=oidc` | `AUTH_PROVIDER=oidc` |
| `OIDC_ISSUER` (https) | `OIDC_ISSUER` (same) |
| `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET` | `OIDC_AUDIENCE` (= the client id) |
| `APP_URL` (redirect URI = `APP_URL/auth/callback`) | `OIDC_JWKS_URI` |
| `SESSION_SECRET` (≥ 32 chars) | `AUTH_INSTITUTION_DOMAINS`, `AUTH_REQUIRE_INSTITUTION` (optional) |
| `AUTH_PROVIDER_NAME`, `AUTH_SIGN_IN_LABEL` (optional) | |
| `COURSEPILOT_API_URL` | |

### Verified, and how

| Claim | Evidence |
|---|---|
| The code + PKCE flow, state, nonce, issuer and PKCE checks | vitest against a real local OIDC provider (`frontend/tests/unit`) |
| The browser → provider → web → **real API** chain, the cookie not readable by script, a page-supplied credential ignored, sign-out | End to end in Chrome against `tests/e2e/mock-idp.mjs` (Phase 7.1 report) |
| Required claims, issuer-scoped accounts, the institution policy, `/me` exposing no identifiers, production refusing dev sign-in | pytest on real PostgreSQL (`backend/tests/test_auth_identity.py`) |
| Each security property is actually tested | `scripts/inject_auth_failures.py` |
| **Real Google OIDC, local development** (a ScarletMail-backed Google account) | Manual end-to-end sign-in against a registered Google web client. Google asked only for identity (`openid email`; no Gmail, Drive, Calendar, Contacts or Rutgers access). The callback, strict issuer and audience checks, account bootstrap, authenticated API calls, refresh and navigation, and sign-out all worked. `cp_session` was not readable from page script, protected routes redirected to sign-in after sign-out, and a second sign-in from a fresh browser profile mapped to the same account. |
| **Production Google configuration** | **Not done.** It needs a production OAuth client (or production redirect URIs), an `https` `APP_URL` (which also makes the cookies `Secure`), and secrets held outside the repository. |
| **The ScarletMail `hd` value; any institution rule** | **Not established** (see above). |

---

## 2. CoursePilot academic data: "What has the student explicitly provided?"

Only two paths write academic history (DATA_MODEL §46):

- **Upload:** the student uploads their unofficial transcript. It is extracted
  into **staging**, reviewed and corrected by the student, and written only on
  explicit confirmation.
- **Manual entry:** the student enters courses one at a time.

Signing in writes none of it. The provider's identity claims never become
academic facts.

## 3. Institutional authorization (future): "What has the student authorized Rutgers to share or do?"

**Not built, and not simulated.** If Rutgers ever offers an official, supported
integration, it would be a **separate, explicit authorization**: its own consent
step and its own scoped credential, never implied by signing in.

**Read path (if ever offered):** the student authorizes, then an official
integration returns the specifically permitted data, and that data enters
CoursePilot as a reviewed import.

**Action path, for example "move my class to the later section":**

| Today | With an official integration (future) |
|---|---|
| 1. The student asks. | 1. The student asks. |
| 2. CoursePilot's deterministic engines (eligibility, schedule) propose a valid change. | 2. CoursePilot's engines validate the exact action. |
| 3. CoursePilot shows the section and index details. | 3. CoursePilot shows that **exact** action. |
| 4. **The student registers through Rutgers.** | 4. The student explicitly confirms it. |
| | 5. The official integration performs it. |
| | 6. CoursePilot verifies the result. |

What keeps that future possible without building it now:

- accounts are not tied to one provider;
- authentication grants nothing beyond identity;
- academic data has its own explicit, auditable entry points.
