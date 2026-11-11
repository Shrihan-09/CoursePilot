# Review packet: `sebs-core` (component, gen_ed)

- **Lifecycle:** `pending_review`
- **Encoded by:** manual encoding, Prompt 08 B1 (Claude Code session, 2026-11-11) - encoding is not review
- **Definition:** `data/programs/rutgers/nb-undergrad/2026-2027/components/sebs-core.json`  semantic sha256 `9fc8d96182df46aa`

## Sources

| id | role | status | retrieved (UTC) | representation | sha256 | url |
|---|---|---|---|---|---|---|
| `sebs-catalog-core` | catalog | archived | 2026-11-11T19:09:33+00:00 | raw_html | `87f0563f9759eb0d` | https://newbrunswick-26-27-undergrad.catalogs.rutgers.edu/schools/sebs/degree-requirements/mission-goals-core-curriculum-undergrad |
| `sebs-catalog-credits-residency` | catalog | archived | 2026-11-11T19:09:36+00:00 | raw_html | `1c921ab769caf18e` | https://newbrunswick-26-27-undergrad.catalogs.rutgers.edu/schools/sebs/degree-requirements/credits-residency |
| `sebs-core` | department | failed (open evidence gap) | 2026-11-11T19:09:38+00:00 | failed | `` | https://sebs.rutgers.edu/core |
- `sebs-core`: sebs.rutgers.edu/core returned HTTP 403 to a plain GET on 2026-11-11; the department source is an open evidence gap.

## Machine checks (not review)

- passed: **True**, quotes found verbatim: 25/25

## Review questions

