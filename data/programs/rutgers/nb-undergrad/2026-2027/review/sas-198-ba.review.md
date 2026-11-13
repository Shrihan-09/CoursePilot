# Review packet: `sas-198-ba` (program)

- **Lifecycle:** `needs_rereview`
- **Encoded by:** manual encoding, Phase 3 (encoder not recorded) - encoding is not review
- **Definition:** `data/programs/rutgers/nb-undergrad/2026-2027/sas-198-ba.json`  semantic sha256 `4bd6b68e13229c54`

## Sources

| id | role | status | retrieved (UTC) | representation | sha256 | url |
|---|---|---|---|---|---|---|
| `catalog-cs-198-2026-2027` | catalog | archived | 2026-09-13 | raw_html | `dc89098d8598619c` | https://newbrunswick-26-27-undergrad.catalogs.rutgers.edu/schools/sas/program-listing/computer-science-198 |
| `phase08a-cs-bs-degree` | department | archived | 2026-11-09T19:51:02.092573+00:00 | raw_html | `dc360f490d57026c` | https://www.cs.rutgers.edu/academics/undergraduate/cs-degrees/b-s-degree |
| `owner-2026-11-12-cs-intro` | owner_decision | not in manifest |  |  | `` |  |

## Machine checks (not review)

- passed: **False**, quotes found verbatim: 16/17
- ERROR: CS_ELECTIVES: quote not found verbatim in catalog-cs-198-2026-2027: 'five electives from a designated list of courses in computer science and related disciplin'
- warning: CS_ELECTIVES: 01:198:324 not in the loaded course table (reported, never created)
- warning: CS_ELECTIVES: 01:198:411 not in the loaded course table (reported, never created)
- warning: CS_ELECTIVES: 01:198:424 not in the loaded course table (reported, never created)
- warning: CS_ELECTIVES: 14:332:453 not in the loaded course table (reported, never created)

## OPEN conflicts (both claims shown; none resolved)

### CS-INTRO-2027 (resolved_by_owner)
Catalog 2026-2027 names 01:198:111/112; the department names 121/122 from Spring 2027. Resolved 2026-11-12 by the owner: one course-based curriculum (121 or completed 111; 122 or completed 112), with the 111/112 counting flagged for review. Both original claims are kept.
- `phase08a-cs-bs-degree`: "Beginning Spring 2027, the Department of Computer Science has adopted a new introductory course sequence consisting of:"


## Review questions

