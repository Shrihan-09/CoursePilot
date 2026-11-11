# Review packet: `rbs-business-core` (component, school_core)

- **Lifecycle:** `pending_review`
- **Encoded by:** manual encoding, Prompt 08 B1 (Claude Code session, 2026-11-11) - encoding is not review
- **Definition:** `data/programs/rutgers/nb-undergrad/2026-2027/components/rbs-business-core.json`  semantic sha256 `15e6158cbd4f0ccc`

## Sources

| id | role | status | retrieved (UTC) | representation | sha256 | url |
|---|---|---|---|---|---|---|
| `rbs-catalog-bait` | catalog | archived | 2026-11-11T19:09:24+00:00 | raw_html | `863737ed3974d15a` | https://newbrunswick-26-27-undergrad.catalogs.rutgers.edu/schools/rbsnb/degree-requirements/programs-majors-minors/bait |
| `rbs-catalog-programs` | catalog | archived | 2026-11-11T19:09:19+00:00 | raw_html | `87b56becf9a97ce2` | https://newbrunswick-26-27-undergrad.catalogs.rutgers.edu/schools/rbsnb/degree-requirements/programs-majors-minors/programs |
| `phase08a-myrbs-bait` | department | archived | 2026-11-09T19:54:38.603287+00:00 | extracted_text | `13240ec394575554` | https://myrbs.business.rutgers.edu/undergraduate-new-brunswick/business-analytics-and-information-technology-major |
- `phase08a-myrbs-bait`: Phase A extraction-only capture (web reader text), not raw HTML.

## Machine checks (not review)

- passed: **True**, quotes found verbatim: 27/27
- warning: RBS_BC_ETHICS: 33:522:335 not in the loaded course table (reported, never created)

## OPEN conflicts (both claims shown; none resolved)

### BAIT-ETHICS (open)
- `rbs-catalog-bait`: "33:522:335 OR 33:140:320 Business Ethics OR Business Law"
- `rbs-catalog-bait`: "You can earn credit towards graduation if you take 33:522:334 Business Ethics"
- `phase08a-myrbs-bait`: "33:522:334 Business Ethics OR 33:140:320 Business Law"


## Review questions

- [ ] Resolve BAIT-ETHICS: the BAIT page's core table prints 33:522:335 while its own notes and the department pages print 33:522:334.
- [ ] Confirm the business core table is identical on all six RBS major pages for fall 2026 entrants (encoded once from the BAIT page).

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
| `RBS_BUSINESS_CORE` Business Core | all_of |  | min_grade=C<br>available_after_stage=rbs-nb-admission | 0 |
| `RBS_BC_010_272` Intro to Financial Accounting | course | RBS_BUSINESS_CORE | courses: 33:010:272 | 1 |
| `RBS_BC_011_301` Foundations for Your Career Journey | course | RBS_BUSINESS_CORE | courses: 33:011:301 | 1 |
| `RBS_BC_011_302` Building Your Brand | course | RBS_BUSINESS_CORE | courses: 33:011:302 | 1 |
| `RBS_BC_011_303` Conquering Your Transition | course | RBS_BUSINESS_CORE | courses: 33:011:303 | 1 |
| `RBS_BC_390_300` Financial Management | course | RBS_BUSINESS_CORE | courses: 33:390:300 | 1 |
| `RBS_BC_620_302` Management Skills | course | RBS_BUSINESS_CORE | courses: 33:620:302 | 1 |
| `RBS_BC_136_385` Stat Methods for Business | course | RBS_BUSINESS_CORE | courses: 33:136:385 | 1 |
| `RBS_BC_620_369` International Business | course | RBS_BUSINESS_CORE | courses: 33:620:369 | 1 |
| `RBS_BC_630_301` Intro to Marketing | course | RBS_BUSINESS_CORE | courses: 33:630:301 | 1 |
| `RBS_BC_799_301` Intro to Supply Chain | course | RBS_BUSINESS_CORE | courses: 33:799:301 | 1 |
| `RBS_BC_136_386` Business Operations Analytics | course | RBS_BUSINESS_CORE | courses: 33:136:386 | 1 |
| `RBS_BC_620_492` Business Policy & Strategy | course | RBS_BUSINESS_CORE | courses: 33:620:492 | 1 |
| `RBS_BC_MIS_OR_AIS` Management Information Systems OR Accounting Information Systems | any_of | RBS_BUSINESS_CORE | min_count=1 | 0 |
| `RBS_BC_136_370` Management Information Systems | course | RBS_BC_MIS_OR_AIS | courses: 33:136:370 | 1 |
| `RBS_BC_010_458` Accounting Information Systems | course | RBS_BC_MIS_OR_AIS | courses: 33:010:458 | 1 |
| `RBS_BC_ETHICS_OR_LAW` Business Ethics OR Business Law | any_of | RBS_BUSINESS_CORE | min_count=1 | 0 |
| `RBS_BC_ETHICS` Business Ethics | course | RBS_BC_ETHICS_OR_LAW | courses: 33:522:335<br>**open conflict** BAIT-ETHICS | 0 |
| `RBS_BC_140_320` Business Law | course | RBS_BC_ETHICS_OR_LAW | courses: 33:140:320 | 1 |

