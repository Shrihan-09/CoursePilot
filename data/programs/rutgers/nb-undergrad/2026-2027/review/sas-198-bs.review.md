# Review packet: `sas-198-bs` (program)

- **Lifecycle:** `validated`
- **Encoded by:** manual encoding, Phase 7.3 (Claude Code session, 2026-10-23) - encoding is not review
- **Definition:** `data/programs/rutgers/nb-undergrad/2026-2027/sas-198-bs.json`  semantic sha256 `305a817f1a4497c4`

## Sources

| id | role | status | retrieved (UTC) | representation | sha256 | url |
|---|---|---|---|---|---|---|
| `catalog-cs-198-2026-2027` | catalog | archived | 2026-09-13 | raw_html | `dc89098d8598619c` | https://newbrunswick-26-27-undergrad.catalogs.rutgers.edu/schools/sas/program-listing/computer-science-198 |
| `phase08a-cs-bs-degree` | department | archived | 2026-11-09T19:51:02.092573+00:00 | raw_html | `dc360f490d57026c` | https://www.cs.rutgers.edu/academics/undergraduate/cs-degrees/b-s-degree |
| `phase08a-cs-electives` | department | archived | 2026-11-09T19:51:07.098586+00:00 | raw_html | `7b5b963251fe592c` | https://www.cs.rutgers.edu/academics/undergraduate/electives |
| `owner-2026-11-12-cs-intro` | owner_decision | not in manifest |  |  | `` |  |

## Machine checks (not review)

- passed: **True**, quotes found verbatim: 27/27
- warning: CS_ELECTIVES: 01:198:324 not in the loaded course table (reported, never created)
- warning: CS_ELECTIVES: 01:198:411 not in the loaded course table (reported, never created)
- warning: CS_ELECTIVES: 01:198:424 not in the loaded course table (reported, never created)
- warning: CS_ELECTIVES: 14:332:453 not in the loaded course table (reported, never created)

## OPEN conflicts (both claims shown; none resolved)

### CS-INTRO-2027 (resolved_by_owner)
Catalog 2026-2027 names 01:198:111/112; the department names 121/122 from Spring 2027. Resolved 2026-11-12 by the owner: one course-based curriculum (121 or completed 111; 122 or completed 112), with the 111/112 counting flagged for review. Both original claims are kept.
- `catalog-cs-198-2026-2027`: "six required courses in computer science, 01:198:111, 112, 205, 206, 211, and 344"
- `phase08a-cs-bs-degree`: "Beginning Spring 2027, the Department of Computer Science has adopted a new introductory course sequence consisting of:"
- `phase08a-cs-bs-degree`: "Students complete the major requirements through CS121 and CS122. CS120 does not count toward Computer Science major requirements"

### CS-BS-COPY (open)
The department's B.S. page states the residency and ten-year elective clauses in terms of 'the B.A. degree'. Whether they bind B.S. students is not stated.
- `phase08a-cs-bs-degree`: "Residency requirement: At least seven of the required and elective courses used to satisfy the B.A. degree requirements must be taken in the New Brunswick Department of Computer Science (01:198:xxx)."
- `phase08a-cs-bs-degree`: "All CS electives required for the B.A. degree must have been completed within the last ten years preceding graduation."


## Review questions

