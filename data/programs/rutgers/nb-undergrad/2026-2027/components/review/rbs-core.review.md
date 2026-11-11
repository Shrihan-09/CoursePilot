# Review packet: `rbs-core` (component, gen_ed)

- **Lifecycle:** `pending_review`
- **Encoded by:** manual encoding, Prompt 08 B1 (Claude Code session, 2026-11-11) - encoding is not review
- **Definition:** `data/programs/rutgers/nb-undergrad/2026-2027/components/rbs-core.json`  semantic sha256 `0f50b88f3ff3ccd0`

## Sources

| id | role | status | retrieved (UTC) | representation | sha256 | url |
|---|---|---|---|---|---|---|
| `rbs-catalog-programs` | catalog | archived | 2026-11-11T19:09:19+00:00 | raw_html | `87b56becf9a97ce2` | https://newbrunswick-26-27-undergrad.catalogs.rutgers.edu/schools/rbsnb/degree-requirements/programs-majors-minors/programs |
| `rbs-catalog-credits-residency` | catalog | archived | 2026-11-11T19:09:16+00:00 | raw_html | `e17ca7bd44ec016c` | https://newbrunswick-26-27-undergrad.catalogs.rutgers.edu/schools/rbsnb/degree-requirements/credits-residency |
| `myrbs-degree-requirements` | department | failed (open evidence gap) | 2026-11-11T19:09:31+00:00 | failed | `` | https://myrbs.business.rutgers.edu/undergraduate-new-brunswick/degree-requirements |
| `myrbs-policies-procedures` | department | failed (open evidence gap) | 2026-11-11T19:09:29+00:00 | failed | `` | https://myrbs.business.rutgers.edu/undergraduate-new-brunswick/policies-procedures |
- `myrbs-degree-requirements`: HTTP 403 to a plain GET on 2026-11-11: open evidence gap.
- `myrbs-policies-procedures`: HTTP 403 to a plain GET on 2026-11-11: open evidence gap.

## Machine checks (not review)

- passed: **True**, quotes found verbatim: 10/10

## Review questions

- [ ] Is RBS's Rutgers Core the same goal-code structure as SAS/SEBS Core (CC: CCD/CCO; Areas of Inquiry: NS/HST/SCL/AH; Cognitive Skills: WC/WCr/WCd/QQ/QR)? If an official RBS source says so, the three nodes can use SOC certifications instead of an unknown list.

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
| `RBS_CORE` Rutgers Core (general education) | all_of |  | min_course_credits=3<br>graded_only=True | 0 |
| `RBS_CORE_CC` Contemporary Challenges | choose_n | RBS_CORE | min_count=2<br>**course list UNKNOWN** (source defers / unarchived) | 0 |
| `RBS_CORE_AOI` Areas of Inquiry | choose_n | RBS_CORE | min_count=6<br>**course list UNKNOWN** (source defers / unarchived) | 0 |
| `RBS_CORE_CSP` Cognitive Skills and Processes | choose_n | RBS_CORE | min_count=5<br>**course list UNKNOWN** (source defers / unarchived) | 0 |

### Verbatim quotes per node

- **RBS_CORE**:
  - `rbs-catalog-programs`: "Rutgers Core (general education) Requirements (minimum 30 credits)"
  - `rbs-catalog-programs`: "all Rutgers Business School: Undergraduate-New Brunswick students must complete the following Rutgers core curriculum requirements:"
  - `rbs-catalog-programs`: "All courses must be at least 3 credits and must be taken for degree credit. Pass/no credit courses may not be used to fulfill these requirements. Students should see Degree Navigator for specific courses and conditions."
- **RBS_CORE_CC**:
  - `rbs-catalog-programs`: "1) Contemporary Challenges (two courses)"
  - note: Course list not stated in the catalog (deferred to Degree Navigator).
- **RBS_CORE_AOI**:
  - `rbs-catalog-programs`: "2) Areas of Inquiry (six courses)"
  - note: Course list and any per-area distribution not stated in the catalog (deferred to Degree Navigator).
- **RBS_CORE_CSP**:
  - `rbs-catalog-programs`: "3) Cognitive Skills and Processes (five courses)."
  - note: Course list and any per-skill distribution not stated in the catalog (deferred to Degree Navigator).

## Rules

- `RBS_MIN_GPA` (min_gpa, evaluable=True): Cumulative GPA of at least 2.000
  - `rbs-catalog-credits-residency`: "All students must earn at least 120 degree credits, with a minimum cumulative grade-point average of 2.000."
- `RBS_45_BUSINESS_CREDITS_NB` (residency, evaluable=False): 45 business credits at Rutgers-New Brunswick
  - not evaluated: Counts business CREDITS taken at Rutgers-New Brunswick; which courses are 'business credits' is not defined on the archived page, and attendance campus is not certified on the record.
  - `rbs-catalog-credits-residency`: "Rutgers Business School: Undergraduate-New Brunswick (RBS-UNB) students must complete 45 business credits at Rutgers University-New Brunswick."

## Rules not yet modeled

- Which courses fill each Rutgers Core category for RBS students, and any per-goal distribution inside a category.  
  _why:_ The catalog defers to Degree Navigator; the myRBS pages returned HTTP 403. Recorded as unknown, never borrowed from another school.
- Rutgers Core minimum of 30 credits.  
  _why:_ Thirteen courses of at least 3 credits each already exceed 30; no separate credit node is needed until a source states otherwise.
- Students matriculated in RBS-UNB must receive written prior approval to take courses outside of Rutgers-New Brunswick.  
  _why:_ An approval process, not a degree requirement CoursePilot can check.  
  `rbs-catalog-credits-residency`: "Students matriculated in RBS-UNB must receive written prior approval to take courses outside of Rutgers-New Brunswick."

## Unknowns

- Course lists for the three Rutgers Core categories (Degree Navigator).
- RBS Pass/No Credit policy beyond 'Pass/no credit courses may not be used to fulfill these requirements' (myRBS, HTTP 403).

## How to record your decision

```
Components have no review command yet: record the decision in the PR / review notes; the component stays 'pending_review' (and every audit and index label says so) until a component review command exists.
```
