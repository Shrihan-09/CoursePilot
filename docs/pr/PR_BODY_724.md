# Phase 7.2.4: Current Rutgers Term Intelligence, Planning Strategy & Course/Professor Intelligence Foundations

## Summary

CoursePilot planned Spring 2027 while saying *"Spring 2027 isn't published yet; Rutgers offered it in Spring 2026."* The cause:
- Rutgers' public Schedule of Classes (SOC) had **begun publishing Spring 2027**. It was empty on Oct 14 and held 4,520 courses and 11,233 sections on Oct 19.
- CoursePilot never re-checked, so the planner fell back to Spring 2026 history.
- The UI then described a gap in **CoursePilot's data** as a fact about **Rutgers**.

This phase fixes that generically and adds the strategy, change-intent and intelligence foundations.

```
term discovery (rolling window, low frequency)  ->  rutgers_term: PUBLISHED?
term ingestion + validation                     ->  INGESTED?  ->  READY
planner offering evidence:  READY -> term data decides (history never does)
                            published, not loaded -> UNKNOWN (pending)
                            unpublished / unchecked -> labelled history
```

## Implemented

- **Term discovery and registry** (`rutgers_term`, migration `724currentterm01`).
  - It probes a rolling window computed from today. There is no hard-coded term, and SOC has no term-list endpoint.
  - Probes are sequential and paced, and payloads are archived.
  - "Published", "ingested" and "ready" are separate states. A failed request never counts as "not published".
  - Run with `python -m coursepilot_ingestion.term_cli discover|ingest|status`.
- **Ingestion validation.** The existing SOC pipelines load the archived payload. Coverage becomes `complete` only after the hash, rejected-record and count checks all pass. Already-validated data is never downgraded.
- **Target-term authority** (`services/term_authority.py`). Once a term is READY, a course missing from it is `not_offered_in_term`. A published-but-unloaded term is `target_term_data_pending` (UNKNOWN, not placeable). Students see one of five statuses:

  | Status | Meaning |
  |---|---|
  | CONFIRMED | listed in the target term's data |
  | NOT_FOUND_IN_PUBLISHED_TERM | the term is ready and doesn't list it |
  | HISTORICALLY_LIKELY | unpublished term; offered in past same-season terms |
  | INSUFFICIENT_HISTORY | offered only in other seasons |
  | UNKNOWN | pending data, or no evidence |

- **Saved plans.** Two new stale reasons: `offering_assumption_contradicted` and `target_term_published`. Plans also note pending semesters. Courses are never swapped silently.
- **Wording.** The UI says "CoursePilot has no Spring 2028 schedule from Rutgers". It says "Rutgers hasn't published …" only after an actual check.
- **Planning strategy.** Options are `balanced`, `major_first`, `requirements_first` (school/Core) and `unlock_future_options`. "Finish sooner" stays the existing *Finish earlier* pace.
  - Strategy is one ranking key, applied after dependency priority and only over valid courses.
  - It is carried only through the confirmed profile, and the form and summary show it.
  - Planning engine version: **6.8.0**.
- **Course-change intent** (read-only). Seven intents with stated consequences. Replacements come from the requirement's pool and are never marked eligible.
- **Section ranking**, separate from course ranking. Structural validity filters first. Live open/closed is a soft key only. Professor evidence counts only on a verified identity.
- **Intelligence foundation.**
  - Instructor identity is conservative: a human-verified link is required, and initial-only or ambiguous names never attach.
  - External sources are gated by recorded access status.
  - First-party feedback is aggregate-only, with a minimum of 5 responses and evidence-only wording. Missing data is "Not enough data".
- **Source authority map:** `docs/SOURCE_AUTHORITY.md`.

## Investigated, not implemented

| Source | Finding | Status |
|---|---|---|
| CSP | NetID login page; field-level differences can't be proven without authentication | blocked by authentication |
| WebReg | CAS / RUID+PAC login | student-provided PDF only (7.2.2) |
| Degree Navigator | CAS redirect | future student-provided comparison |
| Rutgers SIRS | NetID-permissioned; terms forbid aggregation without consent | blocked by permission |
| Rate My Professors | terms prohibit automated access and reuse without written permission | blocked by permission; interface only |
| Reddit | Data API requires an approved application | pending approval; interface only |

**Source disagreement (Spring 2027):** `openSections.json` lists 15,205 indexes against 11,233 NB sections; 4,189 are unknown to the NB payload, and 23 sections marked open are missing from the list. This is recorded per term as counts, and neither source overwrites the other.

## Real-data result

- **Discovery (live SOC, throwaway database):**

  | Term | Status |
  |---|---|
  | Summer 2026, Fall 2026, Winter 2027, **Spring 2027** | published |
  | Summer 2027, Fall 2027 | not published |

- **Spring 2027 ingestion:** 4,520 courses and 11,233 sections were loaded and validated, matching the payload exactly. Authority is **ready**.

## Verification

| Check | Result |
|---|---|
| New tests | 14 term-authority, 15 strategy (+1 API), 6 change-intent, 9 intelligence, 1 wording |
| Backend suite | 1,060 passed, 4 skipped, 1 failed (a planning-engine version pin to 6.7.0); after the fix that file passes 13/13 |
| Ingestion suite | 996 passed, 1 skipped |
| Failure injection (`scripts/inject_term_failures.py`) | 15/15 detected; the first run missed 1, which gained a test |
| Existing injectors re-run | personalization 13/13, academic 19/19 |
| Migration roundtrip | upgrade → downgrade → re-upgrade → `alembic check` OK; the downgrade refuses to destroy feedback, identities or strategies |
| Performance (`main` vs branch, identical throwaway DBs, 21 runs) | p50 within noise (744/488/514 vs 737/810/614 ms); +4 SQL per plan |
| Frontend | tsc clean, vitest 73/73, production build OK |
| Privacy | no credentials, cookies, CAS sessions, real documents or student data |

## Limitations / future work

- **Production scheduling:** term discovery and ingestion are CLI jobs; running them on a schedule is left to operations.
- **Failed re-ingest:** a failed re-ingest of a *newer* payload can leave rows from that payload in place. The term stays READY on its earlier validated hash.
- **Applying changes:** applying a change intent, and a UI for it, are future work.
- **Professor and feedback signals:** feedback collection UI/API, plus Reddit and RMP adapters, await product work or permission.
- **Degree Navigator and CSP:** student-provided comparisons are design-only.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