- [ ] CS-BS-COPY: do the residency (seven 01:198 courses) and ten-year elective clauses on the B.S. page bind B.S. students? The page labels them 'B.A.'.
- [ ] The physics list names '01:750:193 and 194' twice ('... or 01:750:193 and 194 ... or 01:750:193, 194'). Encoded once. Confirm no other course was meant.
- [ ] Chemistry 01:160:171 appears in all three chemistry options; each option is encoded as its own all-of set.
- [ ] Same graduate-course question as the B.A. (sas-198-ba.json review_questions).
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
| `CS_BS` Computer Science B.S. Major | all_of |  |  | 0 |
| `CS_CORE` Required Computer Science Courses | all_of | CS_BS |  | 0 |
| `CS_111` Introduction to Computer Science | course | CS_CORE | courses: 01:198:121<br>EQUIV_111: 01:198:111 | 2 |
| `CS_112` Data Structures | course | CS_CORE | courses: 01:198:122<br>EQUIV_112: 01:198:112 | 2 |
| `CS_205` Introduction to Discrete Structures I | course | CS_CORE | courses: 01:198:205 | 1 |
| `CS_206` Introduction to Discrete Structures II | course | CS_CORE | courses: 01:198:206 | 1 |
| `CS_211` Computer Architecture | course | CS_CORE | courses: 01:198:211 | 1 |
| `CS_344` Design and Analysis of Computer Algorithms | course | CS_CORE | courses: 01:198:344 | 1 |
| `CS_MATH` Mathematics Requirement | all_of | CS_BS |  | 0 |
| `MATH_151` Calculus I | course | CS_MATH | courses: 01:640:151 | 1 |
| `MATH_152` Calculus II | course | CS_MATH | courses: 01:640:152 | 1 |
| `MATH_250` Introductory Linear Algebra | course | CS_MATH | courses: 01:640:250 | 1 |
| `CS_ELECTIVES` Computer Science Electives | choose_n | CS_BS | min_count=7<br>min_at_level=300<br>min_at_level_count=2<br>max_outside_subject=2<br>constraint_subject_code=198<br>courses: 01:198:210, 01:198:213, 01:198:214, 01:198:314, 01:198:323, 01:198:324, 01:198:334, 01:198:336, 01:198:352, 01:198:411, 01:198:416, 01:198:417, 01:198:419, 01:198:424, 01:198:425, 01:198:428, 01:198:431, 01:198:437, 01:198:439, 01:198:440, 01:198:442, 01:198:443, 01:198:444, 01:198:445, 01:198:452, 01:198:460, 01:198:461, 01:198:462, 01:198:493, 01:198:494, 14:332:376, 14:332:423, 14:332:424, 14:332:443, 14:332:451, 14:332:452, 14:332:453, 14:332:456, 14:332:472, 01:640:338, 01:640:348, 01:640:354, 01:640:428, 01:640:454, 01:640:461, 01:730:315, 01:730:407, 01:730:408, 01:730:329, 01:730:424, 01:615:441, 01:960:384, 01:960:463, 01:960:476, 01:960:486<br>at most 1 of 01:198:493, 01:198:494 | 51 |
| `CS_BS_SCIENCE` Science Requirement (B.S.) | any_of | CS_BS |  | 0 |
| `CS_BS_PHYS_A` Physics 203, 204, 205, 206 | all_of | CS_BS_SCIENCE |  | 0 |
| `CS_BS_PHYS_A_203` Physics 01:750:203 | course | CS_BS_PHYS_A | courses: 01:750:203 | 1 |
| `CS_BS_PHYS_A_204` Physics 01:750:204 | course | CS_BS_PHYS_A | courses: 01:750:204 | 1 |
| `CS_BS_PHYS_A_205` Physics 01:750:205 | course | CS_BS_PHYS_A | courses: 01:750:205 | 1 |
| `CS_BS_PHYS_A_206` Physics 01:750:206 | course | CS_BS_PHYS_A | courses: 01:750:206 | 1 |
| `CS_BS_PHYS_B` Physics 123, 124, 227, 229 | all_of | CS_BS_SCIENCE |  | 0 |
| `CS_BS_PHYS_B_123` Physics 01:750:123 | course | CS_BS_PHYS_B | courses: 01:750:123 | 1 |
| `CS_BS_PHYS_B_124` Physics 01:750:124 | course | CS_BS_PHYS_B | courses: 01:750:124 | 1 |
| `CS_BS_PHYS_B_227` Physics 01:750:227 | course | CS_BS_PHYS_B | courses: 01:750:227 | 1 |
| `CS_BS_PHYS_B_229` Physics 01:750:229 | course | CS_BS_PHYS_B | courses: 01:750:229 | 1 |
| `CS_BS_PHYS_C` Physics 193, 194 | all_of | CS_BS_SCIENCE |  | 0 |
| `CS_BS_PHYS_C_193` Physics 01:750:193 | course | CS_BS_PHYS_C | courses: 01:750:193 | 1 |
| `CS_BS_PHYS_C_194` Physics 01:750:194 | course | CS_BS_PHYS_C | courses: 01:750:194 | 1 |
| `CS_BS_PHYS_D` Physics 271, 272, 275, 276 | all_of | CS_BS_SCIENCE |  | 0 |
| `CS_BS_PHYS_D_271` Physics 01:750:271 | course | CS_BS_PHYS_D | courses: 01:750:271 | 1 |
| `CS_BS_PHYS_D_272` Physics 01:750:272 | course | CS_BS_PHYS_D | courses: 01:750:272 | 1 |
| `CS_BS_PHYS_D_275` Physics 01:750:275 | course | CS_BS_PHYS_D | courses: 01:750:275 | 1 |
| `CS_BS_PHYS_D_276` Physics 01:750:276 | course | CS_BS_PHYS_D | courses: 01:750:276 | 1 |
| `CS_BS_PHYS_E` Physics 201, 202 | all_of | CS_BS_SCIENCE |  | 0 |
| `CS_BS_PHYS_E_201` Physics 01:750:201 | course | CS_BS_PHYS_E | courses: 01:750:201 | 1 |
| `CS_BS_PHYS_E_202` Physics 01:750:202 | course | CS_BS_PHYS_E | courses: 01:750:202 | 1 |
| `CS_BS_CHEM_A` Chemistry 159, 160, 171 | all_of | CS_BS_SCIENCE |  | 0 |
| `CS_BS_CHEM_A_159` Chemistry 01:160:159 | course | CS_BS_CHEM_A | courses: 01:160:159 | 1 |
| `CS_BS_CHEM_A_160` Chemistry 01:160:160 | course | CS_BS_CHEM_A | courses: 01:160:160 | 1 |
| `CS_BS_CHEM_A_171` Chemistry 01:160:171 | course | CS_BS_CHEM_A | courses: 01:160:171 | 1 |
| `CS_BS_CHEM_B` Chemistry 161, 162, 171 | all_of | CS_BS_SCIENCE |  | 0 |
| `CS_BS_CHEM_B_161` Chemistry 01:160:161 | course | CS_BS_CHEM_B | courses: 01:160:161 | 1 |
| `CS_BS_CHEM_B_162` Chemistry 01:160:162 | course | CS_BS_CHEM_B | courses: 01:160:162 | 1 |
| `CS_BS_CHEM_B_171` Chemistry 01:160:171 | course | CS_BS_CHEM_B | courses: 01:160:171 | 1 |
| `CS_BS_CHEM_C` Chemistry 163, 164, 171 | all_of | CS_BS_SCIENCE |  | 0 |
| `CS_BS_CHEM_C_163` Chemistry 01:160:163 | course | CS_BS_CHEM_C | courses: 01:160:163 | 1 |
| `CS_BS_CHEM_C_164` Chemistry 01:160:164 | course | CS_BS_CHEM_C | courses: 01:160:164 | 1 |
| `CS_BS_CHEM_C_171` Chemistry 01:160:171 | course | CS_BS_CHEM_C | courses: 01:160:171 | 1 |

