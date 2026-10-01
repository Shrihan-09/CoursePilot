# Review packet: `sas-640-ba-option-a` (2026-2027)

- **Lifecycle:** `validated`
- **Encoded by:** AI assistant (Claude), Phase 6.0 - encoder only, not a reviewer - encoding is not review
- **Definition:** `data/programs/rutgers/nb-undergrad/2026-2027/sas-640-ba-option-a.json`  sha256 `c6a802cc6cbf3ba0`
- **Source:** https://newbrunswick-26-27-undergrad.catalogs.rutgers.edu/schools/sas/program-listing/mathematics-640
- **Archive:** `data/raw/catalog/catalog_mathematics-640_2026_2027.html`  prose sha256 `90cfb20ae0a9b193`

## Machine checks (not review)

- passed: **True**, quotes found verbatim: 13/13
- 1 eligible row(s) are courses outside offering unit 01 (graduate 16:xxx, or another school's course) - check each was meant: named explicitly, or admitted by a query

## Review questions

- [ ] MATH_UPPER: do '411-412' and '451-452' denote two-semester SEQUENCES that count only as a pair? The encoding accepts either course of a pair on its own.
- [ ] MATH_251: what counts as '251 or equivalent'? Only 01:640:251 is encoded.
- [ ] MATH_COMPUTING: 14:332:252 (School of Engineering) is accepted as the page states; confirm.
- [ ] Is the 'C or better' rule for 250, 251 and 244/252 correctly left unmodeled (minimum grades are out of scope), rather than encoded approximately?

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
| `MATH_BA` | all_of |  |  | 0 |
| `MATH_FOUNDATION` | all_of | MATH_BA |  | 0 |
| `MATH_151` | course | MATH_FOUNDATION | courses: 01:640:151 | 1 |
| `MATH_152` | course | MATH_FOUNDATION | courses: 01:640:152 | 1 |
| `MATH_251` | course | MATH_FOUNDATION | courses: 01:640:251 | 1 |
| `MATH_250` | course | MATH_FOUNDATION | courses: 01:640:250 | 1 |
| `MATH_DIFFEQ` | choose_n | MATH_FOUNDATION | min_count=1<br>courses: 01:640:244, 01:640:252 | 2 |
| `MATH_COMPUTING` | choose_n | MATH_BA | min_count=1<br>courses: 01:198:107, 01:198:111, 14:332:252 | 3 |
| `MATH_UPPER` | choose_n | MATH_BA | min_count=8<br>min_distinct_categories=2<br>ANALYSIS: 01:640:311, 01:640:312, 01:640:411, 01:640:412; ALGEBRA: 01:640:350, 01:640:351, 01:640:451, 01:640:452<br>query {"exclude_course_numbers": ["491", "492"], "max_course_number": 499, "min_course_number": 300, "subject_code": "640"} | 52 |

### Quotes and notes

- **MATH_BA**: "The requirements for a math major are as follows:"
- **MATH_FOUNDATION**: "Three semesters of calculus (01:640:151-152, and 251 or equivalent), Introductory Linear Algebra (01:640:250), and Elementary Differential Equations (01:640:244 or 01:640:252)."
- **MATH_151**: "Three semesters of calculus (01:640:151-152, and 251 or equivalent)"
- **MATH_152**: "Three semesters of calculus (01:640:151-152, and 251 or equivalent)"
- **MATH_251**: "Three semesters of calculus (01:640:151-152, and 251 or equivalent)"
  - note: 'or equivalent' is not modeled: the catalog does not say which courses are equivalent.
- **MATH_250**: "Introductory Linear Algebra (01:640:250)"
- **MATH_DIFFEQ**: "Elementary Differential Equations (01:640:244 or 01:640:252)."
- **MATH_COMPUTING**: "01:198:107 Computing for Mathematics and the Sciences with a grade of C or better. (01:198:111 Introduction to Computer Science or 14:332:252 Programming Methodology I may be substituted for 01:198:107.)"
  - note: The prose names 01:198:107 and permits 01:198:111 or 14:332:252 as substitutes, so all three are eligible. Wherever a course is absent from the loaded SOC terms (14:332:252 is absent from the development database) it is reported unresolved rather than created.
- **MATH_UPPER**: "to complete the standard mathematics major a student must pass eight 300- to 400-level mathematics courses, excluding 01:640:491,492. ... At least four of the upper-level courses used to complete the major must be taken at Rutgers University-New Brunswick, including one of 01:640:311,312, 411-412, and one of 01:640:350, 351, 451-452."
  - note: 'including one of 01:640:311,312, 411-412, and one of 01:640:350, 351, 451-452' is encoded as two categories that the eight courses must jointly cover (min_distinct_categories=2). AMBIGUITY for a human reviewer: '411-412' and '451-452' may denote two-semester SEQUENCES that count only as a pair; this encoding accepts either course of the pair on its own. The prose attaches the analysis/algebra clause to the courses taken at Rutgers-New Brunswick; the location part is not evaluable (see MATH_RESIDENCY).

## Program rules

- `MATH_RESIDENCY` (residency, evaluable=False): "At least four of the upper-level courses used to complete the major must be taken at Rutgers University-New Brunswick"

## Rules not yet modeled

- Courses 01:640:250, 251, and 244/252 must be passed with grades of C or better.  
  _why:_ Per-course minimum grades are not evaluated by the engine today. Encoding them as a whole-record max_grade_count rule would change their meaning.
- All but one of these courses (curriculum code 640) must be passed with a grade of C or better.  
  _why:_ Scoped to the eight upper-level courses, not the whole record; the existing max_grade_count rule counts across the whole record, so it would not mean the same thing.
- 01:198:107 ... with a grade of C or better; C or better in the courses in other departments used to fulfill the requirements.  
  _why:_ Per-course minimum grades are not evaluated by the engine today.
- An appropriate Rutgers graduate mathematics course may be substituted for the required analysis and/or algebra course, with departmental approval.  
  _why:_ Requires departmental approval, which no CoursePilot data records.
- Students must notify the mathematics department in writing if they are not following the standard mathematics major option.  
  _why:_ An administrative step, not an academic requirement a record can satisfy.

## How to record your decision

```
python -m coursepilot_ingestion.registry_cli review --program sas-640-ba-option-a --year 2026-2027 --reviewer "<your full name>" --decision approved|changes_requested --notes "..."  (then, if approved: registry_cli publish --program ... --year ...)
```
