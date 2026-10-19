# Phase 7.1: Product Shell, Authentication, Onboarding & Visual Foundation

## Summary

CoursePilot is now a student-facing web product, not only a backend:

```
Sign in (OIDC) → Welcome → Program → Transcript upload or manual entry
               → Review → Confirm → Complete → Home / Settings
```

Phase 7.1 also establishes the visual foundation that later Degree, Plan,
Schedule, AI, notification and native clients will build on:

- brand assets derived from the supplied logo;
- semantic tokens with designed light and dark themes;
- primitives, a responsive shell, and the generation motion language.

**What is verified, and how:**

- **Locally implemented and tested:** everything in this PR.
- **The OIDC sign-in is tested end to end in Chrome:**
  - the browser goes to a local OIDC provider, which verifies PKCE and the
    client secret;
  - the Next.js server exchanges the code;
  - the **real CoursePilot API** verifies the provider-issued ID token against
    the provider's JWKS.
- **Real Google OIDC: exercised in local development.** One ScarletMail-backed
  Google account signed in against a registered Google web client.
  - Google asked only for identity (`openid email`). There was no Gmail,
    Drive, Calendar, Contacts or Rutgers access.
  - These worked: the callback, strict issuer and audience checks, account
    bootstrap, authenticated API calls, refresh and navigation, and sign-out.
  - `cp_session` was unreadable from page script, and protected routes
    redirected to sign-in after sign-out.
  - A second sign-in from a fresh browser profile mapped to the **same**
    account: one OIDC account, provisioned once.
  - The `hd` claim was deliberately not read. No institution rule exists, and
    the button stays **"Continue with Google"**.
  - Nothing found needed a code change.
  - A React hydration warning seen in the regular browser profile came from
    attributes the Grammarly extension injects. It did not reproduce in a
    browser without extensions, so no CoursePilot change was made.
- **Visual QA on the rendered product:**
  - light and dark, at 320 / 390 / 820 / 1280 / 1600 px;
  - every screen audited with axe-core (WCAG 2 A/AA), an overflow check and a
    touch-target check;
  - final result: 56 screens, 0 findings.

## Authentication (identity only)

- **Architecture:** the Next.js server is a **confidential OIDC client**
  (Authorization Code + PKCE S256 + state + nonce).
  - Google requires the client secret for web clients.
  - The ID token sits in an **encrypted httpOnly cookie** that page script can
    never read.
  - `/api/cp/*` is the browser's only route to the API. It adds the credential
    itself and drops anything the page sends.
- **Least privilege:**
  - scopes are `openid email` only, asserted by a test;
  - no `profile`, no offline access, no Gmail/Drive/Calendar/contacts, nothing
    Rutgers;
  - after verification the API keeps only `hd` and `email_verified`, and **not
    the email address**.
- **Provider-agnostic accounts:** keyed `(provider, issuer, subject)`
  (migration `d498ccbced22`), so a provider can be added or changed without
  merging people. The same OIDC model serves the future native app.
- **Fails closed:**
  - production refuses dev sign-in and any missing or partial configuration;
  - an `https` issuer is required;
  - there is no fallback.
- **Rutgers email:**
  - ScarletMail is Google Workspace; sign-in goes through Rutgers CAS, so
    CoursePilot never sees the password.
  - Membership is decided from Google's **signed `hd` claim only**, never an
    email domain.
  - ScarletMail persists for alumni, so this proves affiliation, **not
    enrollment**.
  - Institution checks are off by default; `AUTH_INSTITUTION_DOMAINS` is set
    only after a real sign-in confirms the value.
  - `AUTH_REQUIRE_INSTITUTION` refuses non-members **before** any account
    exists.
- **`GET /api/v1/me`:** sign-in method, institution status and whether a
  profile exists. No identifiers.
- **Three trust boundaries:** documented separately in
  `docs/AUTHENTICATION.md`.
  - **Who you are:** answered by signing in.
  - **What you provided:** comes only from uploads and entries you confirm.
  - **Official Rutgers authorization:** none exists, and none is simulated.

## Brand

- **The source:** `logo 2.jpg`, kept byte-for-byte. `logo one.jpg` was
  byte-identical, so there is one source.
- **Transparency is extracted** (alpha from luminance against the baked-in
  black, edges un-premultiplied), not faked with CSS.
- **Derived assets:** marks at 64–1024 px, favicon, Apple touch icon, PWA icons
  (standard and maskable), and a 1024 native-app-icon source.
- No redrawing or recolouring. The only simplification is a tighter crop at
  favicon sizes (`frontend/brand/README.md`).

## Design system (docs/DESIGN_SYSTEM.md)

- **Neutral first; CoursePilot blue is sampled from the mark.**
  - `#016DF4` carries white text at 4.67:1.
  - Blue is reserved for primary actions, active and selected states,
    progress, links and focus.
- **Dark theme:** designed, with near-black and charcoal elevation steps; not
  inverted.
- **Appearance:** System (default), Light or Dark, with no flash of the wrong
  theme. The preference is stored alone in `localStorage`.
- **Type:** Geist Sans and Mono, self-hosted. Course codes and numbers use
  tabular figures.
- **Primitives:** Button, IconButton, Input, Select, SearchInput, Checkbox,
  Radio, Tabs, Badge, StatusBadge, Alert, Toast, Dialog, Sheet (native
  `<dialog>`), Tooltip, Progress, Skeleton, EmptyState, ErrorState, PageHeader.
