# Phase 5.13 — GPT-5.6 Luna Live AI + RAG Verification

## Summary

This PR adds OpenAI GPT-5.6 Luna as CoursePilot's explanation model. It sits
behind the existing `LLMProvider` / `ExplanationModel` boundary, and nothing
was redesigned. The Degree Engine is still the only authority on academic
correctness. The model explains a decision the engine has already made.

**This has not been verified live.** There is no `OPENAI_API_KEY` in this
environment. No credential was created, the tool's own credentials were not
used, and no live test was marked as passed. The model ID `gpt-5.6-luna` is
wired as a server-side setting (`OPENAI_MODEL`) and has **not** been checked
against the vendor.

## What changed

| | |
|---|---|
| `app/llm/providers/openai.py` | new `OpenAIProvider` |
| `app/llm/base.py` | `ProviderError` moved here so the OpenAI adapter does not import from the Anthropic module (re-exported there, so existing imports still work) |
| config | `openai_api_key` (server only), `openai_model = "gpt-5.6-luna"`, and a production-audit finding when the provider is `openai` with no key |
| wiring | `LLM_PROVIDER=openai`, `EXPLANATION_PROVIDER=openai`. A missing key gives `NoModel`, so the deterministic path runs |
| deps | `openai>=1.60` added to the optional `ai` extra |

Unchanged: the evidence assembly, retrieval, validator, system prompt,
fallback, request schema, routes, the Degree Engine, and the Anthropic
adapter (kept and still tested).

## Verification

- **Adapter contract:** roles, `json_object` only when a schema is given, `max_completion_tokens` from settings, usage mapping, and malformed or empty output handled.
- **Failure matrix:** timeout, connection, rate limit, 5xx, auth and bad request each become a class-only `ProviderError`, and the student gets the deterministic explanation. Retries happen only inside the SDK, bounded by settings.
- **RAG:** similar-vocabulary siblings are retrieved by BM25 and then filtered out before the model. Provenance is carried through. Decision facts (`coursepilot_degree_audit`) stay separate from catalog facts (`rutgers_*`). Four explanation requests cause **one** index build.
- **Outbound payload (`test_rag_6`):** captured at the transport through the authenticated route. It names the course and contains no `external_ref`, subject, issuer, bearer token, API key or identity field.
- **Prompt injection:** hostile catalog text reaches the model, and the model's fabricated graduation claim is rejected. The response is discarded, the deterministic explanation is served, and the model is **not called again**.
- **Evaluation, 17 cases:** exact model-call counts. Ungrounded requests (course not recommended, course excluded) make **zero** calls. Rejected output always falls back to the deterministic explanation.

## Findings

1. **The live smoke test would have passed on a failed request.** A bad key or a wrong model ID raises `ProviderError`, the service falls back, and the old assertions accepted either outcome. The test now requires exactly one real completion. That was checked against a closed local port, so no external request was made.
2. **The "no identity leaves" claim had no test behind it.** I was about to write it into the docs, so I wrote `test_rag_6` first.
3. **`already_satisfied` was flaky** because the fixture gave out random course keys, not because the engine is nondeterministic (its tie-break is the course string).
4. **A Phase 5.11 lifecycle test** searched for a fixed marker, and it dropped out of the top 10 once the persistent test DB held 17 copies of it. The marker is now unique per run.
5. **The vendor-import guard** now lets each adapter import **only its own** SDK.

## Stage timings (real dev-DB copy, 4,415 courses, no network)

```
BM25 reuse 0.49 · retrieval 0.79 · audit hit 3.66 · baseline 23.17
evidence 0.008 · render 0.003 · validation 0.016 · adapter 0.49   (p50 ms)
```

OpenAI network latency, model latency and token usage could not be measured,
because no request was made.

## RAG verdict

| claim | verdict |
|---|---|
| adapter, error translation, filtering, provenance, fact separation, index reuse, injection containment, fallback, no identity outbound, provider independence | LOCALLY VERIFIED |
| client cannot choose model / provider / limits | IMPLEMENTED |
| `gpt-5.6-luna` is a served model ID | UNVERIFIED |
| a real GPT-5.6 Luna request succeeds and passes the validator | UNVERIFIED |
| end-to-end RAG + LLM | LOCALLY VERIFIED, not LIVE VERIFIED |

## Tests

```
SQLite      554 passed,  66 skipped   (unchanged)
PostgreSQL  619 passed,   1 skipped   (unchanged)
Backend     470 passed,   4 skipped   (was 418/3; 3 consecutive runs)
retrieval    46 passed                (unchanged)
alembic check  clean, head ce2b9afd3fa7
```

## To make it live

Set `OPENAI_API_KEY` and `EXPLANATION_PROVIDER=openai`, then run
`RUN_LIVE_AI_TESTS=1 pytest tests/test_openai_provider.py -k live`.
`test_the_live_smoke_test_is_opt_in_and_currently_skips` fails as soon as a key
is present, so the live test cannot be quietly skipped forever.

Docs: `DATA_MODEL.md` §35 and `LEARNING.md` Lesson 27.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
