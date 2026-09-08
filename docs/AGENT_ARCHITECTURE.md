# Agent Architecture

Status: **design only.** No agent, router, planner, or validator is
implemented. `AnthropicProvider` is a stub that raises `NotImplementedError`.

---

## 1. What the agent is and is not

The agent is a **proposal generator operating over a retrieved candidate set**.

It is not an oracle. It does not know Rutgers requirements. It does not decide
whether a plan is valid. Its value is in the parts of the problem that are
genuinely fuzzy — interpreting vague questions, weighing tradeoffs, sequencing
courses sensibly, explaining a plan in plain language.

Everything crisp is handled by code.

---

## 2. Flow

```
                        USER REQUEST
                             │
                             ▼
                  ┌──────────────────────┐
                  │    INTENT ROUTER     │  typed RouteDecision
                  └──────────┬───────────┘
                             │
              ┌──────────────┴──────────────┐
              │                             │
    missing information?              enough to proceed
              │                             │
              ▼                             ▼
     ask a clarifying question     ┌──────────────────────┐
     (do not guess the major)      │   CONTEXT ASSEMBLY   │
                                   │  load selected skills │
                                   └──────────┬───────────┘
                                              ▼
                                   ┌──────────────────────┐
                                   │      RETRIEVAL       │
                                   │ authoritative records │
                                   └──────────┬───────────┘
                                              ▼
                                   ┌──────────────────────┐
                                   │   PLANNING AGENT     │
                                   │  typed proposal only  │
                                   └──────────┬───────────┘
                                              ▼
                                   ┌──────────────────────┐
                                   │  ENTITY RESOLUTION   │  ◄── hallucination gate
                                   │ every id must exist   │
                                   └──────────┬───────────┘
                                              ▼
                                   ┌──────────────────────┐
                                   │ DETERMINISTIC VALIDATOR │
                                   └──────────┬───────────┘
                                              ▼
                                        valid?
                                    ┌─────┴─────┐
                                  yes           no
                                    │            │
                                    ▼            ▼
                          STRUCTURED OUTPUT   attempts left?
                                    │        ┌────┴────┐
                                    ▼      yes         no
                                   UI       │           │
                                            ▼           ▼
                                    REPLAN with    NEEDS_REVISION
                                    findings fed   + partial plan
                                    back in        + blocking findings
                                            │
                                            └──► VALIDATE AGAIN
```

---

## 3. Components

### 3.1 Intent router

Input: the request plus minimal student context.
Output: a **typed** `RouteDecision`:

```
intent            course_lookup | eligibility_check | requirement_query
                  | semester_plan | full_schedule | general_question
skills_to_load    list of skill ids
tools_required    list of tool names
missing_context   what we need but don't have
confidence        low routing confidence triggers a clarifying question
```

Typed, not prose, so a bad route is a structured error the system can detect —
not a subtly odd answer.

**When context is missing, ask.** If the student's program is unknown, the
router asks. It does not assume a major. A plan built for the wrong program is
worse than no plan, and more convincing.

### 3.2 Skill loading

Loads only what the router selected. See `skills/SKILL_FORMAT.md`.

The planner may request one additional skill mid-run if it detects a gap.
Bounded to one extra load, to prevent a runaway loading everything.

### 3.3 Retrieval

Returns **candidate entity ids** from the database. The planner selects from
this set. See `docs/RAG_ARCHITECTURE.md`.

### 3.4 Planning agent

The only LLM call that touches academic content. Constraints:

- Structured output. The response is parsed into `PlanResponse`; prose is not
  consumed as data.
- Courses are chosen **from the retrieved candidate set**, referenced by id.
- Its `rationale` and `summary` fields are advisory. They render as
  explanation; nothing downstream depends on them.
- It never sets `status` or populates `validation`. Those come from the
  validator.

### 3.5 Entity resolution — the hallucination gate

Between planning and validation, every id in the proposal is looked up.

