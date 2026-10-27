# Phase 7.2.1: Student Context + Personalized Planning Foundation

## Summary

Manual use of Phase 7.2, followed by a 24-persona audit of fabricated
students against the real engines and Rutgers data, showed that CoursePilot
mixed two things: **what is true** about a student and **what the student
wants**. This phase separates them and makes the planner respect the
difference.

```
ACADEMIC CONTEXT (true)          PLANNING PROFILE (wanted)
confirmed record, engines   +    pace, target / max load, seasons, class times, goals (exploring)
            \                      /
             planning readiness (server-owned)
                       |
             Planning Engine 6.6.0 (pace, target vs hard max, validated levelling)
                       |
             Schedule: My current schedule (confirmed sections) | Plan a semester
```

## Audit: reported issues and what else it found

| # | Issue | Sev | Root cause | Disposition |
|---|---|---|---|---|
| U1 | "First semester" is ambiguous | P2 | It meant the plan's first term | **Fixed**: "Plan starting semester", with options owned by the server |
| U2 | Only a credit ceiling exists | P2 | There was no target concept | **Fixed**: a soft target and a hard maximum |
| U3 | Fall 2028 had one course | P2 | **(E) greedy earliest-first filling plus (D) the 16-credit cap**. The course had prerequisites met by history, Fall offerings and 78 alternatives; it was stranded by the remainder (3 of 12 personas showed the same) | **Fixed**: validated levelling |
| U4 | No personalization | P2 | Settings existed only per request | **Fixed**: a confirmed Planning Profile and a guided form |
| U5 | Double-major intent | P2 | One program per student; no double-count rules | Intent recorded as "exploring"; **combined planning DEFERRED (design §49.8)** |
| U6 | Early graduation assumed | P2 | Filling to the maximum races to finish | **Fixed**: Balanced by default; capability is surfaced, never applied |
| U7 | In-progress semantics | - | Verified correct | Not a bug; protected by fault E |
| U8 | Current schedule | P2 | Records carry no sections | **Fixed**: student-confirmed sections only |
| N1 | Plans start in the PAST (behind, returning and near-complete students; one plan for 2022 was empty) | **P1** | No notion of "now" | **Fixed**: the start is always a future term |
| N2 | "X of 51 degree credits" | **P1** | 51 is the major's minimum, not the degree's | **Fixed**: wording now says the program's modelled minimum, not the total to graduate |
| N3 | "Planned" entries push the start later and get planned again | P2 | The floor counted every status | **Fixed**: they no longer move the floor; readiness notes they are unused |
| N4 | A light-load student runs out of horizon | P2 | The horizon was fixed at 8 | **Fixed**: the horizon follows pace (Lighter 12) |
| N5 | No warning when the current term isn't recorded | P2 | No current-term awareness | **Fixed**: a readiness warning |
| N6 | AP and transfer credit need a term code | P3 | Data model | Deferred to the transfer/AP coverage work |
| N7 | No soft schedule preferences | P3 | The Schedule Engine has hard limits only | Profile defaults pre-fill the hard limits, which are labelled; soft ranking deferred |
| N8 | Adaptive AI interview | - | The LLM adapters lack native structured output, a distributed limiter and a budget | **Deferred** to the assistant phase; a deterministic form now, **not presented as AI** |

No P0 (academic, security or data-integrity) issue was found.

## What changed

**Backend**

- **`student_planning_profile`** (migration `b491ce3a2784`, additive):
  - pace, target and maximum load, course cap, horizon, summer and winter,
    schedule defaults, and goals recorded as exploring;
  - written only by `PUT /student/planning-profile` (confirmation; the
    `version` bumps);
  - the body is `extra="forbid"`, so a course sent as a "preference" is a 422.
- **`GET /student/planning-readiness`**: `needs_program` /
  `needs_academic_history` / `needs_planning_profile` / `ready_with_warnings`
  / `ready`, with checks. `POST /student/plans` refuses (409) unless ready.
- **Start terms** (`calendar.py`; `ACADEMIC_TODAY` pins the date):
  - the default is the first future Fall/Spring term after both today and the
    record;
  - starts in the past or inside the record are refused;
  - the context reports the history start, record-through, current term and
    the accepted starts.
- **Saved plans:**
  - the body is the start term only; constraints come from the confirmed
    profile;
  - new stale reason `planning_preferences_changed`;
  - `start_term_passed` also fires when time passes the start.