- **Responsive shell:**
  - desktop: a persistent sidebar;
  - phones: a top bar, bottom tabs and a More sheet;
  - future areas are listed as "Later", never as dead links.
- **Motion:**
  - no startup splash;
  - skeletons for ordinary loading;
  - `GenerationAnimation` for substantial generation only:
    - the plan sheet emerges from **behind** the cap and flies along the logo's
      arrow vector, then resolves into the result;
    - stages are supplied by the caller;
    - the caption never implies an AI decides anything;
    - reduced motion shows the static mark and the same stages;
    - pure CSS.

## Onboarding UI (presents the API; decides nothing academic)

- **Program choice:** pending-review programs are selectable and labelled.
- **Transcript review:**
  - row states shown with **icon and words**;
  - document warnings, including out-of-order reading and term totals that
    don't add up;
  - the original line shown for every row;
  - "Mark as in progress" is *offered* for ungraded courses, never assumed;
  - a plain statement of what confirming will do.
- **Manual entry:** shows the API's structured validation issues.
- **Transcript-reported GPA:** labelled as the transcript's own figure, not
  CoursePilot's.

## Found and fixed during this phase

- **Real OIDC callback bug:** Next.js `NextURL` was passed to `oauth4webapi`,
  so every real sign-in would have failed at the callback. Caught by the
  route-handler test against the real local provider.
- **Visual QA findings:**
  - a phone overflow in the step indicator;
  - light-mode success-badge contrast;
  - an invalid radiogroup structure;
  - an unlabeled file input;
  - misplaced sticky panels;
  - arrows on rows that weren't links.
- **Animation layering:** frame capture showed the sheet in **front** of the
  cap, because an animated `z-index` interpolates through the cap's layer.
  `z-index` is now never animated.
- **Auth injection found a test gap:** a missing-configuration guard was
  untested for single missing variables. Fixed.

## Schema

Migration `d498ccbced22` adds `user_account.identity_issuer`.

- **Backfill:** dev accounts get the dev issuer; pre-7.1 OIDC rows get
  `OIDC_ISSUER`.
- **Downgrade:** refuses to merge accounts.
- **Verified:** upgrade, downgrade and re-upgrade, plus `alembic check`, run on
  a **throwaway database** (`scripts/verify_migration_roundtrip.py`).

## Tests

| suite | result |
|---|---|
| `frontend` vitest (config, scopes, open redirects, sealed cookies, proxy, middleware, full code+PKCE flow against a real local provider) | 30 passed |
| `backend/tests/test_auth_identity.py` (PG: required claims, wrong iss/aud/expiry, issuer-scoped accounts, institution policy, `/me`, production dev refusal) | 16 passed |
| Visual QA walkthrough (`frontend/tests/visual/qa.mjs`) | 56 screens, 0 findings |
| End-to-end OIDC in Chrome (`frontend/tests/e2e/mock-idp.mjs` + real API) | 10/10 checks |

**Failure injection.** All suites detect what they should:

| suite | result |
|---|---|
| 7.1 auth (`scripts/inject_auth_failures.py`) | **18/18 DETECTED** |
| 7.0 onboarding | **43/44 DETECTED**. The miss is T, benign by design (the UNIQUE constraint answers identically); T2 covers it. |
| 6.8 monitoring | **16/16 DETECTED** |
| 6.9 live | **29/29 DETECTED** |

**Final regression** (HEAD `e3ea189`; `*_test` PostgreSQL 16; full logs kept):

| suite | before (main `ddff132`) | after |
|---|---|---|
| frontend typecheck / vitest / production build | - (no tests existed) | **clean / 30 passed / built** |
| root (PG) | 1851 passed, 5 skipped | **1867 passed, 5 skipped** |
| backend (PG) | 921 passed, 4 skipped | **937 passed, 4 skipped** |
| ingestion (PG) | 930 passed, 1 skipped | **930 passed, 1 skipped** |
| retrieval | 46 passed | **46 passed** |
| ingestion (SQLite) | 850 passed, 81 skipped | **850 passed, 81 skipped** |

The laptop slept during the run (system log: standby 05:42 to 12:08), so the
two long ingestion suites report inflated durations. Their results are
unaffected. Skips are pre-existing and environment-gated; they are not
reported as passes.

## Limitations

- **Google has been exercised locally only.** Production still needs:
  - a production OAuth client (or production redirect URIs);
  - an `https` `APP_URL`, which makes the cookies `Secure`;
  - secrets held outside the repository.
- **No Rutgers institution rule.** The ScarletMail `hd` value is unconfirmed,
  and one account would not establish a rule. Google sign-in never implies
  current enrollment.
- **Sessions last as long as the ID token** (Google: 1 hour). No refresh token
  is requested, by design.
- **Degree, Plan, Schedule, Courses, Notifications and Assistant screens:** not
  built (by design).
- **The generation animation is a component and a prototype.** It is wired to
  the planning UI in the core-product phase.
- **Phase 7.0 transcript limitations carry over:**
  - transfer/AP and retake layouts unverified on real documents;
  - other schools' layouts unverified;
  - no OCR;
  - unstorable transfer credit without an equivalent;
  - prefixes not persisted;
  - `layout_verified=false`.
- **A Next.js build warning** about `CompressionStream` comes from `jose`'s
  optional compression path in middleware. It is a static-analysis false
  positive; the session code doesn't use compression.

## Not done (by design)

- No Rutgers password, CAS automation, WebReg, scraping or registration
  actions.
- No academic data from signing in.
- No LLM in any academic decision.
- No mobile app.
- The PR is not merged.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