### Verbatim quotes per node

- **CS_BS**:
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
  - `catalog-cs-198-2026-2027`: "complete seven courses from category 3, no more than two of these outside the Department of Computer Science"
  - `phase08a-cs-bs-degree`: "Seven electives from a designated list of courses in computer science and related disciplines . At least five of the electives must be taken in the New Brunswick Department of Computer Science (i.e., they must be 01:198:xxx), of which at least two must be at the 300 level or above."
  - `phase08a-cs-electives`: "A CS Advisor will help you make selections from this list to complete your CS major."
  - `phase08a-cs-electives`: "However, at most one of a student's electives may be one of these courses. Whether or not a given instance of 493/494 counts for elective credit depends on the individual arrangement."
  - note: The designated list is the Department of Computer Science electives page (archived capture phase08a-cs-electives, raw HTML, 2026-11-09), to which the catalog defers: 30 CS courses plus Electrical Engineering, Mathematics, Philosophy, Linguistics and Statistics options - identical to the list encoded 2026-10-23. Constraints from the department B.S. page: at least five of the seven in 01:198 (encoded as at most two outside subject 198; every listed course outside 198 is also outside unit 01:198), at least two of those at the 300 level or above, and at most one 01:198:493/494 (group cap).
- **CS_BS_SCIENCE**:
  - `catalog-cs-198-2026-2027`: "plus four physics courses: 01:750:203, 204, 205, and 206 (or 01:750:123, 124, 227, and 229 or 01:750:193 and 194 or 01:750:271, 272, 275, and 276 or 01:750:201 and 202 or 01:750:193, 194); or chemistry 01:160:159, 160, and 171 (or 01:160:161, 162, and 171 or 01:160:163, 164, and 171)"
  - note: One complete set: a physics sequence or a chemistry sequence, as the prose lists them.
