# Phase 7.2.2: Academic Intelligence Foundation

## Summary

This phase separates **who owns a requirement** from **who is being audited**. It makes progress honest. It lets a student import their registered schedule from WebReg without CoursePilot guessing any part of their section identity.

```
Rutgers data -> facts            Degree Engine 7.2.2 -> requirements (major + school rules, one owner each)
confirmed student data -> context      Eligibility -> can / cannot / UNKNOWN
modeled progress -> completed | in progress (conditional) | planned (conditional) | UNKNOWN
WebReg PDF -> staged identities -> SOC match -> student review -> confirmed registration (separate record)
```

The phase was started by another agent, which hit its usage limit partway through. It was resumed from the same working tree. The interrupted work was diagnosed and repaired, not reverted.

## What changed

- **School rules compose with majors.**
  - SAS Core is a `SchoolRequirementSetVersion`, owned independently of any major.
  - Every requirement has exactly one owner (a database CHECK enforces it).
  - Allocation scope, eligible origins, graded-only rules, required campus and verified-evidence exemptions are all explicit.
  - Unknown attendance or grade evidence stays UNKNOWN.
- **Modeled progress, read-only (`GET /student/progress`, policy `modeled-obligations/1`).**
  - Percentages are exact fractions.
  - Completed, in-progress and planned work are separate segments; the in-progress and planned segments are conditional.
  - UNKNOWN is never counted as done. A scope with an unknown denominator shows no percentage.
  - A stale saved plan is withheld, never regenerated.
  - `graduation_eligibility` is always `not_assessed`: **100% modeled progress does not mean eligible to graduate.**
- **WebReg "View / Print Schedule" import.**
  - Positioned-layout extraction runs in an isolated, killable process. Raw PDFs are not retained.
  - It fails closed: no OCR, and ambiguous terms, conflicting identifiers and unexplained course text are all refused.
  - Each row is deterministically matched to SOC sections: `EXACT_MATCH` / `MULTIPLE_MATCHES` / `NO_MATCH` / `NEEDS_CONFIRMATION`.
  - Nothing is recorded until the student reviews and confirms.
  - Confirmed registrations are a separate record. They never change academic history or section confirmations.
  - Meetings are shown only for an exact SOC match.
- **Web.**
  - Profile shows modeled progress, with drill-downs.
  - The import review uses wording written for students.
  - Imported registrations appear in My current schedule, labelled as imported.
- **Foundations (no behaviour change for students yet).**
  - Modality normalization (course versus section).
  - Ranking evidence, plus the `ranked_by_requirement_coverage` reason.
  - Program-coverage report.
  - Design-only contracts for feedback, assistant tools, registration hand-off and Degree Navigator comparison (see `docs/PHASE_7_2_2.md`).
- **Engines.**

  | Engine | Version |
  |---|---|
  | Audit | 7.2.2 |
  | Planning | 6.7.0 |
  | Schedule | 6.8.0 |
  | WebReg parser | `webreg-layout-3` |

- **Migrations.**
  - Revision chain: `722academic01` → `722schedule01`. Both are additive.
  - Downgrades refuse to destroy confirmed registrations or multi-component current sections.

## Takeover repairs

| Found | Repair |
|---|---|
| Mojibake in the API router and Home page | Restored |
| Line-ending damage in 5 frontend files | Restored; the diffs now show only real changes |
| tsc errors in the progress test; a nested `<li>` | Fixed |
| A circular import after adding the imported week | `asOption` moved to `week-calendar` |
| 8 suite failures | 5 were intended behaviour whose tests weren't updated (updated); 1 was readiness setup (fixed); 1 was an unreviewed edit to the curated CS definition (reverted pending human review); 1 lifecycle rule is now enforced via `program_lifecycle.school_set_definition_changed` |
| The `722schedule01` downgrade silently dropped confirmed registrations | It now refuses |

## Real WebReg validation (local only; aggregate figures only)

Two real View / Print Schedule PDFs were checked outside the repository. No content was copied, printed or committed.

1. **Saved through "Microsoft Print To PDF".**
   - Every glyph is a vector outline: 0 fonts, 0 text objects, 0 images, about 19k path segments.
   - There is no character data, so reading it would require OCR, which this architecture deliberately does not use. `UNSUPPORTED_LAYOUT` is correct.
   - The upload error now tells students to use the browser's own "Save as PDF".
2. **Saved with the browser's own "Save as PDF".** This one has a text layer.
   - Header, term, weekday labels and the by-arrangement heading were all recognised.
   - It was refused because real grid cells print `course:section:index`, with a **colon** before the index; the parser had assumed a hyphen.
   - **Fixed** by accepting exactly one `:` or `-` before exactly five digits. Nothing else was relaxed.
   - **Result:** recognised, with 5 candidates, each with an exact index. Matched against archived public SOC data: **5 EXACT_MATCH**, 0 MULTIPLE_MATCHES, 0 NO_MATCH, 0 NEEDS_CONFIRMATION.

Both formats now have permanent, fully fabricated fixtures:
- `build_schedule_pdf(outlined=True)`;
- colon-form grids, which fail before the fix at the same parser line as the real document.

## Verification

| Check | Result |
|---|---|
| Frontend | tsc clean; vitest 72 passed; production build OK |
| Backend suite (after the parser fix) | 1050 passed, 4 skipped |
| Ingestion suite (PostgreSQL) | 961 passed, 1 skipped |
| Root combined suite | 2004 passed, 5 skipped (run before the 7 WebReg tests were added) |
| Migration roundtrip (disposable DB) | upgrade → downgrade → re-upgrade → `alembic check` OK |
| Academic failure injection | 19 faults, all detected (the first run missed 6, and each gained a test); WebReg/schedule faults re-run after the parser fix: 11/11 |
| Personalization / Core / auth injection | 13/13, 16/16, 18/18 detected (personalization F re-targeted to the 7.2.2 confirmation loop; it had become un-injectable) |
| Browser QA (`tests/visual/academic.mjs`, disposable stack, fabricated students) | 32 screens at 320–1600 px, light and dark: 0 overflow, 0 axe, 0 small targets; 34/34 assertions |
| Performance (disposable DB) | progress p50 58–105 ms with flat SQL per call and byte-identical output; WebReg parse 0.1 ms; matching 10 rows 29 ms; isolated extractor about 1 s (Windows spawn) |
| Privacy | no PDF or image added; no real names, identities, emails, IDs or secrets; every WebReg identity in the repo is fabricated (`01:999:*`) |

## Limitations

- **Real-format coverage:** one real browser-saved layout has been validated. Other WebReg variants may still fail closed, and the fix for those is new fabricated fixtures, never parser relaxation. Outlined-glyph PDFs remain unsupported by design (no OCR).
- **Progress scope:** modeled progress does not cover graduation credits, residency or combined additional programs, and says so on screen.
- **Program review:** the reverted CS-definition edit needs a human program review.
- **Dev database:** it was not migrated by this work. Run `alembic upgrade head` (additive) before trying 7.2.2 locally.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
