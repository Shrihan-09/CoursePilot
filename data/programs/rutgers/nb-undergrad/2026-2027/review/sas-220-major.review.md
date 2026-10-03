# Review packet: `sas-220-major` (2026-2027)

- **Lifecycle:** `validated`
- **Encoded by:** AI assistant (Claude), Phase 6.3 - encoder only, not a reviewer - encoding is not review
- **Definition:** `data/programs/rutgers/nb-undergrad/2026-2027/sas-220-major.json`  sha256 `0a01e4315d455f44`
- **Source:** https://newbrunswick-26-27-undergrad.catalogs.rutgers.edu/schools/sas/program-listing/economics-220
- **Archive:** `data/raw/catalog/catalog_economics-220_2026_2027.html`  prose sha256 `99477789d0efff70`

## Machine checks (not review)

- passed: **True**, quotes found verbatim: 13/13
- 2 eligible row(s) are courses outside offering unit 01 (graduate 16:xxx, or another school's course) - check each was meant: named explicitly, or admitted by a query

## Review questions

- [ ] Confirm the degree designation (B.A.?) - the page does not state it.
- [ ] Should ECON_CALC accept 01:640:151 as the 'equivalent'?
- [ ] Are 01:220 honors/independent-study/internship courses (e.g. 1-credit internship) valid electives?

## What to verify

- [ ] Open the archived page (path below) and find every quoted sentence.
- [ ] For each requirement node, confirm the encoding says what the quote says: course lists, counts, levels, exclusions.
- [ ] Confirm nothing the page requires for COMPLETION is missing - compare against 'rules not yet modeled'.
- [ ] Confirm admission/declaration rules are kept as admission_prose and not as completion requirements.
- [ ] Answer every review question; a 'changes_requested' review is a valid outcome.
- [ ] Spot-check the eligible-course counts below against the course list you expect.

## Admission (kept separate from completion)

> To declare a major in economics, a student must have a grade of C or better in both 01:220:102 Introduction to Microeconomics and 01:220:103 Introduction to Macroeconomics. The student must also have completed the required calculus course with a grade of C or better.

## Requirement nodes

| node | type | parent | encoding | eligible rows |
|---|---|---|---|---|
| `ECON_MAJOR` | all_of |  |  | 0 |
| `ECON_CORE` | all_of | ECON_MAJOR | minimum grade C (this node and its subtree) | 0 |
| `ECON_102` | course | ECON_CORE | courses: 01:220:102 | 1 |
| `ECON_103` | course | ECON_CORE | courses: 01:220:103 | 1 |
| `ECON_320` | course | ECON_CORE | courses: 01:220:320 | 1 |
| `ECON_321` | course | ECON_CORE | courses: 01:220:321 | 1 |
| `ECON_322` | course | ECON_CORE | courses: 01:220:322 | 1 |
| `ECON_STATS` | choose_n | ECON_MAJOR | min_count=1<br>courses: 01:960:211, 01:960:285<br>minimum grade C (this node and its subtree) | 2 |
| `ECON_CALC` | course | ECON_MAJOR | courses: 01:640:135<br>minimum grade C (this node and its subtree) | 1 |
| `ECON_ELECTIVES` | choose_n | ECON_MAJOR | min_count=7<br>courses: 33:010:272, 33:010:275<br>query {"exclude_course_numbers": ["102", "103", "200", "320", "321", "322"], "offering_unit_code": "01", "subject_code": "220"}<br>grade quota: at most 1 at or below D | 39 |

### Quotes and notes

- **ECON_MAJOR**: "The seven required courses (five in economics, one in statistics, and one in mathematics) plus seven electives within economics (which may, under certain circumstances, include a limited number of courses from related disciplines) constitute the major."
- **ECON_CORE**: "The foundation of the curriculum in economics consists of 01:220:102,103, 320, 321, and 322. ... A grade of C or better is required in 01:102,103, 320, 321, 322, the required statistics course, and the required calculus course."
- **ECON_STATS**: "It also requires one semester of statistics (01:960:211 or preferably 285) with a grade of C or better. ... A grade of C or better is required in 01:102,103, 320, 321, 322, the required statistics course, and the required calculus course."
- **ECON_CALC**: "One semester of calculus (01:640:135 or equivalent) with a grade of C or better also is required. ... A grade of C or better is required in 01:102,103, 320, 321, 322, the required statistics course, and the required calculus course."
  - note: 'or equivalent' is not modeled: the page does not name the equivalents (01:640:151 is the obvious candidate, but the page does not say so).
- **ECON_ELECTIVES**: "plus seven electives within economics (which may, under certain circumstances, include a limited number of courses from related disciplines) ... Students may take 33:010:272 and 275. These courses will count toward the required seven electives ... Only one elective course with a grade of D can count toward the major."
  - note: Any undergraduate 01:220 course other than the required ones, plus the two accounting courses the page names. OVER-APPROXIMATION: the 'no more than three lower-level electives' limit depends on each course's prerequisites and is not modeled; 'related disciplines' courses are not named and are not encoded.

## Program rules

- `ECON_MAJOR_GPA` (min_gpa, evaluable=True): "To satisfactorily complete the major, students must have a minimum cumulative grade-point average of 2.0 in the major."

## Rules not yet modeled

- Students can count no more than three courses that have no prerequisites, or whose prerequisites are only 01:220:102 and 103 (or only 200), toward the seven required electives.  
  _why:_ Classifying a course as 'lower-level' needs its prerequisite structure; Phase 6.2 parses prerequisites but the requirement layer does not use them.
- Engineering students who take 01:220:200 use it in place of 01:220:102 and 103 and take eight electives instead of seven.  
  _why:_ A school-dependent alternative that also changes the elective count; not representable without a student-attribute condition.
- A maximum of three economics courses taken outside the Department of Economics at Rutgers University-New Brunswick may be applied toward the major.  
  _why:_ Course location/transfer provenance is not recorded on StudentCourse.
- Which courses make up the 'grade-point average ... in the major'.  
  _why:_ Phase 6.4 encodes the 2.0 rule (ECON_MAJOR_GPA) but its scope is not defined by the catalog, so it is reported NOT_EVALUABLE, never computed. Minimum grades and the one-D elective quota are now encoded.
- Students who major in economics (220) may not minor in environmental and business economics (373).  
  _why:_ Cross-program restrictions are out of scope.

## How to record your decision

```
python -m coursepilot_ingestion.registry_cli review --program sas-220-major --year 2026-2027 --reviewer "<your full name>" --decision approved|changes_requested --notes "..."  (then, if approved: registry_cli publish --program ... --year ...)
```