- **CS_BS_PHYS_A** (via `CS_BS_SCIENCE`):
  - `catalog-cs-198-2026-2027`: "plus four physics courses: 01:750:203, 204, 205, and 206 (or 01:750:123, 124, 227, and 229 or 01:750:193 and 194 or 01:750:271, 272, 275, and 276 or 01:750:201 and 202 or 01:750:193, 194); or chemistry 01:160:159, 160, and 171 (or 01:160:161, 162, and 171 or 01:160:163, 164, and 171)"
- **CS_BS_PHYS_A_203** (via `CS_BS_SCIENCE`):
  - `catalog-cs-198-2026-2027`: "plus four physics courses: 01:750:203, 204, 205, and 206 (or 01:750:123, 124, 227, and 229 or 01:750:193 and 194 or 01:750:271, 272, 275, and 276 or 01:750:201 and 202 or 01:750:193, 194); or chemistry 01:160:159, 160, and 171 (or 01:160:161, 162, and 171 or 01:160:163, 164, and 171)"
- **CS_BS_PHYS_A_204** (via `CS_BS_SCIENCE`):
  - `catalog-cs-198-2026-2027`: "plus four physics courses: 01:750:203, 204, 205, and 206 (or 01:750:123, 124, 227, and 229 or 01:750:193 and 194 or 01:750:271, 272, 275, and 276 or 01:750:201 and 202 or 01:750:193, 194); or chemistry 01:160:159, 160, and 171 (or 01:160:161, 162, and 171 or 01:160:163, 164, and 171)"
- **CS_BS_PHYS_A_205** (via `CS_BS_SCIENCE`):
  - `catalog-cs-198-2026-2027`: "plus four physics courses: 01:750:203, 204, 205, and 206 (or 01:750:123, 124, 227, and 229 or 01:750:193 and 194 or 01:750:271, 272, 275, and 276 or 01:750:201 and 202 or 01:750:193, 194); or chemistry 01:160:159, 160, and 171 (or 01:160:161, 162, and 171 or 01:160:163, 164, and 171)"
- **CS_BS_PHYS_A_206** (via `CS_BS_SCIENCE`):
  - `catalog-cs-198-2026-2027`: "plus four physics courses: 01:750:203, 204, 205, and 206 (or 01:750:123, 124, 227, and 229 or 01:750:193 and 194 or 01:750:271, 272, 275, and 276 or 01:750:201 and 202 or 01:750:193, 194); or chemistry 01:160:159, 160, and 171 (or 01:160:161, 162, and 171 or 01:160:163, 164, and 171)"
