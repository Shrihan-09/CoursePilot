# Request Decision Trees

Status: **mostly design.** Section 7 (the academic reasoning path) is
implemented and tested as of Phase 3; flows 1-6 remain design.

How to read these: every branch that could produce a wrong academic claim
terminates in either a **deterministic check** or an **honest refusal**. There
is no branch where a plausible guess is an acceptable output.

---

## Common preamble

Every academic request begins here:

```
REQUEST
  │
  ├─ student identified? ─── no ──► ask / use anonymous mode (limited features)
  │
  ├─ program known? ──────── no ──► ASK. Never infer a major.
  │                                 A plan for the wrong program is worse
  │                                 than no plan and more convincing.
  │
  ├─ catalog year known? ─── no ──► ask, or default to matriculation year;
  │                                 state the assumption in the response
  │
  └─ authoritative data loaded for the relevant term?
        │
        ├─ no ──► INSUFFICIENT_DATA. Say which term is missing.
        │         Do not substitute another term's data.
        │
        └─ yes ─► continue
```

---

## 1. "What should I take next semester?"

```
PREAMBLE
  │
  ▼
IDENTIFY TARGET TERM
  │  explicit ("spring 2027") or inferred from today's date
  │  if the term has no ingested offerings ──► INSUFFICIENT_DATA
  ▼
LOAD ACADEMIC HISTORY
  │  completed courses, in-progress courses, credits, standing
  │  flag self-reported (unverified) entries — the audit inherits that status
  ▼
COMPUTE REMAINING REQUIREMENTS
  │  deterministic traversal of the requirement tree for the student's
  │  program_version. NOT an LLM step.
  │  unsatisfied + partial + indeterminate nodes
  ▼
FIND PREREQUISITE-ELIGIBLE COURSES
  │  for each candidate, evaluate the prerequisite expression tree
  │  against completed + in-progress courses
  │  ┌─ satisfied      ──► eligible
  │  ├─ not satisfied  ──► excluded (with reason, for the explanation)
  │  └─ unparsed       ──► eligible-but-flagged; student must confirm.
  │                        Not silently included, not silently dropped.
  ▼
RETRIEVE OFFERINGS FOR THE TARGET TERM
  │  eligible ∩ actually offered
  │  a perfect course that isn't offered is not a recommendation
  ▼
APPLY STUDENT CONSTRAINTS
  │  credit target, campus, time preferences, personal events
  │  hard constraints filter; soft constraints score
  ▼
GENERATE CANDIDATE PLANS  (LLM proposes, from this candidate set only)
  ▼
ENTITY RESOLUTION ──── unknown id ──► reject the proposal, replan
  ▼
VALIDATE (deterministic)
  │  prerequisites · credit load · conflicts · requirement fit · double-count
  ▼
valid? ── no ──► replan with findings (bounded) ──► NEEDS_REVISION if exhausted
  │
  yes
  ▼
RANK
  │  requirement progress · prerequisite unblocking (a course that opens
  │  many later courses is worth more) · load balance · preference fit
  ▼
EXPLAIN
   per course: which requirement it advances, why now, what it unblocks
   plus: every INDETERMINATE finding, stated plainly
```

**Failure modes this guards against**

| Failure | Guard |
|---|---|
| Recommending an unoffered course | Offering check before proposal |
| Recommending a course the student can't take | Prerequisite validator |
| Recommending an already-completed course | History exclusion |
| Overloading the student | Credit-load validator |
| Claiming a course satisfies a requirement it doesn't | Requirement validator |
| Confident advice on missing data | INSUFFICIENT_DATA / INDETERMINATE |

---

## 2. "Can I take COURSE X?"

```
RESOLVE COURSE
  │
  ├─ not found ──► exact-match failed; try fuzzy (pg_trgm)
  │                 ├─ close matches ──► "did you mean...?"
  │                 └─ nothing ────────► "no such course in our data"
  │                                       NOT "that course doesn't exist" —
  │                                       our data may be incomplete, and the
  │                                       difference matters to the student
  ▼
RETRIEVE PREREQUISITE / COREQUISITE TREE  (with raw_text)
  ▼
LOAD STUDENT COMPLETED + IN-PROGRESS COURSES
  ▼
EVALUATE THE EXPRESSION TREE
  │  and / or / not / min_grade / standing / permission
  │  apply course equivalencies
  │
  ├─ satisfied ─────────► eligible
  ├─ not satisfied ─────► ineligible + exactly which branch failed
  ├─ needs permission ──► "requires permission" — we cannot determine this
  └─ unparsed ──────────► INDETERMINATE + show raw_text + point to an advisor
  ▼
CHECK OFFERING for the term of interest
  ▼
CHECK RESTRICTIONS (major/school/year — TODO(rutgers-source))
  ▼
RETURN STRUCTURED RESULT
   eligible · findings · unmet prerequisites · offering status
   · sources · disclaimer
```

Note the four-way prerequisite outcome. Collapsing "needs permission" or
"unparsed" into yes/no is precisely how a student gets told they can take
something they cannot.

---