### Verbatim quotes per node

- **RBS_BUSINESS_CORE**:
  - `rbs-catalog-bait`: "Major requirements for students who entered RBS in the fall 2026 semester:"
  - `rbs-catalog-programs`: "business core course requirements that all students must complete regardless of their choice of major program"
  - `rbs-catalog-programs`: "All core courses and required courses specific to the major must be completed with a grade of C or better."
- **RBS_BC_010_272**:
  - `rbs-catalog-bait`: "33:010:272 Intro to Financial Accounting"
- **RBS_BC_011_301**:
  - `rbs-catalog-bait`: "33:011:301 Foundations for Your Career Journey"
- **RBS_BC_011_302**:
  - `rbs-catalog-bait`: "33:011:302 Building Your Brand"
- **RBS_BC_011_303**:
  - `rbs-catalog-bait`: "33:011:303 Conquering Your Transition"
- **RBS_BC_390_300**:
  - `rbs-catalog-bait`: "33:390:300 Financial Management"
- **RBS_BC_620_302**:
  - `rbs-catalog-bait`: "33:620:302 Management Skills"
- **RBS_BC_136_385**:
  - `rbs-catalog-bait`: "33:136:385 Stat Methods for Business"
- **RBS_BC_620_369**:
  - `rbs-catalog-bait`: "33:620:369 International Business"
- **RBS_BC_630_301**:
  - `rbs-catalog-bait`: "33:630:301 Intro to Marketing"
- **RBS_BC_799_301**:
  - `rbs-catalog-bait`: "33:799:301 Intro to Supply Chain"
- **RBS_BC_136_386**:
  - `rbs-catalog-bait`: "33:136:386 Business Operations Analytics"
- **RBS_BC_620_492**:
  - `rbs-catalog-bait`: "33:620:492 Business Policy & Strategy"
- **RBS_BC_MIS_OR_AIS**:
  - `rbs-catalog-bait`: "33:136:370 OR 33:010:458 Management Information Systems OR Accounting Information Systems"
- **RBS_BC_136_370**:
  - `rbs-catalog-bait`: "33:136:370 OR"
- **RBS_BC_010_458**:
  - `rbs-catalog-bait`: "33:010:458 Management Information Systems OR Accounting Information Systems"
- **RBS_BC_ETHICS_OR_LAW**:
  - `rbs-catalog-bait`: "33:522:335 OR 33:140:320 Business Ethics OR Business Law"
- **RBS_BC_ETHICS**:
  - `rbs-catalog-bait`: "33:522:335 OR"
  - note: The table's literal course number. The same page's notes, and the department pages, print 33:522:334: see conflict BAIT-ETHICS.
- **RBS_BC_140_320**:
  - `rbs-catalog-bait`: "33:140:320 Business Ethics OR Business Law"

## Rules

- `RBS_ONE_SHARED_MAJOR_COURSE` (double_count_limit, evaluable=False): One RBS course may count toward two RBS majors
  - not evaluated: CoursePilot plans one major per student today; a cross-major overlap cap is recorded, not evaluated.
  - `rbs-catalog-bait`: "Students with more than one Business School major may only double count one RBS course between those majors (not including the Business Core)."

## Rules not yet modeled

- Accounting majors (first, second or third major) take 33:010:458, 33:140:320 and 33:010:275; 33:522:334, 33:136:370 and 33:136:386 then earn graduation credit but no Accounting major credit.  
  _why:_ A major-dependent override of the shared core; it is encoded with the Accounting major (Phase C), never applied to other majors.  
  `rbs-catalog-bait`: "If you major in Accounting as your first, second, or third major, you are required to take 33:010:458 Accounting Information Systems, 33:140:320 Business Law I, and an additional course - 33:010:275 Managerial Accounting."
- A grade of B or better in 33:390:300 is needed to take further Finance courses.  
  _why:_ A prerequisite-grade gate for later courses, not a completion rule of the core; it belongs to the Finance encoding.  
  `rbs-catalog-bait`: "A grade of B or better in 33:390:300 Financial Management is required to take any additional Finance department courses."
- Credit for 33:011:300 Business Forum replaces 33:011:301, 302 and 303.  
  _why:_ Stated only in an extraction-only department capture (phase08a-myrbs-bait), not in the catalog; recorded for review, not encoded.
- Students who entered RBS before fall 2026 follow a different (39-credit) business core.  
  _why:_ A different cohort's curriculum (conflict register BAIT-CORE-COHORT); this component applies to fall-2026 entrants only.

## Unknowns

- Which Business Ethics course number governs the fall 2026 core (33:522:335 or 33:522:334): conflict BAIT-ETHICS.

## How to record your decision

```
Components have no review command yet: record the decision in the PR / review notes; the component stays 'pending_review' (and every audit and index label says so) until a component review command exists.
```