- **CS_BS_PHYS_B** (via `CS_BS_SCIENCE`):
  - `catalog-cs-198-2026-2027`: "plus four physics courses: 01:750:203, 204, 205, and 206 (or 01:750:123, 124, 227, and 229 or 01:750:193 and 194 or 01:750:271, 272, 275, and 276 or 01:750:201 and 202 or 01:750:193, 194); or chemistry 01:160:159, 160, and 171 (or 01:160:161, 162, and 171 or 01:160:163, 164, and 171)"
- **CS_BS_PHYS_B_123** (via `CS_BS_SCIENCE`):
  - `catalog-cs-198-2026-2027`: "plus four physics courses: 01:750:203, 204, 205, and 206 (or 01:750:123, 124, 227, and 229 or 01:750:193 and 194 or 01:750:271, 272, 275, and 276 or 01:750:201 and 202 or 01:750:193, 194); or chemistry 01:160:159, 160, and 171 (or 01:160:161, 162, and 171 or 01:160:163, 164, and 171)"
- **CS_BS_PHYS_B_124** (via `CS_BS_SCIENCE`):
  - `catalog-cs-198-2026-2027`: "plus four physics courses: 01:750:203, 204, 205, and 206 (or 01:750:123, 124, 227, and 229 or 01:750:193 and 194 or 01:750:271, 272, 275, and 276 or 01:750:201 and 202 or 01:750:193, 194); or chemistry 01:160:159, 160, and 171 (or 01:160:161, 162, and 171 or 01:160:163, 164, and 171)"
- **CS_BS_PHYS_B_227** (via `CS_BS_SCIENCE`):
  - `catalog-cs-198-2026-2027`: "plus four physics courses: 01:750:203, 204, 205, and 206 (or 01:750:123, 124, 227, and 229 or 01:750:193 and 194 or 01:750:271, 272, 275, and 276 or 01:750:201 and 202 or 01:750:193, 194); or chemistry 01:160:159, 160, and 171 (or 01:160:161, 162, and 171 or 01:160:163, 164, and 171)"
- **CS_BS_PHYS_B_229** (via `CS_BS_SCIENCE`):
  - `catalog-cs-198-2026-2027`: "plus four physics courses: 01:750:203, 204, 205, and 206 (or 01:750:123, 124, 227, and 229 or 01:750:193 and 194 or 01:750:271, 272, 275, and 276 or 01:750:201 and 202 or 01:750:193, 194); or chemistry 01:160:159, 160, and 171 (or 01:160:161, 162, and 171 or 01:160:163, 164, and 171)"
- **CS_BS_PHYS_C** (via `CS_BS_SCIENCE`):
  - `catalog-cs-198-2026-2027`: "plus four physics courses: 01:750:203, 204, 205, and 206 (or 01:750:123, 124, 227, and 229 or 01:750:193 and 194 or 01:750:271, 272, 275, and 276 or 01:750:201 and 202 or 01:750:193, 194); or chemistry 01:160:159, 160, and 171 (or 01:160:161, 162, and 171 or 01:160:163, 164, and 171)"
- **CS_BS_PHYS_C_193** (via `CS_BS_SCIENCE`):
  - `catalog-cs-198-2026-2027`: "plus four physics courses: 01:750:203, 204, 205, and 206 (or 01:750:123, 124, 227, and 229 or 01:750:193 and 194 or 01:750:271, 272, 275, and 276 or 01:750:201 and 202 or 01:750:193, 194); or chemistry 01:160:159, 160, and 171 (or 01:160:161, 162, and 171 or 01:160:163, 164, and 171)"
- **CS_BS_PHYS_C_194** (via `CS_BS_SCIENCE`):
  - `catalog-cs-198-2026-2027`: "plus four physics courses: 01:750:203, 204, 205, and 206 (or 01:750:123, 124, 227, and 229 or 01:750:193 and 194 or 01:750:271, 272, 275, and 276 or 01:750:201 and 202 or 01:750:193, 194); or chemistry 01:160:159, 160, and 171 (or 01:160:161, 162, and 171 or 01:160:163, 164, and 171)"