- [ ] CS_ELECTIVES source_prose omits 'For details, see a computer science adviser or the departmental website.' without marking the elision ('...'). Confirm the omission is harmless and approve adding the marker (a definition change: it moves CS to needs_rereview until re-approved).
- [ ] CS is published with basis 'legacy_curated' (no review record predates Phase 6.3). Converting it to a human_review publication requires passing through needs_rereview, during which CS reports pending_review. Decide when to do that.
- [ ] CS_ELECTIVES now lists the department's designated electives (2026-10-23) instead of the {subject 198, number >= 300} query. Confirm the list and that it applies to catalog year 2026-2027 (the department page is undated).
- [ ] CS-111-AS-121 / CS-112-AS-122: confirm with the department that completed Rutgers 01:198:111/112 count toward the 121/122 slots (the stated equivalency is for transfer credit).

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
| `CS_BA` Computer Science B.A. Major | all_of |  |  | 0 |
| `CS_CORE` Required Computer Science Courses | all_of | CS_BA |  | 0 |
| `CS_111` Introduction to Computer Science | course | CS_CORE | courses: 01:198:121<br>EQUIV_111: 01:198:111 | 2 |
| `CS_112` Data Structures | course | CS_CORE | courses: 01:198:122<br>EQUIV_112: 01:198:112 | 2 |
| `CS_205` Introduction to Discrete Structures I | course | CS_CORE | courses: 01:198:205 | 1 |
| `CS_206` Introduction to Discrete Structures II | course | CS_CORE | courses: 01:198:206 | 1 |
| `CS_211` Computer Architecture | course | CS_CORE | courses: 01:198:211 | 1 |
| `CS_344` Design and Analysis of Computer Algorithms | course | CS_CORE | courses: 01:198:344 | 1 |
| `CS_MATH` Mathematics Requirement | all_of | CS_BA |  | 0 |
| `MATH_151` Calculus I | course | CS_MATH | courses: 01:640:151 | 1 |
| `MATH_152` Calculus II | course | CS_MATH | courses: 01:640:152 | 1 |
| `MATH_250` Introductory Linear Algebra | course | CS_MATH | courses: 01:640:250 | 1 |
| `CS_ELECTIVES` Computer Science Electives | choose_n | CS_BA | min_count=5<br>min_at_level=300<br>min_at_level_count=2<br>max_outside_subject=2<br>constraint_subject_code=198<br>courses: 01:198:210, 01:198:213, 01:198:214, 01:198:314, 01:198:323, 01:198:324, 01:198:334, 01:198:336, 01:198:352, 01:198:411, 01:198:416, 01:198:417, 01:198:419, 01:198:424, 01:198:425, 01:198:428, 01:198:431, 01:198:437, 01:198:439, 01:198:440, 01:198:442, 01:198:443, 01:198:444, 01:198:445, 01:198:452, 01:198:460, 01:198:461, 01:198:462, 01:198:493, 01:198:494, 14:332:376, 14:332:423, 14:332:424, 14:332:443, 14:332:451, 14:332:452, 14:332:453, 14:332:456, 14:332:472, 01:640:338, 01:640:348, 01:640:354, 01:640:428, 01:640:454, 01:640:461, 01:730:315, 01:730:407, 01:730:408, 01:730:329, 01:730:424, 01:615:441, 01:960:384, 01:960:463, 01:960:476, 01:960:486 | 51 |

### Verbatim quotes per node

- **CS_BA**:
  - `catalog-cs-198-2026-2027`: "The basic major, leading to a bachelor of arts (B.A.) degree, consists of: 1) six required courses in computer science, 01:198:111, 112, 205, 206, 211, and 344; 2) three courses in mathematics, 01:640:151-152 and 01:640:250; and 3) five electives from a designated list of courses in computer science and related disciplines."
- **CS_CORE**:
  - `catalog-cs-198-2026-2027`: "six required courses in computer science, 01:198:111, 112, 205, 206, 211, and 344"
- **CS_111**:
  - `owner-2026-11-12-cs-intro`: "The intro requirement is met by 121 OR completed 111"
  - note: Met by 01:198:121 or a completed 01:198:111 (owner decision 2026-11-12). 01:198:111 counts under an equivalency the department states for transfer credit: needs review.
- **CS_112**:
  - `owner-2026-11-12-cs-intro`: "and by 122 OR completed 112"
  - note: Met by 01:198:122 or a completed 01:198:112 (owner decision 2026-11-12). 01:198:112 counts under an equivalency the department states for transfer credit: needs review.
- **CS_205** (via `CS_CORE`):
  - `catalog-cs-198-2026-2027`: "six required courses in computer science, 01:198:111, 112, 205, 206, 211, and 344"
- **CS_206** (via `CS_CORE`):
  - `catalog-cs-198-2026-2027`: "six required courses in computer science, 01:198:111, 112, 205, 206, 211, and 344"
- **CS_211** (via `CS_CORE`):
  - `catalog-cs-198-2026-2027`: "six required courses in computer science, 01:198:111, 112, 205, 206, 211, and 344"
- **CS_344** (via `CS_CORE`):
  - `catalog-cs-198-2026-2027`: "six required courses in computer science, 01:198:111, 112, 205, 206, 211, and 344"
- **CS_MATH**:
  - `catalog-cs-198-2026-2027`: "three courses in mathematics, 01:640:151-152 and 01:640:250"
