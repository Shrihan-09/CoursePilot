# Stop rejected sessions from looping between sign-in and the app

A sealed frontend cookie could contain a credential the API rejected. The signed-in layout redirected `/me` 401s to `/auth/login`; in development mode that returned to `/signin`, where middleware trusted the cookie and redirected to `/home` again. Clearing cookies only helped until the next development sign-in recreated the rejected session.

The running Next.js server had a development auth override while both saved environment files and the backend specified OIDC (`DEV_AUTH_ENABLED=false`). Backend logs showed repeated `token_rejected` and `GET /api/v1/me` 401 responses, without a missing-table traceback. The development DB and Alembic head both reported `p11b02`.

## Resulting behavior

- Sign-in forwards returning users only after a successful API account check. Middleware no longer treats cookie encryption validity as evidence of API acceptance.
- A rejected account check clears the session and transaction cookies through `/auth/session-error`, then stops at a sign-in screen with an explanation. Browser API 401s use the same recovery path once, even when requests fail together. Provider login begins only after the student chooses to sign in.
- App and onboarding navigation use a shared successful-account check. Backend failures, forbidden responses and timeouts show the existing friendly error/retry page rather than selecting setup or restarting authentication. Server API checks have a 10-second timeout and do not follow upstream redirects.
- A session from a different configured auth mode is rejected. Development sign-in verifies API acceptance before issuing a cookie. Authentication endpoints and sign-in cannot be post-login return destinations.
- Healthy completed users still reach Home; unfinished users still reach onboarding. These two routing decisions use the same API flag.

## Local configuration and existing record

Restarted only the existing Next.js development server with OIDC, matching its saved `.env.local` and the backend. No environment files or secrets were changed. The existing populated student belongs to an OIDC identity. The default development subject does **not** map to that record: dev and OIDC are separate identity namespaces. The fix preserves that boundary and restores OIDC sign-in; it does not create an alias, move ownership or provision a replacement account.

A read-only database transaction verified that the configured OIDC issuer's existing identity resolves the same linked student with its recorded courses intact, with no new account provisioned. This verifies the ownership lookup, not a fresh interactive Google login; the user completes that sign-in in their browser.

## Validation

- Targeted auth and redirect tests: 48 passed. Regression coverage follows the rejected-cookie redirect chain for both `/home` and `/onboarding`, checks direct sign-in, backend outages/403/5xx, changed secrets/modes, simultaneous browser 401s, dev preflight rejection and successful setup/app routing.
- TypeScript: `tsc --noEmit --incremental false` passed.
- Full frontend: **198 tests passed across 19 files**. Commands ran sequentially, using `vitest run --maxWorkers=1`. Output is under `%TEMP%` (`coursepilot-auth-loop-*.log`), outside the repository and OneDrive. All automated auth calls use local mocks, without a database or real identity provider.
- Live Next.js HTTP checks with a deliberately invalid diagnostic credential confirmed `/home` → session recovery → `/signin?error=session` → **HTTP 200**, the same for onboarding, and direct sign-in → **HTTP 200**. No repeating location. Next.js output records the bounded 307/303/200 chain with no stack trace. Startup and request logs are also under `%TEMP%`.

No backend code changes, migration, backfill or DB writes; no backup was needed because no migration ran. `.env` and `frontend/.env.local` remain ignored. No real credentials or private record contents are included in this PR.

Branch: `feature/fix-auth-redirect-loop`, independently based on main `75d0b47`. The unmerged search/drop fix remains on its separate branch.