- **CS_BS_PHYS_D** (via `CS_BS_SCIENCE`):
  - `catalog-cs-198-2026-2027`: "plus four physics courses: 01:750:203, 204, 205, and 206 (or 01:750:123, 124, 227, and 229 or 01:750:193 and 194 or 01:750:271, 272, 275, and 276 or 01:750:201 and 202 or 01:750:193, 194); or chemistry 01:160:159, 160, and 171 (or 01:160:161, 162, and 171 or 01:160:163, 164, and 171)"
- **CS_BS_PHYS_D_271** (via `CS_BS_SCIENCE`):
  - `catalog-cs-198-2026-2027`: "plus four physics courses: 01:750:203, 204, 205, and 206 (or 01:750:123, 124, 227, and 229 or 01:750:193 and 194 or 01:750:271, 272, 275, and 276 or 01:750:201 and 202 or 01:750:193, 194); or chemistry 01:160:159, 160, and 171 (or 01:160:161, 162, and 171 or 01:160:163, 164, and 171)"
- **CS_BS_PHYS_D_272** (via `CS_BS_SCIENCE`):
  - `catalog-cs-198-2026-2027`: "plus four physics courses: 01:750:203, 204, 205, and 206 (or 01:750:123, 124, 227, and 229 or 01:750:193 and 194 or 01:750:271, 272, 275, and 276 or 01:750:201 and 202 or 01:750:193, 194); or chemistry 01:160:159, 160, and 171 (or 01:160:161, 162, and 171 or 01:160:163, 164, and 171)"
- **CS_BS_PHYS_D_275** (via `CS_BS_SCIENCE`):
  - `catalog-cs-198-2026-2027`: "plus four physics courses: 01:750:203, 204, 205, and 206 (or 01:750:123, 124, 227, and 229 or 01:750:193 and 194 or 01:750:271, 272, 275, and 276 or 01:750:201 and 202 or 01:750:193, 194); or chemistry 01:160:159, 160, and 171 (or 01:160:161, 162, and 171 or 01:160:163, 164, and 171)"
- **CS_BS_PHYS_D_276** (via `CS_BS_SCIENCE`):
  - `catalog-cs-198-2026-2027`: "plus four physics courses: 01:750:203, 204, 205, and 206 (or 01:750:123, 124, 227, and 229 or 01:750:193 and 194 or 01:750:271, 272, 275, and 276 or 01:750:201 and 202 or 01:750:193, 194); or chemistry 01:160:159, 160, and 171 (or 01:160:161, 162, and 171 or 01:160:163, 164, and 171)"
- **CS_BS_PHYS_E** (via `CS_BS_SCIENCE`):
  - `catalog-cs-198-2026-2027`: "plus four physics courses: 01:750:203, 204, 205, and 206 (or 01:750:123, 124, 227, and 229 or 01:750:193 and 194 or 01:750:271, 272, 275, and 276 or 01:750:201 and 202 or 01:750:193, 194); or chemistry 01:160:159, 160, and 171 (or 01:160:161, 162, and 171 or 01:160:163, 164, and 171)"
- **CS_BS_PHYS_E_201** (via `CS_BS_SCIENCE`):
  - `catalog-cs-198-2026-2027`: "plus four physics courses: 01:750:203, 204, 205, and 206 (or 01:750:123, 124, 227, and 229 or 01:750:193 and 194 or 01:750:271, 272, 275, and 276 or 01:750:201 and 202 or 01:750:193, 194); or chemistry 01:160:159, 160, and 171 (or 01:160:161, 162, and 171 or 01:160:163, 164, and 171)"
- **CS_BS_PHYS_E_202** (via `CS_BS_SCIENCE`):
  - `catalog-cs-198-2026-2027`: "plus four physics courses: 01:750:203, 204, 205, and 206 (or 01:750:123, 124, 227, and 229 or 01:750:193 and 194 or 01:750:271, 272, 275, and 276 or 01:750:201 and 202 or 01:750:193, 194); or chemistry 01:160:159, 160, and 171 (or 01:160:161, 162, and 171 or 01:160:163, 164, and 171)"