- Unknown `course_id` → **hard failure**, not a warning.
- Course id not in the retrieved candidate set → hard failure.
- Unknown `requirement_id` → hard failure.

This is a cheap database check that catches the single most likely and most
damaging failure: a confidently recommended course that does not exist.

### 3.6 Deterministic validator

Pure functions. No LLM, no network. See `backend/app/services/validation/`.

| Validator | Checks | On missing data |
|---|---|---|
| `course_exists` | course is in the catalog | fail |
| `prerequisite` | expression tree satisfied by completed courses | INDETERMINATE if prereqs `unparsed` |
| `corequisite` | coreqs present in the same term | INDETERMINATE |
| `offering_available` | offered in the target term | INDETERMINATE if term not ingested |
| `requirement_satisfaction` | course actually satisfies the claimed requirement | INDETERMINATE |
| `credit_load` | within program/term credit limits | INDETERMINATE — **TODO(rutgers-source)**: official limits |
| `schedule_conflict` | no overlapping meeting intervals | fail on overlap |
| `duplicate_credit` | no improper double-counting | conservative: disallow |
| `program_applicability` | course counts toward this program | INDETERMINATE |

**The `INDETERMINATE` column is the point.** Missing data never yields
`PASSED`. It yields "we could not check this," and the UI must show that
differently from a confirmed pass.

### 3.7 Replan loop

Bounded (proposed: 2 retries). Failed findings are fed back so the planner
knows what to fix.

After the bound, return `NEEDS_REVISION` with the partial plan and the
blocking findings. **The loop never gives up quietly into a plausible answer.**

Bounded because an unbounded loop is an unbounded bill, and repeated failure
usually signals missing data rather than a bad plan — more attempts will not
fix that.

---

## 4. Safeguards

| Risk | Safeguard |
|---|---|
| Hallucinated course | Entity resolution against the DB; must be in the retrieved set |
| Hallucinated requirement | Requirement ids resolved; satisfaction verified against `requirement_course_option` |
| Invalid prerequisite claim | Prerequisite validator walks the actual expression tree |
| Schedule conflict | Interval comparison over `section_meeting` rows |
| Invented Rutgers policy | Policy claims must cite a `SourceRef`; uncited policy text is stripped before display |
| Unsupported claim | Findings without sources cannot be `BLOCKING` |
| Stale data presented as current | `verification` status surfaced in the UI |
| Overconfidence on missing data | `INDETERMINATE` propagates to `has_unverifiable_claims` |
| Runaway cost | Bounded replans; bounded skill loads; capped retrieval |
| Wrong-program plan | Router asks rather than assumes |

---

## 5. What the model is never allowed to do

1. Set `PlanStatus` or any field of `ValidationReport`.
2. Introduce a course, requirement, or section id not present in retrieval.
3. Assert a Rutgers policy without a `SourceRef`.
4. Cause `INDETERMINATE` to be reported as `PASSED`.
5. Be the sole basis for anything a student would act on.

These are enforced by the pipeline's shape, not by prompt instructions —
because prompt instructions are guidance, and guidance is occasionally ignored.

---

## 6. Observability

Every run should record: the route decision, skills loaded, retrieval queries
and returned ids, the raw model proposal *before* resolution, resolution
failures, validator findings, replan count, and token usage.

The raw proposal matters most. Without it, debugging "why did it suggest
that?" is guesswork, and hallucination rates cannot be measured.

`PlanResponse.trace_id` is reserved for correlation.

---

## 7. Gating criteria — before any of this is built

1. The data model is implemented and migrated.
2. Real Rutgers data is ingested for at least one program, with provenance.
3. Validators exist and are unit-tested, **including cases that must fail**.
4. Retrieval works and is measured against an eval set.

Building the agent first would mean building on data that does not exist, and
the natural way to fill that gap is to let the model invent it. That is the
failure mode this whole architecture is designed to prevent.
