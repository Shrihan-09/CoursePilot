# Semester builder (Prompt 12)

The Plan page is organized by semester and requirement category. The next semester opens by default; later semesters collapse. Course titles lead, with codes secondary and the existing prerequisite, offering, workload and recommendation evidence behind Details. Anchors and user locks remain visible.

The next semester's Your choices card has compact Keep/Change rows. Change opens a grouped modal, initially showing at most three options per group. More results are available through Show more or the full semester builder. Answered choices collapse to All set / Review. Keep accepts the current recommendation for this visit; choosing a replacement still uses the existing preview and confirmation flow.

## Browser and evidence boundary

`POST /student/plans/current/builder` resolves the signed-in student's saved plan. It accepts a term, draft selection, smart-search text, filters, sorting and pagination. There is no student/program identity in the request body. The response reports total offered courses, filtered matches, addable/gated/selected counts, and the current page. Counts refer to distinct primary course identities with SOC offerings for that term, not sections, campus duplicates or companion records.

`app.services.planning.semester_builder.term_courses` starts from every primary course offered in the term. It does not use the options shortlist or its 240-candidate limit. Existing `OptionsContext`, candidate gates, requirement resolver, BM25 topic search, instructor names, section filtering, availability freshness, blocked times and personal similarity signals are reused. Failed gates stay visible when they match the filters. All combined meeting filters must match a single eligible section; hybrid filtering requires both online and physical meetings in that section.

Still-needed is on by default. Turn it off to browse courses outside remaining requirement pools, including non-counting courses. Unknown coverage is explicit. A free-elective label reflects the current engine's degree-credit rules and absence of a mapped requirement; it does not assert that the unbuilt knowledge index has complete coverage of all majors/minors. Potential membership for gated or unsaved draft courses is labeled separately from verified plan contributions. Term-specific Core certification remains authoritative for Core fulfillment. Missing term data produces a visible empty-data notice, not a planner shortlist presented as all offerings.

### Prompt 08 integration point

`MembershipProvider.candidates(ctx, course)` is the replaceable membership boundary. `ExistingMembership` supplies active requirement-pool membership today; `ctx` carries the owned student, program version, plan, term-sensitive engine and current status. An index-backed implementation can be passed to `browse` once Prompt 08 is built and its coverage/review/applicability checks are available. The browser response and frontend do not require an index table or loader. Preserve conditional/unknown labels, conflicts and source provenance when extending this contract; never convert missing index rows into definitive free-elective-only claims.

## Saving a draft

The browser keeps additions and removals local. `set_term` extends the existing `PlanChange` JSON contract with `courses`, `expected_revision` and optional `pin_explanations`. A preview:

1. Checks the draft's starting revision and existing term.
2. Reuses existing add/move/remove operations, preserving anchors and locks.
3. Pins retained flexible courses and additions as USER_SELECTED.
4. Checks added-course validity. Only supported pin exceptions accept an explicit explanation; unmet prerequisites cannot be bypassed by supplying one. Co-added corequisites are checked by the resulting engine run.
5. Regenerates once, reports all changes to later terms/requirements/graduation, and rejects any requested selection the engine could not retain.

Only the existing Confirm operation applies the revision-bound preview. Undo restores the previous plan. This uses existing JSON storage; no migration or backfill is required. The plan supports 48 total pins/selections, with up to 24 courses in one builder draft. An over-limit change is refused rather than saved into an unreadable edits payload.

Two engine corrections support explicit elective choices: pins are still considered after the remaining requirement tree is satisfied, and an explicitly degree-excluded pin is omitted from planned degree credits. The existing move horizon also now supplies the required summer/winter arguments.

The planning engine version is 7.8.1. Existing saved plans retain their records and use the existing out-of-date/rebuild notice before further edits; no plan is regenerated automatically.

## Calendar and assistant

The weekly preview calls the existing schedule engine with the draft courses and saved time, blocked-time, campus and honors preferences. Auto-pick returns section combinations to inspect on the existing weekly calendar; conflicts and unavailable schedules remain visible. The existing engine supports eight courses per schedule request. Calendar combinations are previews, not saved WebReg index selections. The comparison link opens the current draft courses in a new tab, preserving the builder draft.

The merged assistant side panel receives `context_term` and a bounded `builder_selection` list on every message. The selection is explicitly described as an **unsaved draft**, not an academic record. Its removable context chip shows the term and draft size. Add to semester rechecks the suggestion through the browser and appends it locally. It does not consume an assistant apply token. Proposal application is suppressed while builder context is attached; Save / Preview / Confirm remains the write path. Selection changes update the context; closing/unmounting the builder clears its context. The existing daily cap, history and conversation behavior remain shared.

On desktop the builder has browser, selection and calendar columns. Phones/tablets have Browse, Selection and Calendar controls, and the Preview footer stays reachable. Existing theme tokens support light/dark mode.

## Verification

See [the execution checklist](qa/12_semester_builder_checklist.md) and [PR body](pr/PR_BODY_semester_builder.md). Browser fixtures are fabricated and use a local mock API, not a model or student database. Backend integration uses archived SOC and a fabricated student in isolated in-memory SQLite, with the existing test DB guard installed. PostgreSQL-specific behavior is not newly proven by those fixtures. All logs, screenshots, temporary databases and Next build output are outside the repository and OneDrive.