## 3. "What satisfies requirement X?"

```
PREAMBLE (program + catalog year are mandatory here —
          requirements are program-version-scoped)
  ▼
RESOLVE REQUIREMENT
  │  by code, name, or natural language ("the writing requirement")
  ├─ ambiguous ──► list candidates, ask
  └─ not found ──► "no such requirement in our data for this program version"
  ▼
RETRIEVE THE REQUIREMENT SUBTREE  (authoritative, with provenance)
  ▼
ENUMERATE OPTIONS from requirement_course_option
  │  never generated by the model — read from the database
  ▼
ANNOTATE PER OPTION (if a student is identified)
  │  already completed? · prerequisites met? · offered next term?
  │  · double-count allowed with other requirements?
  ▼
RETURN
   the rule (n_of / credits / all_of ...) · the options · annotations
   · sources · any custom/requires_human_review nodes flagged explicitly
```

If the requirement node is `rule_type = custom`, say so and show the published
text. Do not paraphrase an unformalized rule into a checkable one.

---

## 4. "Build me a schedule."

```
DETERMINE COURSE SET
  │  explicit list, or the output of flow #1
  ▼
VALIDATE ELIGIBILITY of every course (flow #2 per course)
  │  any ineligible ──► report before scheduling.
  │  Scheduling an ineligible course wastes everyone's time.
  ▼
RETRIEVE ALL SECTIONS for each course in the term
  │  no sections ──► course cannot be scheduled; report it
  ▼
LOAD PERSONAL EVENTS
  │  expand recurrence into concrete occurrences
  │  hard constraints → blocked intervals
  ▼
BUILD CONSTRAINT SET
  │  HARD: no time overlap · campus travel feasibility · required courses
  │        present · credit limits · closed-section rules
  │        (TODO(rutgers-source)) · hard personal events
  │  SOFT: preferred times · day compactness · campus consistency
  │        · instructor preference · gap minimization
  ▼
GENERATE CANDIDATES
  │  combinatorial search over section choices.
  │  This is deterministic search, NOT an LLM task — the model is bad at
  │  exhaustive combinatorics and cannot prove a conflict-free result.
  ▼
VALIDATE EACH CANDIDATE
  │  pairwise interval overlap across all section_meeting rows
  │  including multi-component sections (lecture + recitation)
  ▼
SCORE surviving candidates against soft constraints
  ▼
RANK and return the top N, each with its tradeoffs stated
  ▼
if none survive:
   report WHICH constraint eliminated everything, and what relaxing it
   would allow. "No schedule found" without a reason is useless.
```

**Why the optimizer is not an LLM:** conflict-freedom is a provable property.
Code can guarantee it; a model can only be usually right, and "usually" here
means a student shows up to two classes at once.

---

## 5. "Can I graduate on time?"

Sketched only; depends on flows 1–3.

```
PREAMBLE
  ▼
COMPUTE REMAINING REQUIREMENTS (deterministic)
  ▼
COUNT REMAINING TERMS to the target graduation date
  ▼
FEASIBILITY CHECK
  │  remaining credits ÷ terms ≤ max credits/term?
  │  are prerequisite CHAINS longer than the remaining terms?
  │    ← this is the real constraint. A student may have enough credits
  │      but a 4-deep prerequisite chain with only 3 terms left.
  │      Requires longest-path traversal of the prerequisite DAG.
  ▼
CHECK OFFERING CADENCE
  │  a course offered only in fall constrains sequencing
  │  TODO(rutgers-source): is offering cadence published, or must it be
  │  inferred from history? Inferred cadence is a PREDICTION, not a fact,
  │  and must be labeled as such.
  ▼
RETURN
   feasible / at-risk / not-feasible-as-planned
   · the binding constraint · what would change the answer
   · every assumption made explicit
```

This question deserves the most caution in the product. The honest answer is
often "based on the data we have, and assuming X, Y, Z — with these caveats."
A bare "yes" here is a promise CoursePilot is not positioned to make.

---

## 6. Cross-cutting rules

1. **Never infer the student's program.** Ask.
2. **Never substitute a different term's data** for a missing term.
3. **Never present INDETERMINATE as PASSED.**
4. **Never say a course doesn't exist** when the truth is that it isn't in our
   data.
5. **Always name the binding constraint** when returning a negative result.
6. **Always attach sources** to Rutgers-derived claims.
7. **Always disclaim**: CoursePilot is a planning aid; the student's official
   Rutgers advising record and academic advisor govern.

---

## 7. The academic reasoning path (Phase 3 — implemented)

This is the first flow in this document with a working implementation behind
it. **No LLM is involved at any step.**