- [ ] Confirm the bracket-to-SOC crosswalk: CC-D->CCD, CC-O->CCO, EL->XPL, SCL-SCA/ECN->SCL or ECN, SCL-GVT->GVT, AH->AHo/AHp/AHq/AHr, WC->WC, WCd->WCd, QQ->QQ, QR->QR.
- [ ] Social Analysis: the catalog bracket is [SCL-SCA/ECN]. Confirm that a course certified SCL (without ECN) meets the Social Cultural Analysis/Economic Analysis area.
- [ ] Confirm the 120-credit total applies to SEBS students in the 2026-2027 catalog (the Core page's 128-credit Area IX is for students matriculating prior to fall 2015).

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
| `SEBS_CORE` SEBS Core Curriculum | all_of |  | min_course_credits=3<br>graded_only=True | 0 |
| `SEBS_CC` Contemporary Challenges | all_of | SEBS_CORE |  | 0 |
| `SEBS_CCD` Diversities and Social Inequalities | choose_n | SEBS_CC | min_count=1<br>allocation_scope=sebs-core:contemporary<br>SOC certification per term: CCD | 150 |
| `SEBS_CCO` Our Common Future | choose_n | SEBS_CC | min_count=1<br>allocation_scope=sebs-core:contemporary<br>SOC certification per term: CCO | 123 |
| `SEBS_EL` Experiential Learning | choose_n | SEBS_CORE | min_count=1<br>allocation_scope=sebs-core:experiential<br>SOC certification per term: XPL | 1 |
| `SEBS_AOI` Areas of Inquiry | all_of | SEBS_CORE |  | 0 |
| `SEBS_NS` Natural Sciences | choose_n | SEBS_AOI | min_count=2<br>allocation_scope=sebs-core:natural-sciences<br>SOC certification per term: NS | 87 |
| `SEBS_HST` Historical Analysis | choose_n | SEBS_AOI | min_count=1<br>allocation_scope=sebs-core:historical-social<br>SOC certification per term: HST | 205 |
| `SEBS_SCL` Social Analysis | all_of | SEBS_AOI |  | 0 |
| `SEBS_SCL_SCA_ECN` Social Cultural Analysis / Economic Analysis | choose_n | SEBS_SCL | min_count=1<br>allocation_scope=sebs-core:historical-social<br>SOC certification per term: SCL, ECN | 103 |
| `SEBS_SCL_GVT` Governance and Regulation Analysis | choose_n | SEBS_SCL | min_count=1<br>allocation_scope=sebs-core:historical-social<br>SOC certification per term: GVT | 9 |
| `SEBS_AH` Arts and Humanities | choose_n | SEBS_AOI | min_count=1<br>allocation_scope=sebs-core:arts-humanities<br>SOC certification per term: AHo, AHp, AHq, AHr | 445 |
| `SEBS_CSP` Cognitive Skills and Processes | all_of | SEBS_CORE |  | 0 |
| `SEBS_WC` Writing and Communication | all_of | SEBS_CSP |  | 0 |
| `SEBS_WC_COLLEGE` College Writing | choose_n | SEBS_WC | min_count=1<br>allocation_scope=sebs-core:writing<br>SOC certification per term: WC | 3 |
| `SEBS_WCD` Writing in a Discipline | choose_n | SEBS_WC | min_count=1<br>allocation_scope=sebs-core:writing<br>SOC certification per term: WCd | 181 |
| `SEBS_QFR` Quantitative and Formal Reasoning | all_of | SEBS_CSP |  | 0 |
| `SEBS_QQ` Quantitative Information | choose_n | SEBS_QFR | min_count=1<br>allocation_scope=sebs-core:quantitative<br>SOC certification per term: QQ | 46 |
| `SEBS_QR` Mathematical or Formal Reasoning | choose_n | SEBS_QFR | min_count=1<br>allocation_scope=sebs-core:quantitative<br>SOC certification per term: QR | 41 |

### Verbatim quotes per node

- **SEBS_CORE**:
  - `sebs-catalog-core`: "First-year and transfer students matriculating fall 2019 and later must complete a goal-based Core Curriculum ."
  - `sebs-catalog-core`: "Only graded degree credit-bearing courses worth at least 3 credits and certified by the faculty may be used to meet core goals."
  - `sebs-catalog-core`: "Courses may be counted as meeting multiple learning goals; students generally will complete the core in 10 to 13 courses of 3 or 4 credits each. A course used to meet core goals may also be used to fulfill a major or minor requirement."
- **SEBS_CC**:
  - `sebs-catalog-core`: "(6 credits) Students must take two courses and meet one goal in each of the Contemporary Challenges areas. [CC-D, CC-O]"
- **SEBS_CCD**:
  - `sebs-catalog-core`: "Diversities and Social Inequalities [CC-D]"
- **SEBS_CCO**:
  - `sebs-catalog-core`: "Our Common Future [CC-O]"
- **SEBS_EL**:
  - `sebs-catalog-core`: "(3 credits) Practical application of disciplinary knowledge. Students must participate in a certified experience that meets this goal. [EL]"
  - note: SOC certifies XPL ('Experiential Learning') on very few offerings; an approved experience may not be a course at all. A missing XPL course is never read as 'no experiential option'.
- **SEBS_AOI**:
  - `sebs-catalog-core`: "Areas of Inquiry"
- **SEBS_NS**:
  - `sebs-catalog-core`: "Natural Sciences (6 credits) Students must meet two goals with courses in two of the following disciplines: physical, biological or environmental sciences. [NS]"
  - note: Two courses certified NS. The condition that they come from two different disciplines (physical, biological or environmental sciences) is NOT modeled - see rules_not_yet_modeled.
- **SEBS_HST**:
  - `sebs-catalog-core`: "Historical Analysis (3 credits) Students must meet one goal. [HST]"
- **SEBS_SCL**:
  - `sebs-catalog-core`: "Social Analysis (6 credits) Students must meet one goal in each area below. [SCL]"
- **SEBS_SCL_SCA_ECN**:
  - `sebs-catalog-core`: "Social Cultural Analysis/ Economic Analysis (3 credits) [SCL-SCA/ECN]"
- **SEBS_SCL_GVT**:
  - `sebs-catalog-core`: "Governance and Regulation Analysis (3 credits) [SCL-GVT]"
- **SEBS_AH**:
  - `sebs-catalog-core`: "Arts and Humanities (3 credits) Students must meet one goal. [AH]"
  - note: The catalog prints [AH] and lists four goals whose wording matches SOC's AHo/AHp/AHq/AHr descriptions; the crosswalk is a review question.
- **SEBS_CSP**:
  - `sebs-catalog-core`: "Cognitive Skills and Processes"
- **SEBS_WC**:
  - `sebs-catalog-core`: "Writing and Communication (6 credits) Students must meet one goal in two areas. [WC, WCd]"
- **SEBS_WC_COLLEGE**:
  - `sebs-catalog-core`: "College Writing (101) or Extended College Writing (104) or Exposition and Argument (103) (3 credits) [WC]"
  - note: The catalog names College Writing (101), Extended College Writing (104) and Exposition and Argument (103) without a subject code; eligibility uses SOC's WC certification rather than assuming the subject.
- **SEBS_WCD**:
  - `sebs-catalog-core`: "Writing in a Discipline (3 credits) [WCd]"
- **SEBS_QFR**:
  - `sebs-catalog-core`: "Quantitative and Formal Reasoning (6 credits) Students must meet two goals. [QQ; QR]"
- **SEBS_QQ**:
  - `sebs-catalog-core`: "Formulate, evaluate, and communicate conclusions and inferences from quantitative information. (Includes various quantitative methods courses as well as 640 courses) [QQ]"
- **SEBS_QR**:
  - `sebs-catalog-core`: "Apply effective and efficient mathematical or other formal processes to reason and to solve problems. (Includes 640 courses and formal reasoning course--or placement out of) [QR]"

## Rules

- `SEBS_MIN_GPA` (min_gpa, evaluable=True): Cumulative GPA of at least 2.000
  - `sebs-catalog-credits-residency`: "must complete a minimum of 120 credits* of coursework with a cumulative grade-point average of 2.000 or better"
- `SEBS_LAST_42` (residency_window, evaluable=False): 30 of the last 42 credits at Rutgers
  - not evaluated: Needs certified attempt institution and a chronological window over the last 42 credits; CoursePilot does not yet evaluate rolling residency windows.
  - `sebs-catalog-credits-residency`: "In addition, the student must complete 30 of the last 42 credits at Rutgers."
- `SEBS_PASS_NO_CREDIT` (pass_fail_limit, evaluable=False): Pass/No Credit limited to unspecified electives
  - not evaluated: The quoted P/NC limit appears under the catalog's area requirements for students matriculating prior to fall 2015; the current-cohort policy page (sebs.rutgers.edu) returned HTTP 403. Not evaluated.
  - `sebs-catalog-core`: "Students who have completed at least 60 credits of coursework may register for up to two of these unspecified elective courses (no more than one per semester) on a Pass/No Credit basis."

## Rules not yet modeled

- Natural Sciences: the two courses must come from two different disciplines (physical, biological or environmental sciences).  
  _why:_ No source-backed course-to-discipline mapping is archived; a subject-code guess would invent one.
- Experiential Learning may be met by a certified experience that is not a course.  
  _why:_ No verified experiential-approval evidence kind exists yet; only XPL-certified offerings are eligible, and their absence is never read as 'no option'.
- Environmental Engineering students complete at least 129 credits.  
  _why:_ A program-level total; it belongs to the Environmental Engineering encoding (Phase C), not the school component.

## Unknowns

- The current SEBS Core page and Pass/No Credit policy on sebs.rutgers.edu (HTTP 403 to a plain GET).
- Whether every SOC-certified CCD/CCO/NS/HST/SCL/AH/WC/WCd/QQ/QR course is certified for SEBS students (the catalog says lists are in Degree Navigator).

## How to record your decision

```
Components have no review command yet: record the decision in the PR / review notes; the component stays 'pending_review' (and every audit and index label says so) until a component review command exists.
```
