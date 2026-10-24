# Review packet: `sas-198-bs` (2026-2027)

- **Lifecycle:** `validated`
- **Encoded by:** manual encoding, Phase 7.3 (Claude Code session, 2026-10-23) - encoding is not review
- **Definition:** `data/programs/rutgers/nb-undergrad/2026-2027/sas-198-bs.json`  sha256 `e55095cdd4f410c0`
- **Source:** https://newbrunswick-26-27-undergrad.catalogs.rutgers.edu/schools/sas/program-listing/computer-science-198
- **Archive:** `data/raw/catalog/catalog_computer-science-198_2026_2027.html`  prose sha256 `4421d9b70ef5a4f8`

## Machine checks (not review)

- passed: **True**, quotes found verbatim: 9/9

## Review questions

- [ ] Category 3 for the B.S.: the B.A. text says 'at least two must be computer science courses at the 300 level or above'. The B.S. sentence repeats only the 'no more than two outside' limit. This encoding KEEPS the 300-level minimum (2) for the seven electives - confirm it applies to the B.S.
- [ ] The physics list names '01:750:193 and 194' twice ('... or 01:750:193 and 194 ... or 01:750:193, 194'). Encoded once. Confirm no other course was meant.
- [ ] Chemistry 01:160:171 appears in all three chemistry options; each option is encoded as its own all-of set.
- [ ] Same elective-list gap and graduate-course question as the B.A. (sas-198-ba.json review_questions).

## What to verify

- [ ] Open the archived page (path below) and find every quoted sentence.
- [ ] For each requirement node, confirm the encoding says what the quote says: course lists, counts, levels, exclusions.
- [ ] Confirm nothing the page requires for COMPLETION is missing - compare against 'rules not yet modeled'.
- [ ] Confirm admission/declaration rules are kept as admission_prose and not as completion requirements.
- [ ] Answer every review question; a 'changes_requested' review is a valid outcome.
- [ ] Spot-check the eligible-course counts below against the course list you expect.

## Requirement nodes

| node | type | parent | encoding | eligible rows |
|---|---|---|---|---|
| `CS_BS` | all_of |  |  | 0 |
| `CS_CORE` | all_of | CS_BS |  | 0 |
| `CS_111` | course | CS_CORE | courses: 01:198:111 | 1 |
| `CS_112` | course | CS_CORE | courses: 01:198:112 | 1 |
| `CS_205` | course | CS_CORE | courses: 01:198:205 | 1 |
| `CS_206` | course | CS_CORE | courses: 01:198:206 | 1 |
| `CS_211` | course | CS_CORE | courses: 01:198:211 | 1 |
| `CS_344` | course | CS_CORE | courses: 01:198:344 | 1 |
| `CS_MATH` | all_of | CS_BS |  | 0 |
| `MATH_151` | course | CS_MATH | courses: 01:640:151 | 1 |
| `MATH_152` | course | CS_MATH | courses: 01:640:152 | 1 |
| `MATH_250` | course | CS_MATH | courses: 01:640:250 | 1 |
| `CS_ELECTIVES` | choose_n | CS_BS | min_count=7<br>min_at_level=300<br>min_at_level_count=2<br>max_outside_subject=2<br>constraint_subject_code=198<br>query {"min_course_number": 300, "subject_code": "198"} | 32 |
| `CS_BS_SCIENCE` | any_of | CS_BS |  | 0 |
| `CS_BS_PHYS_A` | all_of | CS_BS_SCIENCE |  | 0 |
| `CS_BS_PHYS_A_203` | course | CS_BS_PHYS_A | courses: 01:750:203 | 1 |
| `CS_BS_PHYS_A_204` | course | CS_BS_PHYS_A | courses: 01:750:204 | 1 |
| `CS_BS_PHYS_A_205` | course | CS_BS_PHYS_A | courses: 01:750:205 | 1 |
| `CS_BS_PHYS_A_206` | course | CS_BS_PHYS_A | courses: 01:750:206 | 1 |
| `CS_BS_PHYS_B` | all_of | CS_BS_SCIENCE |  | 0 |
| `CS_BS_PHYS_B_123` | course | CS_BS_PHYS_B | courses: 01:750:123 | 1 |
| `CS_BS_PHYS_B_124` | course | CS_BS_PHYS_B | courses: 01:750:124 | 1 |
| `CS_BS_PHYS_B_227` | course | CS_BS_PHYS_B | courses: 01:750:227 | 1 |
| `CS_BS_PHYS_B_229` | course | CS_BS_PHYS_B | courses: 01:750:229 | 1 |
| `CS_BS_PHYS_C` | all_of | CS_BS_SCIENCE |  | 0 |
| `CS_BS_PHYS_C_193` | course | CS_BS_PHYS_C | courses: 01:750:193 | 1 |
| `CS_BS_PHYS_C_194` | course | CS_BS_PHYS_C | courses: 01:750:194 | 1 |
| `CS_BS_PHYS_D` | all_of | CS_BS_SCIENCE |  | 0 |
| `CS_BS_PHYS_D_271` | course | CS_BS_PHYS_D | courses: 01:750:271 | 1 |
| `CS_BS_PHYS_D_272` | course | CS_BS_PHYS_D | courses: 01:750:272 | 1 |
| `CS_BS_PHYS_D_275` | course | CS_BS_PHYS_D | courses: 01:750:275 | 1 |
| `CS_BS_PHYS_D_276` | course | CS_BS_PHYS_D | courses: 01:750:276 | 1 |
| `CS_BS_PHYS_E` | all_of | CS_BS_SCIENCE |  | 0 |
| `CS_BS_PHYS_E_201` | course | CS_BS_PHYS_E | courses: 01:750:201 | 1 |
| `CS_BS_PHYS_E_202` | course | CS_BS_PHYS_E | courses: 01:750:202 | 1 |
| `CS_BS_CHEM_A` | all_of | CS_BS_SCIENCE |  | 0 |
| `CS_BS_CHEM_A_159` | course | CS_BS_CHEM_A | courses: 01:160:159 | 1 |
| `CS_BS_CHEM_A_160` | course | CS_BS_CHEM_A | courses: 01:160:160 | 1 |
| `CS_BS_CHEM_A_171` | course | CS_BS_CHEM_A | courses: 01:160:171 | 1 |
| `CS_BS_CHEM_B` | all_of | CS_BS_SCIENCE |  | 0 |
| `CS_BS_CHEM_B_161` | course | CS_BS_CHEM_B | courses: 01:160:161 | 1 |
| `CS_BS_CHEM_B_162` | course | CS_BS_CHEM_B | courses: 01:160:162 | 1 |
| `CS_BS_CHEM_B_171` | course | CS_BS_CHEM_B | courses: 01:160:171 | 1 |
| `CS_BS_CHEM_C` | all_of | CS_BS_SCIENCE |  | 0 |
| `CS_BS_CHEM_C_163` | course | CS_BS_CHEM_C | courses: 01:160:163 | 1 |
| `CS_BS_CHEM_C_164` | course | CS_BS_CHEM_C | courses: 01:160:164 | 1 |
| `CS_BS_CHEM_C_171` | course | CS_BS_CHEM_C | courses: 01:160:171 | 1 |

