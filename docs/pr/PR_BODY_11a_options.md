# Course options, confirmed plan changes, and requirement-aware search

Students can review alternatives to a planned course, see exactly what a change does, and save it only after Confirm. The planning engine supplies the options and validates every proposal. Saved choices and locks survive rebuilding; invalidated choices are flagged for reselection. Undo restores a previous plan revision.

## Part A checklist

| Spec | Status | Behavior |
|---|---|---|
| A1 Options | Done | Options on planned courses; anchors explain their lock and allow valid moves; same-requirement and different-requirement groups; offering, workload, delivery, requirements, and impact labels. |
| A2 Preview / confirm / locks / undo | Done | Course and semester locks; swap, move, remove, add, lighten, and choose-alternative previews; affected-term diff and forced moves; revision-checked Confirm; five-version history; actionable 10-second Undo toast and persistent Undo last change. |
| A3 More like what worked | Done with approved search approach | Owner-only B-or-better and in-progress doing-well signals; profile opt-out defaults on; feature similarity orders options and fillers after validity and other priorities. No grades appear in similarity labels or plan-change logs. |
| A3b Your choices | Done | Next-term questions, three options per group, alternative recommendations, anchor explanations, skip/default choices, search fallback. Setup opens Plan after building. Questions refresh on plan revision and regeneration. |
| A3c Requirement-aware search | Done with approved search approach | Deterministic goal/name/alias resolution, ambiguity prompts, topic search, exclusions, term, delivery, campus, time/day, level, credits, open-section and multiple-requirement filters. Delivery and time filters must fit the same eligible section; unknown times cannot satisfy a time constraint. |
| A4 Typed owner-scoped tools | Done | Options, choices, search, preview, apply, undo, doing-well, and personalization endpoints resolve the signed-in student's record. Stale previews are refused. |

## Search limitation approved by the owner

Keep the existing BM25 search over available titles/descriptions and curated aliases (including AI/ML), plus owner-only feature similarity. The repo has no course embedding index; existing architecture deliberately avoids a new large embedding dependency for a corpus that is mostly titles. **This change does not implement pgvector semantic similarity or create embeddings.** Sparse catalog descriptions and unloaded offerings can legitimately produce no matches; the UI explains empty results and preserves confirmed/historical labels. The assistant remains marked coming soon; Part B is outside this PR.

## Registration schedule and archive fix

- Spring 2027 registration tiers and the nightly WebReg closure are stored in `backend/app/core/data/registration_schedule_20271.json`, with the original owner-provided screenshot attribution. Polling slows during the configured nightly closure; observed changes can be grouped by registration tier.
- The term watcher writes openSections archives into `data/raw/live`, the same mounted directory the live worker archives and deduplicates, rather than its container-only cache path.
- Tests cover the schedule's tiers, Eastern time boundaries, polling cadence, phase labels, and the shared archive path.

## Validation

- Backend full suite: **1,246 passed, 4 skipped**. The DEBUG-default test now isolates its environment. `test_requirement_alternatives.py::test_no_feasible_alternative_and_no_choice_is_undecided_with_one_branch_open` passes.
- Final backend edge-case checks: **37 passed**, followed by **9 JSON-default comparator cases passed**. These cover the final requirement-filter and migration-tooling fixes added after the full run.
- Ingestion full suite: **1,176 passed, 1 skipped**. Final options/search recheck and synthetic descriptions: **4 passed, 22 deselected**.
- Frontend Vitest: **163 passed in 15 files**, with one worker at a time. `npm.cmd run typecheck`: passed.
- `alembic check` against `coursepilot_test`: **No new upgrade operations detected**.
- Synthetic-fixture swap preview: **1.00 second**. The existing preview performance regression also passed; this is not a benchmark of a real student's complete corpus.
- `git diff --check` and the private-file guard: passed. All temporary test folders and the description adapter were removed.

No suites ran concurrently. PostgreSQL test URLs targeted `coursepilot_test`; ingestion also used its existing isolated in-memory fixtures. The inherited visual script now requires an explicit test frontend, a `_test` database declaration, and a CI-mode backend before seeding fixtures.

## UI and search walkthrough

See [the synthetic walkthrough descriptions](../qa/11a_options_descriptions.md) for the Your-choices card, Options sheet, swap preview, and all five required query strings. The limited fixture has no eligible match for the first four searches; they remain constrained and show an explicit empty state. The Fridays-off example returns eligible Sociology and Mythology options. No screenshots of a real student's account were captured.

## Migrations and rollout

`p11a01` follows `t3dn01`. It adds plan-edit/revision/preview/history storage and owner-only personalization fields. Dev was already at `p11a01 (head)` at takeover; this continuation did not downgrade or modify dev. Apply the migration in other environments before serving this frontend. No data backfill is required. No Docker volumes or persistent databases were deleted or pruned.

The Alembic check exposed PostgreSQL's lack of a JSON equality operator. The comparator now compares JSON literal defaults without executing `json = json`; other types retain Alembic's comparison. Snapshot model defaults are aligned with the defaults already created by `p11a01`. This adds no migration and changes no stored data.

Owner review and merge only. Part B starts after this Part A merge.
