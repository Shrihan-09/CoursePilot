# Review packet: `rbs-entry` (component, entry_stage)

- **Lifecycle:** `pending_review`
- **Encoded by:** manual encoding, Prompt 08 B1 (Claude Code session, 2026-11-11) - encoding is not review
- **Definition:** `data/programs/rutgers/nb-undergrad/2026-2027/components/rbs-entry.json`  semantic sha256 `d87bbe54369a4438`

## Sources

| id | role | status | retrieved (UTC) | representation | sha256 | url |
|---|---|---|---|---|---|---|
| `rbs-catalog-programs` | catalog | archived | 2026-11-11T19:09:19+00:00 | raw_html | `87b56becf9a97ce2` | https://newbrunswick-26-27-undergrad.catalogs.rutgers.edu/schools/rbsnb/degree-requirements/programs-majors-minors/programs |
| `rbs-catalog-admissions` | catalog | archived | 2026-11-11T19:09:22+00:00 | raw_html | `86ce23f7ddd092e0` | https://newbrunswick-26-27-undergrad.catalogs.rutgers.edu/schools/rbsnb/gen-info/admissions |
| `rbs-transfer-eligibility` | department | failed (open evidence gap) | 2026-11-11T19:09:27+00:00 | failed | `` | https://www.business.rutgers.edu/undergraduate-new-brunswick/transfer-eligibility |
| `myrbs-policies-procedures` | department | failed (open evidence gap) | 2026-11-11T19:09:29+00:00 | failed | `` | https://myrbs.business.rutgers.edu/undergraduate-new-brunswick/policies-procedures |
- `rbs-transfer-eligibility`: HTTP 403 to a plain GET on 2026-11-11: open evidence gap.
- `myrbs-policies-procedures`: HTTP 403 to a plain GET on 2026-11-11: open evidence gap.

## Machine checks (not review)

- passed: **True**, quotes found verbatim: 9/9

## Review questions

- [ ] From an official RBS source, which courses satisfy each foundational course (microeconomics, macroeconomics, calculus, introductory statistics)?
- [ ] Do the entry requirements differ for first-year direct admits, school-to-school and external transfers? (Only the school-to-school route is a pre-business stage.)

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
| `RBS_FOUNDATIONS` Foundational courses before admission to RBS | all_of |  |  | 0 |
| `RBS_FOUNDATION_MICRO` Introduction to Microeconomics | choose_n | RBS_FOUNDATIONS | min_count=1<br>**course list UNKNOWN** (source defers / unarchived) | 0 |
| `RBS_FOUNDATION_MACRO` Introduction to Macroeconomics | choose_n | RBS_FOUNDATIONS | min_count=1<br>**course list UNKNOWN** (source defers / unarchived) | 0 |
| `RBS_FOUNDATION_CALCULUS` Calculus | choose_n | RBS_FOUNDATIONS | min_count=1<br>**course list UNKNOWN** (source defers / unarchived) | 0 |
| `RBS_FOUNDATION_STATISTICS` Introductory Statistics | choose_n | RBS_FOUNDATIONS | min_count=1<br>**course list UNKNOWN** (source defers / unarchived) | 0 |

### Verbatim quotes per node

- **RBS_FOUNDATIONS**:
  - `rbs-catalog-programs`: "The following are the four foundational courses that students should complete during their first three (four at most) semesters:"
  - `rbs-catalog-programs`: "Foundational Courses (12-13 credits)"
  - `rbs-catalog-programs`: "In addition to the four eligibility courses and courses that are required for the major"
- **RBS_FOUNDATION_MICRO**:
  - `rbs-catalog-programs`: "Introduction to Microeconomics (3)"
  - note: Named by title only in the catalog; course number not stated on an archived source.
- **RBS_FOUNDATION_MACRO**:
  - `rbs-catalog-programs`: "Introduction to Macroeconomics (3)"
  - note: Named by title only in the catalog; course number not stated on an archived source.
- **RBS_FOUNDATION_CALCULUS**:
  - `rbs-catalog-programs`: "Calculus (3-4)"
  - note: Named by title only in the catalog ('Calculus (3-4)'); which calculus courses qualify is not stated on an archived source.
- **RBS_FOUNDATION_STATISTICS**:
  - `rbs-catalog-programs`: "Introductory Statistics (3)"
  - note: Named by title only in the catalog; course number not stated on an archived source.

## Rules not yet modeled

- Admission criteria for school-to-school transfer into RBS-NB (grades in the foundational courses, GPA, credits completed, application timing).  
  _why:_ The catalog defers them online; the department pages returned HTTP 403. Unknown - and admission is never inferred from course completion.  
  `rbs-catalog-admissions`: "Students who wish to transfer to Rutgers Business School: Undergraduate-New Brunswick from other Rutgers University schools should use the online application . More information about the requirements can be found online."
- Timing: the foundational courses within the first three (four at most) semesters.  
  _why:_ Guidance on timing ('should complete'), not a completion rule; plans keep the courses but do not impose a deadline.
- Students follow the major requirements in effect at their admission into the major.  
  _why:_ Cohort applicability (entered_school_from_term) is recorded on the business core; the admission term itself is a verified-evidence fact, never inferred.  
  `rbs-catalog-programs`: "Students must complete the requirements of the major that are in effect at the time of their admission into the major."

## Unknowns

- Course numbers for the four foundational courses. Archived pages mention 01:220:102, 01:220:103, 01:640:135 and 01:960:285 only as PREREQUISITES of business courses (phase08a-rbs-bait, phase08a-myrbs-bait): a review hint, not encoded.
- Grade and GPA thresholds for admission to RBS (myRBS, HTTP 403).

## How to record your decision

```
Components have no review command yet: record the decision in the PR / review notes; the component stays 'pending_review' (and every audit and index label says so) until a component review command exists.
```
