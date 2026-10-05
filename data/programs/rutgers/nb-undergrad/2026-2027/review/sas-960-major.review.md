# Review packet: `sas-960-major` (2026-2027)

- **Lifecycle:** `validated`
- **Encoded by:** AI assistant (Claude), Phase 6.3 - encoder only, not a reviewer - encoding is not review
- **Definition:** `data/programs/rutgers/nb-undergrad/2026-2027/sas-960-major.json`  sha256 `07d3c4434542b970`
- **Source:** https://newbrunswick-26-27-undergrad.catalogs.rutgers.edu/schools/sas/program-listing/statistics-960
- **Archive:** `data/raw/catalog/catalog_statistics-960_2026_2027.html`  prose sha256 `5f46db7e2a86d771`

## Machine checks (not review)

- passed: **True**, quotes found verbatim: 11/11

## Review questions

- [ ] Confirm the degree designation - the page does not state it.
- [ ] Is 01:640:477/481 acceptable in place of 381/382 for every student (encoded as yes)?

## What to verify

- [ ] Open the archived page (path below) and find every quoted sentence.
- [ ] For each requirement node, confirm the encoding says what the quote says: course lists, counts, levels, exclusions.
- [ ] Confirm nothing the page requires for COMPLETION is missing - compare against 'rules not yet modeled'.
- [ ] Confirm admission/declaration rules are kept as admission_prose and not as completion requirements.
- [ ] Answer every review question; a 'changes_requested' review is a valid outcome.
- [ ] Spot-check the eligible-course counts below against the course list you expect.

## Admission (kept separate from completion)

> To declare the major an overall C average or above, based on all times they were taken, must be achieved in Calculus I and II combined (01:640:151-152).

## Requirement nodes

| node | type | parent | encoding | eligible rows |
|---|---|---|---|---|
| `STAT_MAJOR` | all_of |  | minimum grade C (this node and its subtree) | 0 |
| `STAT_CS` | choose_n | STAT_MAJOR | min_count=1<br>courses: 01:198:107, 01:198:110, 01:198:111, 01:198:170 | 4 |
| `STAT_MATH` | all_of | STAT_MAJOR |  | 0 |
| `STAT_M151` | course | STAT_MATH | courses: 01:640:151 | 1 |
| `STAT_M152` | course | STAT_MATH | courses: 01:640:152 | 1 |
| `STAT_M250` | course | STAT_MATH | courses: 01:640:250 | 1 |
| `STAT_M251` | course | STAT_MATH | courses: 01:640:251 | 1 |
| `STAT_CORE` | all_of | STAT_MAJOR |  | 0 |
| `STAT_381` | choose_n | STAT_CORE | min_count=1<br>courses: 01:960:381, 01:640:477 | 2 |
| `STAT_382` | choose_n | STAT_CORE | min_count=1<br>courses: 01:960:382, 01:640:481 | 2 |
| `STAT_384` | course | STAT_CORE | courses: 01:960:384 | 1 |
| `STAT_295_390` | choose_n | STAT_CORE | min_count=1<br>courses: 01:960:295, 01:960:390 | 2 |
| `STAT_463` | course | STAT_CORE | courses: 01:960:463 | 1 |
| `STAT_486` | course | STAT_CORE | courses: 01:960:486 | 1 |
| `STAT_490` | course | STAT_CORE | courses: 01:960:490 | 1 |
| `STAT_ELECTIVES` | choose_n | STAT_MAJOR | min_count=2<br>courses: 01:960:365, 01:960:467, 01:960:476, 01:960:483 | 4 |
| `STAT_MATH_ELECTIVE` | choose_n | STAT_MAJOR | min_count=1<br>courses: 01:640:252<br>query {"exclude_course_numbers": ["477", "481"], "min_course_number": 300, "offering_unit_code": "01", "subject_code": "640"} | 45 |

### Quotes and notes

- **STAT_MAJOR**: "A total of 46 credits is required: 18 credits in mathematics, 25 credits in statistics, and 3 credits in computer science, as follows: ... Grades of C or better must be earned in all courses counted toward the major."
- **STAT_CS**: "Computer Science 01:198:107, 110, 111, or 170"
- **STAT_MATH**: "Mathematics 01:640:151, 152, 250, 251"
- **STAT_CORE**: "Statistics 01:960:381, 382, 384, 295 or 390, 463, 486, 490"
- **STAT_381**: "01:640:477 and 01:640:481 may be taken instead of 01:960:381, 382."
- **STAT_382**: "01:640:477 and 01:640:481 may be taken instead of 01:960:381, 382."
- **STAT_ELECTIVES**: "Two courses chosen from 01:960:365, 467, 476, 483"
- **STAT_MATH_ELECTIVE**: "Three credits in mathematics electives (01:640:252 or a course at the 300 level or above, but not 01:640:477 or 01:640:481)"
  - note: 'Three credits' is encoded as one course; every eligible course is 3+ credits in practice, but that is not checked.

## Rules not yet modeled

- Credit is not given for both 01:640:477 and 01:960:381, nor for both 01:640:481 and 01:960:382.  
  _why:_ A credit-exclusion pair; the choose_n nodes accept either, and the engine uses one course once.

## How to record your decision

```
python -m coursepilot_ingestion.registry_cli review --program sas-960-major --year 2026-2027 --reviewer "<your full name>" --decision approved|changes_requested --notes "..."  (then, if approved: registry_cli publish --program ... --year ...)
```
