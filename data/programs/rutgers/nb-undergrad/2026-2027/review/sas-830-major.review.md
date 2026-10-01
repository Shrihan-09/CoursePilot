# Review packet: `sas-830-major` (2026-2027)

- **Lifecycle:** `validated`
- **Encoded by:** AI assistant (Claude), Phase 6.3 - encoder only, not a reviewer - encoding is not review
- **Definition:** `data/programs/rutgers/nb-undergrad/2026-2027/sas-830-major.json`  sha256 `1b88f78c556eea29`
- **Source:** https://newbrunswick-26-27-undergrad.catalogs.rutgers.edu/schools/sas/program-listing/psychology-830
- **Archive:** `data/raw/catalog/catalog_psychology-830_2026_2027.html`  prose sha256 `85c5bf3af0396d47`

## Machine checks (not review)

- passed: **True**, quotes found verbatim: 6/6
- 1 course(s) named by the definition are not in the loaded course table (reported, never created), e.g. 01:830:361

## Review questions

- [ ] Confirm the degree designation - the page does not state it.
- [ ] Is item 4 (one 400-level elective) IN ADDITION to the six electives, as encoded, or one of them?
- [ ] Do 01:830:101 / 01:830:200 count toward the six electives?

## What to verify

- [ ] Open the archived page (path below) and find every quoted sentence.
- [ ] For each requirement node, confirm the encoding says what the quote says: course lists, counts, levels, exclusions.
- [ ] Confirm nothing the page requires for COMPLETION is missing - compare against 'rules not yet modeled'.
- [ ] Confirm admission/declaration rules are kept as admission_prose and not as completion requirements.
- [ ] Answer every review question; a 'changes_requested' review is a valid outcome.
- [ ] Spot-check the eligible-course counts below against the course list you expect.

## Admission (kept separate from completion)

> The prerequisite for declaring the major in psychology is completion of the following courses with a grade of C or better in each:

## Requirement nodes

| node | type | parent | encoding | eligible rows |
|---|---|---|---|---|
| `PSYC_MAJOR` | all_of |  |  | 0 |
| `PSYC_CORE` | choose_n | PSYC_MAJOR | min_count=4<br>min_distinct_categories=4<br>BEHAV_NEURO: 01:830:310, 01:830:311, 01:830:313, 01:830:361; CLINICAL: 01:830:310, 01:830:340, 01:830:346, 01:830:394; COGNITIVE: 01:830:301, 01:830:303, 01:830:305, 01:830:351; SOCIAL: 01:830:321, 01:830:338, 01:830:339, 01:830:377 | 15 |
| `PSYC_ELECTIVES` | choose_n | PSYC_MAJOR | min_count=6<br>query {"offering_unit_code": "01", "subject_code": "830"} | 71 |
| `PSYC_400` | choose_n | PSYC_MAJOR | min_count=1<br>query {"max_course_number": 499, "min_course_number": 400, "offering_unit_code": "01", "subject_code": "830"} | 21 |

### Quotes and notes

- **PSYC_MAJOR**: "The following requirements must be met to complete a major in psychology:"
- **PSYC_CORE**: "Four Subdiscipline Core Courses. (A separate course must be selected from each of the following four subdiscipline clusters and must be taken within the 01:830 subject index of the School of Arts and Sciences [SAS] Department of Psychology.)"
  - note: 01:830:310 appears in two clusters; one course can satisfy only one cluster ('A separate course must be selected from each').
- **PSYC_ELECTIVES**: "Completion of Six Psychology Electives. (To be selected from the department's complete set of course offerings.)"
  - note: Any undergraduate 01:830 course. NOT modeled: at most three 200-level courses, at most 6 credits of nonclassroom courses, at least one elective in the SAS department. Whether 01:830:101 and 200 (the declaration prerequisites) count as electives is a REVIEW QUESTION.
- **PSYC_400**: "Completion of One 400-Level Elective."

## Rules not yet modeled

- One 4-Credit Content Course and Lab Combination.  
  _why:_ The page does not list which courses form a content-and-lab combination.
- No more than 6 credits of nonclassroom courses such as fieldwork, research, or internships may be applied toward the major.  
  _why:_ Course format (classroom vs nonclassroom) is not recorded.
- No more than three 200-level courses (10 credits) may be applied.  
  _why:_ A per-level maximum is not an engine constraint today.
- Grade of C or better in 01:830:101 and 01:830:200 to declare.  
  _why:_ Admission rule; kept as admission_prose, not degree completion.

## How to record your decision

```
python -m coursepilot_ingestion.registry_cli review --program sas-830-major --year 2026-2027 --reviewer "<your full name>" --decision approved|changes_requested --notes "..."  (then, if approved: registry_cli publish --program ... --year ...)
```