- **Planning Engine 6.6.0:**
  - **Paces:** BALANCED aims for the target and passes it only when that
    lands closer; LIGHTER never exceeds the target; FINISH_EARLIER fills
    toward the maximum.
  - **The hard maximum** is never exceeded.
  - **Levelling** compresses a stranded final term, then makes the moves
    that most reduce load spread.
  - **Every move is re-validated** by the engine's own checks: offering
    evidence and prerequisites re-checked for the new term, order preserved,
    co-requisite groups fixed, caps held. One audit must reproduce the same
    progress and allocation, or every move is reverted. UNKNOWN can never be
    moved in.
  - **Observations:** `earlier_completion_possible` ("currently modeled
    requirements… not a graduation date… your plan has not been changed") and
    `light_term`, with the reason.
  - `pace=None` keeps the Phase 6.5 behaviour exactly.
- **My current schedule:**
  - `GET /student/current-schedule` and `GET`/`PUT`/`DELETE .../sections`;
  - sections exist only once the student confirms the exact index;
  - the transcript's section number only marks a match;
  - a removed section is reported, never retargeted.

**Web**

- **`/plan/personalize`:** preferences, then "Here's how CoursePilot will
  plan for you", then explicit confirmation. It never re-asks the record and
  is editable from Plan and Settings.
- **Plan:** follows server readiness, uses the server's start options, shows
  pace, target and maximum as preferences, and shows observations.
- **Schedule:** "My current schedule" and "Plan a semester" tabs. Class-time
  limits come from the profile.
- **Home:** the next step follows readiness.
- **Credit wording:** fixed on Degree and Home.
- **Frontend term arithmetic removed;** the server owns valid terms.

## Plan quality (fabricated students, real data; `scripts/plan_quality.py`)

| Student | 6.5 greedy (max 15) | Balanced 15/18 | Lighter 12/12 | Finish earlier 15/18 |
|---|---|---|---|---|
| third-year | 15 / 14 / 15 / **3** | **16 / 16 / 15** | 12 / 11 / 12 / 12 | 16 / 14.5 / 15 |
| second-year | 15 / 15 / 15 / 12 | 16 / 16 / 15 / 12 | 5 terms, ≤ 12 | 16 / 16 / 12 / 13.5 |

**Persona re-run (12 personas):**

- no one-course final semesters;
- every start is a future term;
- the returning student's plan is no longer empty.

**Performance:**

- plan generation is about 1.5–5 s, against about 1.4–4.4 s for 6.5 on the
  same personas;
- the outlier is "Lighter, empty record" (11 terms, ~11.8 s), because more
  terms need more projected audits;
- the move check caches eligibility per (course, term, earlier plan) and is
  bounded to 40 iterations × 25 candidates.

## Tests

| Suite | Result |
|---|---|
| `backend/tests/test_personalization_api.py` (profile, readiness, current schedule) | 19 passed |
| `backend/tests/test_saved_plan_api.py` (readiness gate, profile-only body, returning student, planned rows, preference and time staleness) | 33 passed |
| `ingestion/tests/test_planning_pace.py` (every pace against the invariants and the order oracle, target vs maximum, earlier completion, determinism, `pace=None` unchanged) | 13 passed |
| Frontend vitest | 65 passed |
| `tests/visual/core.mjs` (light and dark, 320–1600 px) | **102 screens, 0 findings; 54/54 assertions** |
| `scripts/inject_personalization_failures.py` | **13/13 DETECTED** |

The first injection run **missed two faults**; both are reported, not hidden:

- **B was an equivalent mutant:** BALANCED's closeness rule kept terms near
  the target anyway. It now ignores the target outright.
- **M had only a conditional test.** It now has a deterministic one.

**Final regression** (`coursepilot_test` on PostgreSQL 16; full logs kept):

| Suite | Before (main `8bf1dac`) | After |
|---|---|---|
| Frontend typecheck / vitest / production build | clean / 61 / built | **clean / 65 passed / built** |
| Root (PG) | 1892 passed, 5 skipped | **1932 passed, 5 skipped** |
| Backend (PG) | 962 passed, 4 skipped | **989 passed, 4 skipped** |
| Ingestion PG: planning engine, pace, schedule engine, linked components | 68 passed | **81 passed** |
| Migration `b491ce3a2784`: upgrade, downgrade, re-upgrade and `alembic check` on a throwaway database | - | **OK** |
| Failure injection, 7.2.1 personalization | - | **13/13 DETECTED** |
| Failure injection, 7.2 core | 16/16 | **16/16 DETECTED** |
| Failure injection, 7.1 auth | 18/18 | **18/18 DETECTED** |

**What the first regression run caught (fixed in `b26e4ba`, all reported):**

- **An injected fault was left on disk after its run.** The repo is under
  OneDrive, and its sync appears to have raced the restore. That made the
  first 7.2 core injection results untrustworthy.
  - Nothing was committed, and the file was restored from git.
  - All three injectors now refuse to inject into a modified file, and they
    read every restore back (retry, else stop).
  - All three suites were then re-run on a clean tree, with the results
    above.
- **Two 7.2 core faults no longer matched** the code 7.2.1 changed. They
  now follow it.
- **The root suite didn't collect** because the new test marker was
  unregistered at the root.
- **A pre-existing test-fixture flake:** 6-hex synthetic subject codes
  collided with old rows. They now use 8.

**Not rerun:** the full ingestion SQLite and retrieval suites, and the 6.8,
6.9 and 7.0 injections. None of the code they exercise changed.

The secrets/PII scan of the full diff is clean. The persona audit and every
QA student used fabricated data, and the QA accounts were deleted from the
dev database.

## Remaining limitations

- **Multi-program planning is design only** (§49.8): data model, allocation,
  planner, fingerprints and phasing are specified. The profile records intent
  only.
- **The adaptive LLM interview is deferred** to the assistant phase.
- **Soft schedule preferences are deferred,** as is the AP/transfer term
  requirement.
- **Season boundaries are approximate;** they affect only the default start.
- **Plans cannot be hand-edited;** "Lighter" on an empty record takes ~12 s.
- **Spring 2027 sections** are not published in local data.
- **Existing saved plans** now show `engine_updated` and need regenerating
  after personalization.

## Not done (by design)

- No LLM in academic decisions or planning.
- No guessed sections.
- No fabricated double-major support.
- No WebReg automation.
- No real student data in tests.
- The PR is not merged.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
