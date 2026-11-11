# Launch prep: fix pre-existing test failures, assistant cost limits, rebrand to Coursenav

One PR, three commits in order:

1. `test: fix the two pre-existing backend test failures`
2. `feat: assistant cost limits for public launch`
3. `feat: rebrand CoursePilot -> Coursenav (user-facing only)`

## Commit 1: fix the 2 pre-existing backend test failures

- `test_a_fully_configured_production_setup_reports_nothing`: a fully
  configured production setup now sets `openai_api_key` to a placeholder
  explicitly, because the assistant defaults to OpenAI. The startup check
  (`require_openai_key`) and the production audit are unchanged.
- `test_no_path_parameterised_student_route_exists`: the expected list gains
  the 10 student routes added since it was last updated. I checked that each
  one resolves the student from the signed-in account:

  | Route | Added in | Why it's safe |
  |---|---|---|
  | `/student/assistant`, `/actions`, `/conversations`, `/history`, `/messages` | Prompt 11b | Owner-resolved; no student id in the path or body |
  | `/student/assistant/conversations/{conversation_id}` | Prompt 11b | Looked up with `student_id == caller` (`service._conversation`); another account's id is a 404 |
  | `/student/plans/current/builder` | Prompt 12 | Owner-resolved; a term not in the caller's plan is a 422 |
  | `/student/plans/current/terms/{term}/sections`, `.../sections/{course}`, `.../webreg` | Prompt 11b | `{term}` and `{course}` name a term or course in the caller's own plan |

  `conversation_id` is added to the allowed path parameters.

**Tests:** full backend suite against `coursepilot_test`: **1334 passed, 4 skipped**.

## Commit 2: assistant cost limits for public launch

| Limit | Frontend | Backend |
|---|---|---|
| **Input: 250 characters** | A live `n/250` counter in the shared composer (side panel and full Assistant page). Past the limit it says "20 over the 250-character limit", marks the field invalid and disables Send. No hard `maxLength`, so a long paste is shown as over the limit instead of being silently cut. | `ASSISTANT_MAX_INPUT_CHARS=250`. Longer text gets a friendly **400** ("Please keep your message to 250 characters or fewer (yours is N)…") before any work is done or the daily allowance is spent. Surrounding whitespace isn't counted, on either side. The status endpoint returns `max_input_chars`. Clarify-answer values are now capped at 120, like chips (they had no cap). |
| **Output** | - | `ASSISTANT_MAX_OUTPUT_TOKENS` default **2000 → 400**, passed to every model call. The system prompt now asks for `text` under 120 words. |
| **Rate** | - | **5 messages per account per minute** (`ASSISTANT_RATE_LIMIT_PER_MINUTE`), a friendly **429** with `Retry-After`. Separate from the existing model-call budget. The daily cap of **50** is unchanged. |
| **Sign-in** | - | Already required on every assistant route (`enforce_request_rate_limit` → `get_principal`); now tested. |

**Tests:**
- New tests:
  - input limit: frontend counter, Send disabled, no truncation; backend 400, exactly 250 accepted, a refused message costs no allowance
  - output cap: comes from settings and reaches every provider call; the prompt wording
  - rate limit: the 6th message in a minute is a 429, and another account isn't affected
  - anonymous requests: a 401 on all 8 assistant routes, with a check that the list covers every assistant route
- Backend **1340 passed, 4 skipped**.
- Frontend **185 passed**; typecheck clean.

## Commit 3: rebrand CoursePilot → Coursenav (user-facing only)

- **One name constant:**
  - frontend `APP_NAME` in `src/lib/brand.ts`, alongside the description and the disclaimer
  - backend `APP_NAME` in `app/core/config.py`, plus a `Settings.app_name` field
  - used for page titles and metadata, the PWA manifest, Open Graph and Twitter tags, the wordmark and logo alt text, the header home links, the assistant panel, the error page, the API title, the assistant footer and the system prompt
- **Footer on every page:** "Coursenav is a student project and is not affiliated with or endorsed by Rutgers University."
  - Rendered once, by the root layout, so it appears on sign-in, setup, app and error pages; `global-error` repeats it, because it replaces the root layout.
  - In the app shell it clears the fixed phone tab bar, and on phones it also clears the Plan page's floating "Ask" button.
- **Open Graph:** new (there were none). Absolute URLs come from an optional `NEXT_PUBLIC_SITE_URL`.
- **User-visible text:**
  - Frontend: 118 non-comment lines in `frontend/src`, covering UI text, wizard copy, empty states and error messages.
  - Backend: 98 string literals, found with a tokenizer that skips comments and docstrings. They cover API error and status messages, audit, planning and scheduling messages, the assistant's replies and system prompt, and the explanation prompts. Migrations weren't touched.
- **Docs:** README (now starting with *Formerly CoursePilot*), `CLAUDE.md`, `backend/`, `ingestion/` and `frontend/brand/` READMEs, and every top-level `docs/*.md`.
- **Safety-relevant follow-ons:**
  - The machine-reviewer denylist (`program_lifecycle.py`) now refuses "coursenav" as well as "coursepilot", so the product name can't be recorded as a human reviewer. Tested.
  - The assistant grounding allowlist now accepts "coursenav".
- **Method:**
  - grep for every variant (`-i 'course ?pilot'`), file list first, then file by file
  - only the exact word `CoursePilot` is replaced, never a search for "nav"
  - Degree Navigator, DN and `dn_*` are untouched (the rename never matches them)
  - the final grep finds no user-visible "CoursePilot": what's left is comments, docstrings and the kept internal names below