### Quotes and notes

- **CS_BS**: "The basic major, leading to a bachelor of arts (B.A.) degree, consists of: 1) six required courses in computer science, 01:198:111, 112, 205, 206, 211, and 344; 2) three courses in mathematics, 01:640:151-152 and 01:640:250; and 3) five electives from a designated list of courses in computer science and related disciplines."
- **CS_CORE**: "six required courses in computer science, 01:198:111, 112, 205, 206, 211, and 344"
- **CS_MATH**: "three courses in mathematics, 01:640:151-152 and 01:640:250"
- **CS_ELECTIVES**: "complete seven courses from category 3, no more than two of these outside the Department of Computer Science"
  - note: B.S.: seven electives from category 3 (the B.A.'s designated list), at most two outside CS. The 300-level minimum is kept from the category's definition - a REVIEW QUESTION. Non-CS options are not encoded (list not in the catalog).
- **CS_BS_SCIENCE**: "plus four physics courses: 01:750:203, 204, 205, and 206 (or 01:750:123, 124, 227, and 229 or 01:750:193 and 194 or 01:750:271, 272, 275, and 276 or 01:750:201 and 202 or 01:750:193, 194); or chemistry 01:160:159, 160, and 171 (or 01:160:161, 162, and 171 or 01:160:163, 164, and 171)"
  - note: One complete set: a physics sequence or a chemistry sequence, as the prose lists them.

## Program rules

- `CS_MAX_D` (max_grade_count, evaluable=True): "No more than one grade of D can be accepted in the courses required for the major."
- `CS_EXCLUDED` (course_exclusion, evaluable=True): "Declared computer science majors (198) will not receive credit (major or degree) for subsequent enrollment in computer science 105, 107,110, 142, 170, or 405."
- `CS_RESIDENCY` (residency, evaluable=False): "A minimum of seven courses must be taken in the Rutgers University-New Brunswick Department of Computer Science."

## Rules not yet modeled

- At most two of the five electives may be taken outside the Department of Computer Science; at least two must be CS at the 300 level or above.  
  _why:_ IMPLEMENTED as group constraints on CS_ELECTIVES (max_outside_subject / min_at_level), not as a program rule.

## How to record your decision

```
python -m coursepilot_ingestion.registry_cli review --program sas-198-bs --year 2026-2027 --reviewer "<your full name>" --decision approved|changes_requested --notes "..."  (then, if approved: registry_cli publish --program ... --year ...)
```
