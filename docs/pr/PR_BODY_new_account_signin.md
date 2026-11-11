# Sign-in never ends in a bare 500; refused accounts get a clear page

## The report

Signing in with a second, brand-new Google account showed "Internal Server
Error" at `localhost:3000/auth/callback`, at about 00:50. The first account
still signed in fine. The reporter had signed in as account A, did not sign
out, then signed in as a brand-new account B in the same browser.

## What the investigation found

**The original exception could not be recovered.**

- The backend container's logs for 00:30–01:15 show **no 5xx and no
  exception**. Every request, including `/me`, returned 200, so the failure
  never reached the API.
- The frontend dev server's terminal output isn't persisted anywhere
  readable, and Next's `.next/trace` has no request-level errors.

**Every reported scenario was reproduced end to end, and none returns a
500 on main.** The new `frontend/tests/e2e/signin-accounts.mjs` drives
Chrome through browser → web (OIDC client) → mock provider → web → the real
API on PostgreSQL `coursepilot_test`:

- a brand-new account
- the same account again
- **switching to a brand-new account B without signing out**, with A's
  session cookie present
- a leftover state from an abandoned attempt
- the callback replayed with a used code (refresh or back button)
- B's email already used by another account under a different subject
- a forged or expired state
- institution check **OFF and ON**, each with a token that has no `hd` (a
  personal account), `hd=gmail.com` and `hd=rutgers.edu`

The checks run on every response for a 5xx, and on the server logs, reduced
to exception names and stack frames only.

**Institution settings in the dev `.env`:** `AUTH_REQUIRE_INSTITUTION`,
`AUTH_INSTITUTION_DOMAINS` and `AUTH_INSTITUTION_CLAIM` are **not set**
anywhere (`.env`, `frontend/.env.local`, the backend container). So the
defaults apply: the check is off, there are no domains, and the claim is
`hd`. A personal Gmail account (no `hd`) is therefore accepted in dev and
cannot explain the 500.

**Most likely cause: a transient dev-server state, not sign-in logic.** The
running dev servers serve the shared repository folder with live reload.
Between about 00:37 and 01:10 that folder was being switched between
branches and edited by another task. The backend log shows dozens of reloads
in that window, and the Next dev server recompiles in the same way. A
callback that lands mid-rebuild can return Next's plain "Internal Server
Error".

The first harness run hit a related symptom: a sign-in redirect chain died
with "navigation interrupted by chrome-error" right after the dev server
recompiled a route. It didn't recur in five later cold runs, which now
record every hop by path. This is circumstantial, and it is stated as such.

## Real gaps found, and fixed

1. **A callback could still escape as a bare 500.** Main's callback read its
   configuration and cookies *before* its `try`. Any exception there was
   thrown, which is the bare 500. The whole handler is now wrapped, so
   anything unexpected becomes `/signin?error=failed`. Only the error class
   is logged. A unit test shows main throwing (`RangeError` escapes) and the
   fix redirecting.
2. **A refused account landed on an empty app.** With the institution check
   on, a personal (no `hd`) or gmail.com account landed on `/home` as an
   empty app shell, because `/me` returned 403 and the layout ignored it.
   Now the app layout and the setup page send a 403 to the new
   `/auth/refused`. It **clears the session** and shows the sign-in page
   with *"That account can't use CoursePilot. Sign in with your Rutgers
   Google account (your Rutgers email), not a personal one."* and **Try
   again**.
3. **Failed callbacks offer "Try again".** `expired`, `failed`, `cancelled`,
   `provider`, `institution` and `account` all show a **Try again** link
   that restarts sign-in with the same `returnTo`.
4. **An unreachable or failing API** (a 5xx from `/me`, for example while the
   backend reloads) used to render an empty shell. It now shows the app's
   existing friendly error page, which has "Try again". It isn't a redirect
   to `/signin`, because the middleware would send a signed-in browser
   straight back.

Switching accounts without signing out already worked on main: the callback
replaces the session cookie. It is now covered by unit and e2e tests.

## Tests

- **`frontend/tests/unit/auth-callback.test.ts`** (21 tests, real PKCE
  against `tests/unit/mock-oidc.ts`):

  | Area | Cases |
  |---|---|
  | Signing in | brand-new account; existing account; switching without signing out (A's cookie → B's session); same email, different subject |
  | Failed callbacks | no transaction → `expired`; forged state → `failed`; leftover state from an abandoned attempt → `failed`, then the next attempt works; replayed code → friendly, the session kept; cancelled; an unexpected exception in the token step or before it → `failed` (log: class name only); misconfiguration → `unavailable` |
  | Refused accounts | `/auth/refused` clears the session; an unknown reason → general message |
  | The sign-in page | "Try again" for every retryable error; the Rutgers-account message |
- **Main vs fix:** with main's callback, 2 of these tests fail (the
  exception escapes). With the fix, 21/21 pass.
- **`frontend/tests/e2e/signin-accounts.mjs`:** **11/11** with the
  institution check off and **11/11** with it on. There were no 5xx and
  no exceptions in the API or provider logs. The web log has only the
  expected `auth_callback_failed OperationProcessingError` for the refused
  stale state.
- **Mocks:**
  - `tests/e2e/mock-idp.mjs` now supports several users (`/next-user`,
    test-only) and an optional `hd` claim.
  - `tests/unit/mock-oidc.ts` accepts a `sub` override.
- **Frontend suite:** 201/201 passed; typecheck clean. There are no backend
  changes on this branch.

## Migrations / data

None. Nothing in `.env`, the dev database, `data/raw` or Docker volumes was
touched. The e2e harness writes only to `coursepilot_test`; its accounts
use synthetic subjects.

## For you to decide or check

- **Overlap with `feature/fix-auth-redirect-loop`** (unmerged, commit
  `660cb6a`). It also edits `(app)/layout.tsx`, `onboarding/page.tsx` and
  `signin/page.tsx`, and adds `/auth/session-error`. Expect small conflicts
  in those files. Whichever merges second should keep both behaviors: its
  401/redirect-loop guard and this branch's 403 → `/auth/refused` and 5xx →
  error page.
- **Shared folder and live reload.** Your Docker backend (and dev frontend)
  serve the OneDrive repository folder with live reload. While another task
  switches branches in that folder, your running app serves half-switched
  code. This work was done in a separate git worktree for that reason.
- **The Rutgers message assumes Rutgers Google accounts.** When the
  institution check is turned on, set `AUTH_INSTITUTION_DOMAINS` to the
  domain Rutgers Google accounts actually carry in `hd` (verify from a real
  sign-in, as docs/AUTHENTICATION.md says). The message text is generic
  enough for either.
- **Google's own 403 on the consent screen** (OAuth app in Testing mode) is
  separate and is not a code issue. Adding test users is on your side, and
  no Google Cloud settings were changed.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
