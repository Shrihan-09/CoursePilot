# Cross-cutting tests

Tests here span more than one component. Component-local tests live with their
component:

| Location | Scope | Needs |
|---|---|---|
| `backend/tests/` | Backend units, API contract | Nothing (no DB, no network) |
| `ingestion/tests/` | Parsers, normalizers | Fixture files only |
| `tests/contract/` | Frontend/backend schema agreement | Running backend |
| `tests/fixtures/` | Shared golden data | — |

## Testing rules for this project

**No test may call a real LLM.** Model output is non-deterministic, so a test
that depends on it fails randomly and gets muted. Bind `LLM_PROVIDER=echo`, or
a scripted fake that replays recorded responses.

**No test may hit a Rutgers server.** Ingestion tests run against saved
fixtures. This keeps tests fast, offline, and courteous.

**Validator tests are the highest-value tests in the repo.** They are pure
functions over fixed inputs, they encode real academic rules, and they are the
last line of defense against a wrong answer reaching a student. When a
validator is implemented, its tests should include cases that must *fail*
validation — a validator that only gets tested on valid plans has never been
tested.

## The eval set (not yet built)

Retrieval quality is measured, not assumed. `tests/fixtures/` will eventually
hold a labeled query set so BM25, vector, hybrid, and reranked retrieval can
be compared on the same questions. See `docs/RAG_ARCHITECTURE.md`.
