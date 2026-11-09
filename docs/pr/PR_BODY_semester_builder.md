# Semester builder and compact semester plan

The Plan page previously led with course codes and expanded option groups. It now starts with compact Keep/Change choices, title-first cards grouped within collapsible semesters, and a full-term builder that lets students browse beyond the planner's shortlist.

The builder reuses current search/options validity checks and the schedule engine. Additions/removals remain a local draft; saving produces one revision-bound preview with later-term, requirement and graduation effects, and only Confirm writes. Retained flexible picks and additions become USER_SELECTED. The assistant side panel receives the draft term and current selection and adds validated suggestions to the draft without applying action tokens.

## Scope and reuse

- A0: compact choices, grouped modal, three initial options per group, Show more, All set / Review, next-semester placement.
- A: collapsible semesters/categories, title-first cards, lock badges, workload summary and evidence behind Details.
- B: exhaustive SOC term universe, smart search/instructor lookup, filters, visible gates, pagination/sorting, live selection totals, calendar combinations/conflict feedback, draft-aware assistant, preview/confirm/undo.
- Prompt 08 index is **not** implemented. Membership has an adapter boundary over existing requirements/options; incomplete coverage remains visible.
- Rebased on `d618b21`, after assistant-panel merged into main. Prompt saved under `prompts/12-semester-builder-and-plan-redesign.md`.

## Validation

Final results are recorded in `docs/qa/12_semester_builder_checklist.md`. Frontend and backend suites ran sequentially. Test output and screenshots are under system temp, outside OneDrive. The synthetic browser harness covers 360px/tablet/desktop, light/dark mode, choices, gates, an assistant draft addition and confirmation. No real model or development database was used for tests.

Passed: TypeScript; 177 frontend tests; 136 backend regression tests; 15 backend follow-up tests; one focused Core-goal test; five browser scenarios with zero page errors. The follow-up runs overlap the regression suite. Test servers were confirmed stopped, and phone selection / desktop calendar screenshots were visually reviewed.

Backend coverage includes offered-course count reconciliation, pagination beyond the old shortlist, student-specific membership, unknown coverage, anchors, revision checks, undo, gate bypass refusal, free-elective additions and an explicitly non-credit pin excluded from degree totals.

## Storage and decisions

No migrations or backfills. `set_term` adds fields inside the existing PlanChange/PlanEdits JSON contract. Confirm is still the only write operation. Engine fixes retain explicit pins after requirements are met, omit excluded pins from degree-credit totals, and repair move-horizon arguments.

Planning engine version is bumped to 7.8.1, so existing saved plans show the usual out-of-date notice and need a user-triggered rebuild before editing. They are not regenerated automatically.

The current scheduler supports eight courses per calendar request. Section combinations are previews; saved WebReg indexes continue to use the existing handoff flow. Keep on the choices card is local acceptance for that visit. Full-term gate evaluation is exhaustive and may be slower on large terms; no full-production-term performance benchmark is claimed. SQLite fixture coverage does not replace PostgreSQL-specific verification.

No gh, merge, force-push, dev DB migration, private data, or Docker volume/database deletion. Review and merge manually after inspecting the screenshots/descriptions linked in the checklist.
