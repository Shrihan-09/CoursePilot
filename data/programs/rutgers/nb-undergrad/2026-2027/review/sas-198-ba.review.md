# Review packet: `sas-198-ba` (2026-2027)

- **Lifecycle:** `published` (basis `legacy_curated`)
- **Encoded by:** manual encoding, Phase 3 (encoder not recorded) - encoding is not review
- **Definition:** `data/programs/rutgers/nb-undergrad/2026-2027/sas-198-ba.json`  sha256 `fef7b1e12e54b987`
- **Source:** https://newbrunswick-26-27-undergrad.catalogs.rutgers.edu/schools/sas/program-listing/computer-science-198
- **Archive:** `data/raw/catalog/catalog_computer-science-198_2026_2027.html`  prose sha256 `4421d9b70ef5a4f8`

## Machine checks (not review)

- passed: **False**, quotes found verbatim: 7/8
- ERROR: CS_ELECTIVES: quote not found verbatim on the page: 'five electives from a designated list of courses in computer science and related disciplin'
- 60 eligible row(s) are courses outside offering unit 01 (graduate 16:xxx, or another school's course) - check each was meant: named explicitly, or admitted by a query

## Review questions

- [ ] CS_ELECTIVES source_prose omits 'For details, see a computer science adviser or the departmental website.' without marking the elision ('...'). Confirm the omission is harmless and approve adding the marker (a definition change: it moves CS to needs_rereview until re-approved).
- [ ] CS_ELECTIVES' query (subject 198, number >= 300) also admits graduate courses (16:198:5xx/6xx): 60 rows on the development database. Should the query add offering_unit_code '01'? (Also a definition change.)
- [ ] CS is published with basis 'legacy_curated' (no review record predates Phase 6.3). Converting it to a human_review publication requires passing through needs_rereview, during which CS reports pending_review. Decide when to do that.

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
| `CS_BA` | all_of |  |  | 0 |
| `CS_CORE` | all_of | CS_BA |  | 0 |
| `CS_111` | course | CS_CORE | courses: 01:198:111 | 1 |
| `CS_112` | course | CS_CORE | courses: 01:198:112 | 1 |
| `CS_205` | course | CS_CORE | courses: 01:198:205 | 1 |
| `CS_206` | course | CS_CORE | courses: 01:198:206 | 1 |
| `CS_211` | course | CS_CORE | courses: 01:198:211 | 1 |
| `CS_344` | course | CS_CORE | courses: 01:198:344 | 1 |
| `CS_MATH` | all_of | CS_BA |  | 0 |
| `MATH_151` | course | CS_MATH | courses: 01:640:151 | 1 |
| `MATH_152` | course | CS_MATH | courses: 01:640:152 | 1 |
| `MATH_250` | course | CS_MATH | courses: 01:640:250 | 1 |
| `CS_ELECTIVES` | choose_n | CS_BA | min_count=5<br>min_at_level=300<br>min_at_level_count=2<br>max_outside_subject=2<br>constraint_subject_code=198<br>query {"min_course_number": 300, "subject_code": "198"} | 86 |

### Quotes and notes

- **CS_BA**: "The basic major, leading to a bachelor of arts (B.A.) degree, consists of: 1) six required courses in computer science, 01:198:111, 112, 205, 206, 211, and 344; 2) three courses in mathematics, 01:640:151-152 and 01:640:250; and 3) five electives from a designated list of courses in computer science and related disciplines."
- **CS_CORE**: "six required courses in computer science, 01:198:111, 112, 205, 206, 211, and 344"
- **CS_MATH**: "three courses in mathematics, 01:640:151-152 and 01:640:250"
- **CS_ELECTIVES**: "five electives from a designated list of courses in computer science and related disciplines (e.g., electrical engineering, mathematics). At most, two of the five electives may be taken outside the Department of Computer Science; at least two must be computer science courses at the 300 level or above."
  - note: The catalog defers the designated elective list to the department; only CS courses at the 300 level or above are encoded here, which the prose supports directly. Non-CS elective options are NOT encoded because the catalog does not publish them.

## Program rules

- `CS_MAX_D` (max_grade_count, evaluable=True): "No more than one grade of D can be accepted in the courses required for the major."
- `CS_EXCLUDED` (course_exclusion, evaluable=True): "Declared computer science majors (198) will not receive credit (major or degree) for subsequent enrollment in computer science 105, 107,110, 142, 170, or 405."
- `CS_RESIDENCY` (residency, evaluable=False): "A minimum of seven courses must be taken in the Rutgers University-New Brunswick Department of Computer Science."

## Rules not yet modeled

- At most two of the five electives may be taken outside the Department of Computer Science; at least two must be CS at the 300 level or above.  
  _why:_ IMPLEMENTED as group constraints on CS_ELECTIVES (max_outside_subject / min_at_level), not as a program rule.

## How to record your decision

```
python -m coursepilot_ingestion.registry_cli review --program sas-198-ba --year 2026-2027 --reviewer "<your full name>" --decision approved|changes_requested --notes "..."  (then, if approved: registry_cli publish --program ... --year ...)
```