- **MATH_151** (via `CS_MATH`):
  - `catalog-cs-198-2026-2027`: "three courses in mathematics, 01:640:151-152 and 01:640:250"
- **MATH_152** (via `CS_MATH`):
  - `catalog-cs-198-2026-2027`: "three courses in mathematics, 01:640:151-152 and 01:640:250"
- **MATH_250** (via `CS_MATH`):
  - `catalog-cs-198-2026-2027`: "three courses in mathematics, 01:640:151-152 and 01:640:250"
- **CS_ELECTIVES**:
  - `catalog-cs-198-2026-2027`: "five electives from a designated list of courses in computer science and related disciplines (e.g., electrical engineering, mathematics). At most, two of the five electives may be taken outside the Department of Computer Science; at least two must be computer science courses at the 300 level or above."
  - note: Designated list from the Department of Computer Science electives page (https://www.cs.rutgers.edu/academics/undergraduate/electives, read 2026-10-23, undated), to which the catalog defers: 30 CS courses plus Electrical Engineering, Mathematics, Philosophy, Linguistics and Statistics options. Replaces the earlier query {subject 198, number >= 300}, which admitted courses the department does not list (e.g. 01:198:345). The catalog's own constraints are kept: at most two outside CS, at least two CS courses at the 300 level or above.

## Rules

- `CS_MAX_D` (max_grade_count, evaluable=True): At most one grade of D in required major courses
  - `catalog`: "No more than one grade of D can be accepted in the courses required for the major."
- `CS_EXCLUDED` (course_exclusion, evaluable=True): Courses that earn no credit for declared CS majors
  - `catalog`: "Declared computer science majors (198) will not receive credit (major or degree) for subsequent enrollment in computer science 105, 107,110, 142, 170, or 405."
- `CS_RESIDENCY` (residency, evaluable=False): Minimum seven courses in the Rutgers-New Brunswick CS department
  - not evaluated: The rule requires knowing which courses were taken AT Rutgers-New Brunswick. StudentCourse records no transfer provenance, and a cross-listed course carries another department's code, so the count cannot be computed without guessing. Rutgers publishes no transfer-equivalence rule we could apply here.
  - `catalog`: "A minimum of seven courses must be taken in the Rutgers University-New Brunswick Department of Computer Science."
- `CS_120_NO_MAJOR_CREDIT` (no_major_credit, evaluable=True): CS120 earns SAS credit, never CS major credit
  - `phase08a-cs-bs-degree`: "Students earn SAS credit for CS120, but not Computer Science major credit."
  - `owner-2026-11-12-cs-intro`: "CS120: SAS credit only, never CS major credit (free elective only)."

## Rules not yet modeled

- At most two of the five electives may be taken outside the Department of Computer Science; at least two must be CS at the 300 level or above.  
  _why:_ IMPLEMENTED as group constraints on CS_ELECTIVES (max_outside_subject / min_at_level), not as a program rule.
- At most one of a student's electives may be 01:198:493/494 (Independent Study); whether an instance counts depends on the individual arrangement.  
  _why:_ No per-course-group cap in the requirement model yet; source: the department electives page (2026-10-23).
- Placement: the CS placement exam or AP Computer Science A (4 = CS120 equivalency, 5 = CS121 equivalency) lets a student skip CS120.  
  _why:_ No CS placement or AP evidence is recorded on the student record yet; plans include CS120 before CS121 and say that placing out removes it.  
  `owner-2026-11-12-cs-intro`: "Prereq for 121 unless placed out (placement exam, AP CSA 4 = 120 equivalency, AP CSA 5 = 121 equivalency)."

## Unknowns

- (none)

## How to record your decision

```
python -m coursepilot_ingestion.registry_cli review --program sas-198-ba --year 2026-2027 --reviewer "<your full name>" --decision approved|changes_requested --notes "..."
```
