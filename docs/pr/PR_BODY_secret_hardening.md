# Secret-leak hardening: gitleaks hook, fail-fast OpenAI key, log redaction, SECURITY.md

## What changed

**1. gitleaks pre-commit + full-history scan**
- `scripts/hooks/pre-commit` runs the existing path guard, then
  `gitleaks git --pre-commit --staged --redact`. It fails closed: if gitleaks
  isn't installed, the commit is refused with install instructions.
- `.gitleaksignore` lists the two reviewed false positives by fingerprint
  only (no values), each with a reason.
- **CI step: not added. The repo has no CI** (`.github/` doesn't exist).
- **History scan (343 commits, `--redact`):** 2 findings, both false
  positives:

  | Rule | File | Commit | Line | Verdict |
  |---|---|---|---|---|
  | generic-api-key | `frontend/tests/unit/assistant.test.tsx` | `3d7a38c95e` | 40 | Test-fixture `action_token` placeholder (two words + digits) |
  | generic-api-key | `backend/app/services/live.py` | `ab2275c3a1` | 526 | `claim_token=uuid.uuid4()`, a notification lease id |

  After the ignore file is added, a full rescan is clean.

**2. Ignore rules and `.env.example`**
- Already ignored and verified with `git check-ignore`: `.env`, `.env.*`,
  `*.har`, `*state*.json`, `data/private/`.
- Added `coursepilot-private/` and `**/coursepilot-private/`. Git cannot see
  `C:\coursepilot-private` itself; these catch a copy made inside the repo.
- `scripts/check_private_files.py` now also blocks staged `.env` / `.env.*`
  (except `.env.example`) and `coursepilot-private/` paths, even if
  force-added.
- `.env.example` already existed. Its only real-looking value was the local
  Docker DB password, which is now the placeholder `change-me-local-only`,
  used consistently so `cp .env.example .env` still works. The README test-DB
  example uses `<POSTGRES_PASSWORD>`.

**3. Backend**
- `require_openai_key()` in `app/core/config.py`, called first in the
  lifespan: the API **refuses to start** when `LLM_PROVIDER`,
  `EXPLANATION_PROVIDER` or `ASSISTANT_PROVIDER` is `openai` and
  `OPENAI_API_KEY` is empty or blank. The message names settings, never
  values.
- `SecretRedactionFilter` on the root handler scrubs the configured
  OpenAI/Anthropic key values and `sk-…` / `Bearer …` / `Authorization:` /
  `x-api-key:` / `cookie:` shapes from messages, args and `extra` fields.
- The `openai` and `anthropic` SDK loggers are pinned at WARNING, joining
  `httpx`/`httpcore`, because at DEBUG they log request options, including
  headers.
- Audit: no existing call site logs request headers or a key.

**4. `docs/SECURITY.md`**: where secrets live, the guardrail layers,
rotation steps (OpenAI, Anthropic, APNs, DB, and what to do if a key was
committed), the OpenAI budget cap (OpenAI-side project budget plus the
in-app limits), and the deploy plan to move to identity-based Azure OpenAI,
Bedrock or Vertex AI with no static key. Linked from the README.

## Why
To stop credentials and private student material reaching Git or logs, and
to make a misconfigured deploy fail loudly instead of booting "healthy" with
a broken assistant.

## Tests
- New `backend/tests/test_secret_hardening.py`: **15 passed**. Covers the
  fail-fast check for each provider setting and for a blank key, refusal
  messages carrying no values, redaction of key/bearer/header shapes and
  configured literals (in args and extras), ordinary messages left untouched,
  the SDK logger levels, and the path guard (.env, HAR, state, private
  folders blocked; `.env.example` allowed).
- Hook checked by hand in a scratch repo: a staged fake key is blocked and the
  value is not printed; a force-added `.env` is blocked; a clean commit goes
  through.
- Full backend suite against `coursepilot_test`: **1332 passed, 2 failed, 4
  skipped**. Both failures **also fail on clean `main`**, so they predate this
  branch:
  - `test_deployment_verification.py::test_a_fully_configured_production_setup_reports_nothing`:
    the audit reports `assistant_provider is 'openai' but no API key`, because
    the test doesn't set a key while the assistant defaults to openai.
  - `test_student_context_api.py::test_no_path_parameterised_student_route_exists`:
    the expected route list is stale (it lacks `/student/assistant`,
    `/student/scenarios/compare`, ...).
- Note: running the suite with the default local `.env` (DB `coursepilot`)
  makes about 220 DB tests fail on the `_test`-only guard. That's
  environmental and the same on `main`.

## Migrations
None.

## For you to decide or check
- **Fail-fast is conditional**, not unconditional: a missing key fails only
  when a provider is `openai`. Because `ASSISTANT_PROVIDER` defaults to
  `openai`, a default setup does require the key; offline and test runs use
  `fake`/`none`/`echo`. Say if you want it required always.
- **Enable the hook in each clone:** `git config core.hooksPath scripts/hooks`.
  It was not set in your clone before this task, so the existing private-file
  guard wasn't running. I've set it in your local clone. Install gitleaks with
  `winget install Gitleaks.Gitleaks`, then open a new shell so it's on PATH.
- **Set the OpenAI project budget and alerts** in the OpenAI dashboard
  (SECURITY.md §4). Code can't do this.
- When there's CI, add `gitleaks git --redact` as a step.

## Data / backfill
None. Your local `.env` and database are untouched. The `change-me-local-only`
placeholder only affects fresh copies of `.env.example`.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