- **CS_BS_CHEM_A** (via `CS_BS_SCIENCE`):
  - `catalog-cs-198-2026-2027`: "plus four physics courses: 01:750:203, 204, 205, and 206 (or 01:750:123, 124, 227, and 229 or 01:750:193 and 194 or 01:750:271, 272, 275, and 276 or 01:750:201 and 202 or 01:750:193, 194); or chemistry 01:160:159, 160, and 171 (or 01:160:161, 162, and 171 or 01:160:163, 164, and 171)"
- **CS_BS_CHEM_A_159** (via `CS_BS_SCIENCE`):
  - `catalog-cs-198-2026-2027`: "plus four physics courses: 01:750:203, 204, 205, and 206 (or 01:750:123, 124, 227, and 229 or 01:750:193 and 194 or 01:750:271, 272, 275, and 276 or 01:750:201 and 202 or 01:750:193, 194); or chemistry 01:160:159, 160, and 171 (or 01:160:161, 162, and 171 or 01:160:163, 164, and 171)"
- **CS_BS_CHEM_A_160** (via `CS_BS_SCIENCE`):
  - `catalog-cs-198-2026-2027`: "plus four physics courses: 01:750:203, 204, 205, and 206 (or 01:750:123, 124, 227, and 229 or 01:750:193 and 194 or 01:750:271, 272, 275, and 276 or 01:750:201 and 202 or 01:750:193, 194); or chemistry 01:160:159, 160, and 171 (or 01:160:161, 162, and 171 or 01:160:163, 164, and 171)"
- **CS_BS_CHEM_A_171** (via `CS_BS_SCIENCE`):
  - `catalog-cs-198-2026-2027`: "plus four physics courses: 01:750:203, 204, 205, and 206 (or 01:750:123, 124, 227, and 229 or 01:750:193 and 194 or 01:750:271, 272, 275, and 276 or 01:750:201 and 202 or 01:750:193, 194); or chemistry 01:160:159, 160, and 171 (or 01:160:161, 162, and 171 or 01:160:163, 164, and 171)"
- **CS_BS_CHEM_B** (via `CS_BS_SCIENCE`):
  - `catalog-cs-198-2026-2027`: "plus four physics courses: 01:750:203, 204, 205, and 206 (or 01:750:123, 124, 227, and 229 or 01:750:193 and 194 or 01:750:271, 272, 275, and 276 or 01:750:201 and 202 or 01:750:193, 194); or chemistry 01:160:159, 160, and 171 (or 01:160:161, 162, and 171 or 01:160:163, 164, and 171)"
- **CS_BS_CHEM_B_161** (via `CS_BS_SCIENCE`):
  - `catalog-cs-198-2026-2027`: "plus four physics courses: 01:750:203, 204, 205, and 206 (or 01:750:123, 124, 227, and 229 or 01:750:193 and 194 or 01:750:271, 272, 275, and 276 or 01:750:201 and 202 or 01:750:193, 194); or chemistry 01:160:159, 160, and 171 (or 01:160:161, 162, and 171 or 01:160:163, 164, and 171)"
- **CS_BS_CHEM_B_162** (via `CS_BS_SCIENCE`):
  - `catalog-cs-198-2026-2027`: "plus four physics courses: 01:750:203, 204, 205, and 206 (or 01:750:123, 124, 227, and 229 or 01:750:193 and 194 or 01:750:271, 272, 275, and 276 or 01:750:201 and 202 or 01:750:193, 194); or chemistry 01:160:159, 160, and 171 (or 01:160:161, 162, and 171 or 01:160:163, 164, and 171)"
