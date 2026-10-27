# Phase 6.3: program discovery registry, lifecycle, provenance and the first SAS curation wave

## Summary

- **Registry:** discovers every catalog page in Rutgers' own navigation (516 pages for 2026-27, 459 for 2025-26). It extracts credential candidates from page headings. **A page is not a program:** the Mathematics page yields 10 candidates, and discovery never creates a program.
- **Lifecycle:** `parsed -> validated -> reviewed -> published`, plus `needs_rereview`.
  - Machine validation reaches `validated` and stops there.
  - Only a **named human** can review. AI/model/system/bot/validator/placeholder names are refused.
  - Publishing requires an approved review whose source and definition hashes match the current ones.
  - `support_status` is `supported` only when the version is `published`.
- **Provenance:** every version records:
  - its catalog page and archived snapshot;
  - the hash of the page's normalized prose;
  - a semantic definition hash;
  - `curated_by` and the extractor version.

  A change to the source or the definition moves a reviewed or published version to `needs_rereview`.
- **Eligibility reconciliation (bug fix):** eligibility is now recomputed from stored rules, instead of being appended once and never corrected.
  - Six real CS electives were missing on the development database and are now present (CS_ELECTIVES went from 53 to 86 rows).
  - Courses removed from a definition are now removed from eligibility.
  - The tests were written first and failed before the fix.
- **Curated data** moved from `ingestion/tests/fixtures/` to `data/programs/rutgers/nb-undergrad/2026-2027/`.
- **Curation wave:** six new SAS majors (Economics, Linguistics, Philosophy, Psychology, Sociology, Statistics).
  - With CS and Math, 8 programs now have JSON and Markdown review packets.
  - Nothing new is published. All 7 AI-encoded definitions are `validated` and pending human review.
  - CS remains `published` with the stated basis `legacy_curated`. No reviewer was invented.
- **API:** program keys can carry a variant (`sas-640-ba-option-a`).
  - Versions expose their lifecycle and provenance fields.
  - `?include_discovered=true` returns discovered entries in a separate list. By default that list is `null`.
  - Listing uses a fixed number of queries.

## Decisions needed from a human

1. **Review the 8 packets** in `data/programs/rutgers/nb-undergrad/2026-2027/review/`. Each packet ends with the exact `registry_cli review` command.
2. **CS:**
   - The `CS_ELECTIVES` quote drops a sentence without marking the elision.
   - Its query admits 60 graduate (16:198) courses.
   - Converting its `legacy_curated` publication to `human_review` would require passing through `needs_rereview`.

   Each fix is a definition change, so none was made.
3. **Degree designation:** none of the six new pages states B.A. or B.S. for its major, so they use `degree_type = 'major'`.

## Tests

| Suite | Result |
|---|---|
| Ingestion, SQLite | 647 passed, 66 skipped |
| Ingestion, PostgreSQL | 712 passed, 1 skipped |
| Backend | 553 passed, 4 skipped |
| Root (all) | 1265 passed, 5 skipped |
| `alembic check` | no new operations |

The migration's upgrade, downgrade and re-upgrade were verified on a copy of the development database, and again on the development database itself.

## Failure injection

Each mutation was applied, the guarding test was run, and the file was restored.

| Mutation | Result |
|---|---|
| A: publish without the `reviewed` state | caught |
| A1: publish without an approved review | caught |
| A2: AI reviewer accepted | caught |
| A3: stale review publishes | caught |
| A4: source change does not trigger re-review | caught |
| B: removed eligibility retained | caught |
| C: new course not added | caught |
| D: catalog-year isolation broken | caught |
| E: prerequisite term scoping | not applicable; prerequisite code is untouched |

The first version of A was **missed**, because the review-record guard masked the state guard. A test now forges the state for each guard separately.

## Performance (development database: 7,484 courses)

| Operation | p50 | p95 | Queries |
|---|---|---|---|
| `list_programs` | 3.8 ms | 5.8 ms | 4 |
| `list_discovered` (one year) | 8 ms | 68 ms | 2 |
| `reconcile` (all 63 rule-bearing requirements) | 90 ms | 114 ms | 3 |
| Discovery parse (one page) | 7 ms | 7 ms | 0 |

## Out of scope / deferred

- Minimum grades and GPA.
- Sequence semantics (e.g. 411-412).
- The Planning and Schedule engines.
- Political Science, Data Science and Physics: blocked by course lists or tracks the pages don't publish.
- Extractor gap: majors introduced by plain paragraphs, such as Statistics/Mathematics.
- Mass onboarding.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