```
USER REQUEST  ("what do I still need?")
      │
      ▼
IDENTIFY INTENT                       ← not implemented (Phase 6 router)
      │
      ▼
IDENTIFY STUDENT  ─── unknown ──────► ask; never assume
      │
      ▼
IDENTIFY PROGRAM  ─── unknown ──────► ASK. Never infer a major.
      │                                 A plan for the wrong program is worse
      │                                 than no plan and more convincing.
      ▼
IDENTIFY CATALOG YEAR ─ unknown ────► ask, or use matriculation year and
      │                                 STATE the assumption
      ▼
LOAD PROGRAM VERSION
      │   student.catalog_year MUST equal version.catalog_year
      │   mismatch ──► BLOCKING finding, status = INSUFFICIENT_DATA
      │                Never audit against another year's rules.
      ▼
LOAD REQUIREMENT GRAPH
      │   recursive tree for THIS version only
      │   no requirements ──► INSUFFICIENT_DATA (never "complete")
      ▼
LOAD COURSE ELIGIBILITY
      │   requirement_course_option: which courses CAN satisfy what
      ▼
LOAD STUDENT RECORD
      │   completed / in_progress / planned
      │   planned courses are excluded — an intention is not evidence
      │   failing grades excluded, with a WARNING finding
      ▼
ALLOCATE                              ← the hard step
      │   maximum bipartite matching, most-constrained-first
      │   deterministic: same inputs → same allocation
      ▼
EVALUATE each node against its allocation
      │   all_of     every child satisfied
      │   any_of     >= min_count children satisfied
      │   choose_n   >= min_count courses AND group constraints hold
      │   credits    >= min_credits accumulated
      │   course     the specific course completed
      ▼
DERIVE OVERALL STATUS
      ├── all satisfied ─────────────► COMPLETE
      ├── only in-progress remaining ► IN_PROGRESS
      ├── any indeterminate ─────────► INSUFFICIENT_DATA
      └── otherwise ─────────────────► INCOMPLETE
      ▼
STRUCTURED RESULT (Pydantic DegreeAuditResult)
      every requirement carries its status, its reason, its allocated
      courses, and the source prose it was curated from
```

### Where this flow refuses to guess

| Situation | Response |
|---|---|
| Program unknown | Ask. Never infer from coursework. |
| Catalog year mismatch | BLOCKING finding; refuse to audit. |
| No requirements defined | `INSUFFICIENT_DATA`, never `COMPLETE`. |
| Unknown requirement type | `INDETERMINATE`, never a pass. |
| Course not in the catalog | Reported unallocated, never invented. |
| In-progress coursework | `PROVISIONALLY_SATISFIED`, never `SATISFIED`. |
| Rule not modeled (grade/exclusion/residency) | Recorded as not evaluated; not silently ignored. |

### Requirement allocation (the substantive decision)

Rutgers publishes **no allocation rules** — the catalog says only "see a
computer science adviser". So CoursePilot defines its own, explicitly:

```
maximum bipartite matching (Kuhn's algorithm)
  left   = the student's countable courses
  right  = requirement SLOTS (a choose-5 contributes 5 slots)
  edge   = course is eligible for that requirement
  goal   = maximise filled slots
```

**Maximum cardinality alone is not enough** — found against real data.
`01:198:344` is eligible for both the required `CS_344` node and the 53-option
elective pool. Both are one slot, so the matching was indifferent, and it
filled an elective slot while reporting a *required* course unsatisfied.

Slots are therefore ordered **most-constrained-first**: fewer eligible options
wins. A `course` requirement (one possible course) outranks a large pool. This
never shrinks the matching — it only breaks ties among equally large matchings,
choosing the one that serves scarce requirements first.

Determinism comes from sorting both sides before matching, so the same inputs
always produce the same allocation. An audit that changed between page loads
would be untrustworthy regardless of correctness.

Full reasoning: `backend/app/services/audit/allocation.py`.

---

## 8. Program-level rules in the audit path (Phase 3.5)

Rules are evaluated **after** the requirement tree and can only ever make the
verdict worse, never better.

```
REQUIREMENT TREE EVALUATED
      │
      ▼
LOAD PROGRAM RULES for this ProgramVersion
      │
      ├── rule.is_evaluable == False ──► NOT_EVALUABLE
      │                                   + WARNING finding
      │                                   + degree can never be COMPLETE
      │
      ├── max_grade_count ──► count matching grades on COMPLETED courses
      │                       over allowed  ──► UNSATISFIED (BLOCKING)
      │
      ├── course_exclusion ─► always SATISFIED (it describes how credit
      │                       counts, it is not a condition to meet)
      │                       affected courses: no allocation, no credit
      │
      └── residency ────────► only if the data supports it
      ▼
DERIVE OVERALL STATUS
      ├── any rule UNSATISFIED ─────────────► INCOMPLETE
      ├── tree complete + rule NOT_EVALUABLE ► INDETERMINATE
      ├── all satisfied, nothing unchecked ──► COMPLETE
      └── otherwise ─────────────────────────► INCOMPLETE
```

### The guarantee this adds

> Every modeled requirement being satisfied is **not** the same as a complete
> degree.

If an authoritative rule exists that CoursePilot cannot check, the audit
returns `INDETERMINATE` and says which rule and why. A student is told to
confirm with an advisor rather than being told they are finished.

Exclusions are applied **before** allocation, so an excluded course cannot
satisfy a requirement even when it is otherwise eligible - `01:198:405` is a
300-level CS course and would otherwise count as an elective.