- **CS_BS_CHEM_B_171** (via `CS_BS_SCIENCE`):
  - `catalog-cs-198-2026-2027`: "plus four physics courses: 01:750:203, 204, 205, and 206 (or 01:750:123, 124, 227, and 229 or 01:750:193 and 194 or 01:750:271, 272, 275, and 276 or 01:750:201 and 202 or 01:750:193, 194); or chemistry 01:160:159, 160, and 171 (or 01:160:161, 162, and 171 or 01:160:163, 164, and 171)"
- **CS_BS_CHEM_C** (via `CS_BS_SCIENCE`):
  - `catalog-cs-198-2026-2027`: "plus four physics courses: 01:750:203, 204, 205, and 206 (or 01:750:123, 124, 227, and 229 or 01:750:193 and 194 or 01:750:271, 272, 275, and 276 or 01:750:201 and 202 or 01:750:193, 194); or chemistry 01:160:159, 160, and 171 (or 01:160:161, 162, and 171 or 01:160:163, 164, and 171)"
- **CS_BS_CHEM_C_163** (via `CS_BS_SCIENCE`):
  - `catalog-cs-198-2026-2027`: "plus four physics courses: 01:750:203, 204, 205, and 206 (or 01:750:123, 124, 227, and 229 or 01:750:193 and 194 or 01:750:271, 272, 275, and 276 or 01:750:201 and 202 or 01:750:193, 194); or chemistry 01:160:159, 160, and 171 (or 01:160:161, 162, and 171 or 01:160:163, 164, and 171)"
- **CS_BS_CHEM_C_164** (via `CS_BS_SCIENCE`):
  - `catalog-cs-198-2026-2027`: "plus four physics courses: 01:750:203, 204, 205, and 206 (or 01:750:123, 124, 227, and 229 or 01:750:193 and 194 or 01:750:271, 272, 275, and 276 or 01:750:201 and 202 or 01:750:193, 194); or chemistry 01:160:159, 160, and 171 (or 01:160:161, 162, and 171 or 01:160:163, 164, and 171)"
- **CS_BS_CHEM_C_171** (via `CS_BS_SCIENCE`):
  - `catalog-cs-198-2026-2027`: "plus four physics courses: 01:750:203, 204, 205, and 206 (or 01:750:123, 124, 227, and 229 or 01:750:193 and 194 or 01:750:271, 272, 275, and 276 or 01:750:201 and 202 or 01:750:193, 194); or chemistry 01:160:159, 160, and 171 (or 01:160:161, 162, and 171 or 01:160:163, 164, and 171)"

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

- At least five of the seven electives in 01:198, at least two of those at the 300 level or above; at most one 01:198:493/494.  
  _why:_ IMPLEMENTED on CS_ELECTIVES (max_outside_subject / min_at_level / group_caps), sourced from the department B.S. and electives pages.
- Whether an instance of 01:198:493/494 counts depends on the individual arrangement.  
  _why:_ An adviser decision per instance; CoursePilot counts at most one and says it needs confirmation.  
  `phase08a-cs-electives`: "Whether or not a given instance of 493/494 counts for elective credit depends on the individual arrangement."
- CS electives completed within the last ten years preceding graduation.  
  _why:_ Labelled 'B.A.' on the B.S. page (conflict CS-BS-COPY), and a recency window is not modeled.  
  `phase08a-cs-bs-degree`: "All CS electives required for the B.A. degree must have been completed within the last ten years preceding graduation."
- Placement: the CS placement exam or AP Computer Science A (4 = CS120 equivalency, 5 = CS121 equivalency) lets a student skip CS120.  
  _why:_ No CS placement or AP evidence is recorded on the student record yet; plans include CS120 before CS121 and say that placing out removes it.  
  `owner-2026-11-12-cs-intro`: "Prereq for 121 unless placed out (placement exam, AP CSA 4 = 120 equivalency, AP CSA 5 = 121 equivalency)."

## Unknowns

- (none)

## How to record your decision

```
python -m coursepilot_ingestion.registry_cli review --program sas-198-bs --year 2026-2027 --reviewer "<your full name>" --decision approved|changes_requested --notes "..."
```
