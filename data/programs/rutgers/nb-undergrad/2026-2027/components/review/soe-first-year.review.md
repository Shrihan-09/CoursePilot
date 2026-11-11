# Review packet: `soe-first-year` (component, school_core)

- **Lifecycle:** `pending_review`
- **Encoded by:** manual encoding, Prompt 08 B1 (Claude Code session, 2026-11-11) - encoding is not review
- **Definition:** `data/programs/rutgers/nb-undergrad/2026-2027/components/soe-first-year.json`  semantic sha256 `a303a433d826afad`

## Sources

| id | role | status | retrieved (UTC) | representation | sha256 | url |
|---|---|---|---|---|---|---|
| `eng-catalog-first-year` | catalog | archived | 2026-11-11T19:09:07+00:00 | raw_html | `9913d2ee379fdb27` | https://newbrunswick-26-27-undergrad.catalogs.rutgers.edu/schools/eng/programs-study/first-year |
| `eng-catalog-programs-study` | catalog | archived | 2026-11-11T19:10:56+00:00 | raw_html | `de71815c025c38a2` | https://newbrunswick-26-27-undergrad.catalogs.rutgers.edu/schools/eng/programs-study/programs |

## Machine checks (not review)

- passed: **True**, quotes found verbatim: 13/13

## Review questions

- [ ] Confirm the two printed first-year programs (before and starting from Class of 2030) differ only in the Chemistry II footnote.
- [ ] Confirm 01:355:101 College Writing is required of every SoE student (no exemption path is stated on the archived page).

## What to verify

- [ ] Open each archived source (paths below) and find every quoted sentence.
- [ ] For each node, confirm the encoding says what its quote says: courses, counts, levels, grade floors, credit minimums, stages.
- [ ] Confirm nothing the sources require is missing - compare with 'rules not yet modeled' and 'unknowns'.
- [ ] Resolve or explicitly accept each OPEN conflict; never pick a side silently.
- [ ] Confirm admission/declaration rules are entry-stage requirements or labels, never completion requirements, and that no plan would assume admission.
- [ ] Answer every review question; 'changes requested' is a valid outcome.

## Requirement nodes

| node | type | parent | encoding | eligible rows |
|---|---|---|---|---|
| `SOE_FY` First-Year Program (004) | all_of |  |  | 0 |
| `SOE_FY_CHEM_159` General Chemistry for Engineers I | course | SOE_FY | courses: 01:160:159 | 1 |
| `SOE_FY_CHEM_171` Introduction to Experimentation | course | SOE_FY | courses: 01:160:171 | 1 |
| `SOE_FY_WRITING` College Writing | course | SOE_FY | courses: 01:355:101 | 1 |
| `SOE_FY_CALC_151` Calculus I for Mathematical and Physical Sciences | course | SOE_FY | courses: 01:640:151 | 1 |
| `SOE_FY_PHYS_123` Analytical Physics IA | course | SOE_FY | courses: 01:750:123 | 1 |
| `SOE_FY_ID3EA_101` Introduction to Data-Driven Design for Engineering Applications | course | SOE_FY | courses: 14:440:101 | 1 |
| `SOE_FY_CALC_152` Calculus II for Mathematical and Physical Sciences | course | SOE_FY | courses: 01:640:152 | 1 |
| `SOE_FY_PHYS_124` Analytical Physics IB | course | SOE_FY | courses: 01:750:124 | 1 |
| `SOE_FY_ID3EA_102` Integrated Data-Driven Design for Engineering Applications | course | SOE_FY | courses: 14:440:102 | 1 |
| `SOE_FY_STATICS` Engineering Mechanics: Statics | course | SOE_FY | courses: 14:440:221 | 1 |

### Verbatim quotes per node

- **SOE_FY**:
  - `eng-catalog-first-year`: "FIRST-YEAR PROGRAM Curriculum Code 004 (common to all four-year engineering programs)"
  - `eng-catalog-programs-study`: "Students in the first year of a four-year curriculum are listed under curriculum code 004."
- **SOE_FY_CHEM_159**:
  - `eng-catalog-first-year`: "01:160:159 General Chemistry for Engineers I (3cr)"
- **SOE_FY_CHEM_171**:
  - `eng-catalog-first-year`: "01:160:171 Introduction to Experimentation (1cr) *"
- **SOE_FY_WRITING**:
  - `eng-catalog-first-year`: "01:355:101 College Writing (3cr) *"
- **SOE_FY_CALC_151**:
  - `eng-catalog-first-year`: "01:640:151 Calculus I for Mathematical and Physical Sciences (4cr)"
- **SOE_FY_PHYS_123**:
  - `eng-catalog-first-year`: "01:750:123 Analytical Physics IA (2cr)"
- **SOE_FY_ID3EA_101**:
  - `eng-catalog-first-year`: "14:440:101 Introduction to Data-Driven Design for Engineering Applications (2cr)"
- **SOE_FY_CALC_152**:
  - `eng-catalog-first-year`: "01:640:152 Calculus II for Mathematical and Physical Sciences (4cr)"
- **SOE_FY_PHYS_124**:
  - `eng-catalog-first-year`: "01:750:124 Analytical Physics IB (2cr)"
- **SOE_FY_ID3EA_102**:
  - `eng-catalog-first-year`: "14:440:102 Integrated Data-Driven Design for Engineering Applications (2cr)"
- **SOE_FY_STATICS**:
  - `eng-catalog-first-year`: "14:440:221 Engineering Mechanics: Statics (3cr)"

## Rules not yet modeled

- Semester placement of the first-year courses ('* May be taken in the second semester of the first year').  
  _why:_ Layout and timing guidance, not a completion rule.  
  `eng-catalog-first-year`: "* May be taken in the second semester of the first year."

## Unknowns

- (none)

## How to record your decision

```
Components have no review command yet: record the decision in the PR / review notes; the component stays 'pending_review' (and every audit and index label says so) until a component review command exists.
```
