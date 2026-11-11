# Fix the 2 pre-existing backend test failures + assistant cost limits

Two commits, cherry-picked from `feature/launch-prep`, without its rebrand
commit. `feature/launch-prep` is kept as a backup of the rebrand.

1. `test: fix the two pre-existing backend test failures`
2. `feat: assistant cost limits for public launch`

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
  | `/student/assistant/conversations/{conversation_id}` | Prompt 11b | Looked up with `student_id == caller`; another account's id is a 404 |
  | `/student/plans/current/builder` | Prompt 12 | Owner-resolved; a term not in the caller's plan is a 422 |
  | `/student/plans/current/terms/{term}/sections`, `.../sections/{course}`, `.../webreg` | Prompt 11b | `{term}` and `{course}` name a term or course in the caller's own plan |

  `conversation_id` is added to the allowed path parameters.

## Commit 2: assistant cost limits for public launch

| Limit | Frontend | Backend |
|---|---|---|
| **Input: 250 characters** | A live `n/250` counter in the shared composer (side panel and full Assistant page). Past the limit it says "N over the 250-character limit", marks the field invalid and disables Send. No hard `maxLength`, so a long paste is shown as over the limit instead of being silently cut. | `ASSISTANT_MAX_INPUT_CHARS=250`. Longer text gets a friendly **400** before any work is done or the daily allowance is spent. Surrounding whitespace isn't counted, on either side. The status endpoint returns `max_input_chars`. Clarify-answer values are now capped at 120, like chips (they had no cap). |
| **Output** | - | `ASSISTANT_MAX_OUTPUT_TOKENS` default **2000 → 400**, passed to every model call. The prompt asks for `text` under 120 words. |
| **Rate** | - | **5 messages per account per minute** (`ASSISTANT_RATE_LIMIT_PER_MINUTE`), a friendly **429** with `Retry-After`. The daily cap of **50** is unchanged. |
| **Sign-in** | - | Already required on every assistant route; now tested (a 401 on all 8 routes). |

`.env.example` documents the new settings.

## Tests

Run one at a time on this branch:
- **Backend** against `coursepilot_test`: **1340 passed, 4 skipped**.
- **Frontend**: **185 passed**; typecheck clean.

New tests:
- input limit: frontend counter and Send disabled; backend 400, exactly 250 accepted, a refused message costs no allowance
- output cap: comes from settings and reaches every provider call; the prompt wording
- rate limit: the 6th message in a minute is a 429, and another account isn't affected
- anonymous requests rejected on every assistant route

## Migrations

None.

## For you to decide or check

- **Output cap 400 vs. reasoning models:** `max_completion_tokens` includes hidden reasoning tokens. If `gpt-5.6-*` reasons, 400 may truncate the JSON reply, and the turn falls back to the template. Watch the fallback rate; raise `ASSISTANT_MAX_OUTPUT_TOKENS` in the deploy environment if needed.
- **The rate limiter is in-memory and per process:** N workers means N × 5 per minute. A shared store (Redis) is the production answer.
- **`docs/SECURITY.md` §4** doesn't list the two new limits yet; that's a docs follow-up.
- **Not included:** the CoursePilot → Coursenav rebrand. It stays on `feature/launch-prep`.

## Data / backfill

None. `.env` is not touched; your local setup doesn't override `ASSISTANT_MAX_OUTPUT_TOKENS`, so the new default of 400 applies.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