**Tests:**
- Backend **1340 passed, 4 skipped** (a clean rerun; the first run hit 2 `MemoryError`/index-build failures while a grep competed for RAM, and both pass alone).
- Ingestion **1195 passed, 1 skipped**.
- Frontend **189 passed** (adds `brand.test.tsx`); typecheck clean.
- Browser check `tests/visual/launch-prep.mjs`: 5 checks passed. It covers the sign-in title, heading and footer, the header, the desktop and phone footer (clear of the tab bar and the Ask button), and the panel counter at 114/250 and over the limit. It uses a mock API and deletes its temporary copy.

### Kept internal names (not renamed)

- **Degree Navigator:** "DN", "Degree Navigator", `dn_*` identifiers, the DN parser and review code.
- **Docker:** containers `coursepilot-db`, `coursepilot-backend`, `coursepilot-live-worker` and the database volume.
- **Postgres:** `coursepilot`, `coursepilot_test` and the other `coursepilot_*` databases (backups, `migcheck` disposables).
- **Alembic:** files and revision IDs, including `20261014_1742_coursepilot_live_scheduler_inbox_.py`, and migration docstrings.
- **Env vars:** `COURSEPILOT_ENV`, `COURSEPILOT_API_URL`, `COURSEPILOT_PRIVATE_DIR`, `COURSEPILOT_ALLOW_DOWNGRADE`, `COURSEPILOT_VISUAL_DATABASE`.
- **Packages and modules:** `coursepilot_ingestion`; `coursepilot-frontend` (package.json); pyproject names and descriptions.
- **API and stored values:** `coursepilot_estimate`, `coursepilot_audit`, `coursepilot_feedback`, `coursepilot_degree_audit`, `coursepilot_requirement_model`, `coursepilot_*_setting_not_rutgers_policy`, and `service: "coursepilot-backend"` in `/health` and `/meta`.
- **Identifiers whose change would break something:**
  - the dev OIDC issuer `coursepilot-dev`
  - the OIDC client IDs `coursepilot-web` and `coursepilot-web-e2e`
  - hash salts (`coursepilot-observability|`, `coursepilot-push|`), since changing them re-keys stored fingerprints
  - logger names (`coursepilot.degree_audit`)
  - the dev-only session secret string
  - temp-folder prefixes and the vitest cache folder
- **Brand asset file names:** `coursepilot-mark-*.png`, `coursepilot-logo-*.jpg`. The images contain no text.
- **Repo and paths:** the git repository name, the GitHub URL, the `CoursePilot/` folder in the README and ARCHITECTURE trees, and `C:\coursepilot-private`.
- **Code comments and docstrings** in the backend, frontend, ingestion and scripts. They aren't user-visible; `/docs` (OpenAPI) is served only when `DEBUG` is on.
- **Not rewritten:** old PR bodies (`docs/pr/*`), investigation reports (`docs/investigations/*`), QA reports (`docs/qa/*`) and the task prompts (`prompts/*`).

### Unsure (left as is; please decide)

1. **The User-Agent sent to Rutgers:** `CoursePilot/0.1 (Rutgers student academic planning project)` in `ingestion/.../sources/{catalog,soc}.py`, `INGESTION_USER_AGENT` in `.env.example`, and the probe scripts. Rutgers sees this name. Rename it, ideally with a real contact address, when you're ready.
2. **Program data text:** `data/programs/**` JSON and review `.md` files have `reason`, `why_not` and `notes` strings saying "CoursePilot", for example "Requires departmental approval, which no CoursePilot data records." Some may reach the UI. Editing reviewed program data changes its content hashes, so it may need re-validation, review and reload; I didn't touch it.
3. **Live / iOS:** the deep-link scheme `coursepilot://watches/...` and the push payload key `"coursepilot"`. They're visible only as URLs or payload fields, and changing them breaks installed clients.
4. **The Open Graph image URL** is `/brand/coursepilot-mark-512.png`. It's visible in page source and link-preview metadata; renaming the asset files is a separate small change.

### Proposed later plan for internal names

1. **Low risk, one PR:** env var aliases (read `COURSENAV_*`, fall back to `COURSEPILOT_*`, deprecate later), package display names and descriptions, asset file names (with redirects), logger names, temp prefixes.
2. **Data:** the User-Agent change, together with the program-data text through the normal review and publish flow.
3. **Infra, on a planned day:** a new Docker container and volume name via `pg_dump`/restore (never `docker volume prune`), new DB names with `ALTER DATABASE … RENAME`, and updated `.env` URLs. Alembic IDs stay as they are; history is never rewritten.
4. **Breaking identifiers last:** the stored enum values (a data migration with dual-read), the hash salts (re-key with an overlap window), the OIDC client IDs and issuer (at the IdP), and the deep-link scheme (with the app release).
5. **The repo rename** on GitHub (old URLs redirect), and the local folder.

## Migrations

None.

## For you to decide or check

- **Output cap 400 vs. reasoning models:** `max_completion_tokens` includes hidden reasoning tokens. If `gpt-5.6-*` reasons, 400 may truncate the JSON reply, and the turn falls back to the template. Watch the fallback rate after launch; raise `ASSISTANT_MAX_OUTPUT_TOKENS` in the deploy environment if needed.
- **The rate limiter is in-memory and per process** (as documented in `SlidingWindowLimiter`): N workers means N × 5 per minute. A shared store (Redis) is the production answer.
- **`docs/SECURITY.md` §4** doesn't list the two new limits yet. That's a docs follow-up, kept out of these commits to keep them separate.
- **`AGENTS.md` and PR templates don't exist**, so there was nothing to rename.
- **Lint:** `next lint` isn't configured in this repo (it prompts to create a config), so it wasn't run.
- **Screenshots** are produced by `node tests/visual/launch-prep.mjs <folder outside the repo>`.

## Data / backfill

None. `.env`, the API key, the database, `data/raw` and `C:\coursepilot-private` were not touched.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
