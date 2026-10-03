# Review packet: `sas-615-major` (2026-2027)

- **Lifecycle:** `validated`
- **Encoded by:** AI assistant (Claude), Phase 6.3 - encoder only, not a reviewer - encoding is not review
- **Definition:** `data/programs/rutgers/nb-undergrad/2026-2027/sas-615-major.json`  sha256 `2f7a9a6e4914a090`
- **Source:** https://newbrunswick-26-27-undergrad.catalogs.rutgers.edu/schools/sas/program-listing/linguistics-615
- **Archive:** `data/raw/catalog/catalog_linguistics-615_2026_2027.html`  prose sha256 `e52bbf8c4b60df84`

## Machine checks (not review)

- passed: **True**, quotes found verbatim: 8/8

## Review questions

- [ ] Confirm the degree designation - the page does not state it.
- [ ] Does '01:615:495/496' mean both semesters, or either one?

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
| `LING_MAJOR` | all_of |  | minimum grade C (this node and its subtree) | 0 |
| `LING_201` | course | LING_MAJOR | courses: 01:615:201 | 1 |
| `LING_CORE` | all_of | LING_MAJOR |  | 0 |
| `LING_305` | course | LING_CORE | courses: 01:615:305 | 1 |
| `LING_315` | course | LING_CORE | courses: 01:615:315 | 1 |
| `LING_325` | course | LING_CORE | courses: 01:615:325 | 1 |
| `LING_350` | course | LING_CORE | courses: 01:615:350 | 1 |
| `LING_ELECTIVES` | choose_n | LING_MAJOR | min_count=6<br>query {"exclude_course_numbers": ["305", "315", "325", "350", "495", "496", "497"], "min_course_number": 300, "offering_unit_code": "01", "subject_code": "615"} | 21 |
| `LING_CAPSTONE` | any_of | LING_MAJOR |  | 0 |
| `LING_THESIS` | all_of | LING_CAPSTONE |  | 0 |
| `LING_495` | course | LING_THESIS | courses: 01:615:495 | 1 |
| `LING_496` | course | LING_THESIS | courses: 01:615:496 | 1 |
| `LING_497` | course | LING_CAPSTONE | courses: 01:615:497 | 1 |

### Quotes and notes

- **LING_MAJOR**: "A major in linguistics consists of 12, 3-credit courses offered by the School of Arts and Sciences, distributed as follows: ... Grades of C or better must be earned in all coursework that is to be applied to the major."
- **LING_201**: "Introduction: one introductory course (01:615:201)"
- **LING_CORE**: "Theory: four B core theoretical courses (01:615:305, 315, 325, 350)"
- **LING_ELECTIVES**: "Electives: six courses ... At least three (3-6) C courses chosen from a list of linguistics courses at the 300 level or above"
  - note: OVER-APPROXIMATION: the page refers to lists of 'C' and 'D' courses and approved outside courses that it does not publish, so any undergraduate 01:615 course at 300+ is accepted and outside courses are not encoded.
- **LING_CAPSTONE**: "Capstone experience: either a senior honors thesis (01:615:495/496) or the advanced seminar (01:615:497)"
- **LING_THESIS**
  - note: '495/496' read as the two-semester thesis; a REVIEW QUESTION.

## Rules not yet modeled

- At most three (0-3) D courses chosen from a list of linguistics courses at the 300 level or above and/or from the list of approved courses from outside the department.  
  _why:_ The C/D course lists and the approved outside-course list are not published in the catalog.

## How to record your decision

```
python -m coursepilot_ingestion.registry_cli review --program sas-615-major --year 2026-2027 --reviewer "<your full name>" --decision approved|changes_requested --notes "..."  (then, if approved: registry_cli publish --program ... --year ...)
```
