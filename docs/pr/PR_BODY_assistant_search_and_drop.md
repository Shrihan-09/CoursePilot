# Fix async search false negatives and preserve explicit course drops

Async requirement searches could say no results despite valid SOC options. Removing a course also regenerated its semester to the credit floor, replacing the course the student had deliberately dropped. This change makes those two flows explicit and reviewable.

## Search

- Broad async/online requirement requests use the existing search service with one group of alternative remaining requirements, not an intersection of every requirement. Async/asynch/asynchronous maps to async-only; online includes async. User-specified time/campus/seat filters remain available through the existing interpreter. Builder context is kept separate from routed user text, so draft course IDs do not become change requests and the builder's term is honored.
- Removed the 240-candidate cutoff before validity/modality filtering. On the diagnostic record, a WCd-only search found `37:575:202`, while the old broader OR search omitted it at this cutoff. No unit-code filter excludes 37: or 04: courses. Both prefixes are covered by the archived-SOC tests.
- `asynch` is recognized as delivery rather than an accidental BM25 topic. Format words are not expanded as catalog-topic keywords for generic requirement searches.
- Guided "different requirement" searches include all remaining requirements, including those planned later. The three visible question chips no longer limit the search pool.
- Empty results report effective filters and offer opt-in Any format / Any remaining requirement controls. Relaxation preserves other filters and all academic gates. Structured validated search arguments and interpretations are retained in the owned conversation payload for future diagnosis; no API credentials or model history are logged.

The historical chat payload did **not** store tool-call arguments, so its exact original call cannot be recovered. The AND/OR comparison is a controlled local reproduction, not a claim about an unavailable historical trace. An external replay was rejected by approval review and was not executed. All subsequent replays used local/fake providers and read-only tools. The fixed local path was checked on the existing record with zero external model calls and an unchanged plan revision.

## Drops

- A remove keeps the remaining term roster and adds a course/term avoid. Replanning cannot add another course or new anonymous elective-credit placeholders to that term. Existing anonymous elective credits are preserved on a simple remove.
- Preview shows the credit change, the original minimum warning, where affected requirement contributions move (or that they remain unfilled), other moves and graduation impact. Confirm persists the roster and an explicit allow-below-minimum override without lowering the student's preferred minimum.
- Warnings survive rebuilds, including an explicitly emptied semester. Suggest a replacement is a separate opt-in control; it opens browsing or searches for suggestions without applying a course. Explicit add/swap/move choices can update the roster. The normal swap flow remains a chosen replacement with preview/confirmation and Undo.

Storage uses existing JSON snapshots/edits; no migration or backfill. Planning engine version is 7.8.2, so existing plans use the normal rebuild notice. No existing student plan is changed automatically.

## Validation and before/after

See [the QA report](../qa/assistant_search_and_drop.md) for exact commands/results. Frontend/backend suites run one at a time, with outputs outside the repo/OneDrive. Tests use isolated fixtures and fake providers; no test uses the development database or a real model. Read-only diagnostics on the existing record are separate from tests.

Before search: “Found 0” / an unqualified no-online-options claim. After: grounded async cards, including the four named official courses in all four new real-SOC golden cases; empty queries explain their actual filters and offer a controlled relaxation.

Before drop: dropping a three-credit course could keep an 18-credit term by choosing a replacement. After: the preview shows 18 → 15, a below-16 warning when applicable, and later/unfilled requirement consequences. Confirm keeps the gap. Suggest a replacement performs no addition until the student chooses and confirms one.

Validation: 45 final assistant/search tests passed; planner/workload regressions and focused drop integration passed; the 301-candidate cutoff regression passed. Frontend: 182/182, plus TypeScript. Fake golden CLI: 42/42 in both configurations, 100% no-auto-apply and no-invented-course rates. Runs were sequential; detailed overlapping counts are in the QA report.

No gh, main commits/pushes, force-push, private-file commits, Docker volume/database deletion, or development DB migration.
