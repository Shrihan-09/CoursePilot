# Review packet: `sas-920-major` (2026-2027)

- **Lifecycle:** `validated`
- **Encoded by:** AI assistant (Claude), Phase 6.3 - encoder only, not a reviewer - encoding is not review
- **Definition:** `data/programs/rutgers/nb-undergrad/2026-2027/sas-920-major.json`  sha256 `03595a2a5498a20d`
- **Source:** https://newbrunswick-26-27-undergrad.catalogs.rutgers.edu/schools/sas/program-listing/sociology-920
- **Archive:** `data/raw/catalog/catalog_sociology-920_2026_2027.html`  prose sha256 `fd3ea25f39c39fb4`

## Machine checks (not review)

- passed: **True**, quotes found verbatim: 7/7

## Review questions

- [ ] Confirm the degree designation - the page does not state it.

## What to verify

- [ ] Open the archived page (path below) and find every quoted sentence.
- [ ] For each requirement node, confirm the encoding says what the quote says: course lists, counts, levels, exclusions.
- [ ] Confirm nothing the page requires for COMPLETION is missing - compare against 'rules not yet modeled'.
- [ ] Confirm admission/declaration rules are kept as admission_prose and not as completion requirements.
- [ ] Answer every review question; a 'changes_requested' review is a valid outcome.
- [ ] Spot-check the eligible-course counts below against the course list you expect.

## Admission (kept separate from completion)

> Prior to declaring the major in sociology, students must complete Introduction to Sociology (01:920:101) and one of the following courses: 01:920:215, 01:920:311, 01:920:312, 01:920:316.

## Requirement nodes

| node | type | parent | encoding | eligible rows |
|---|---|---|---|---|
| `SOC_MAJOR` | all_of |  |  | 0 |
| `SOC_REQUIRED` | all_of | SOC_MAJOR |  | 0 |
| `SOC_101` | course | SOC_REQUIRED | courses: 01:920:101 | 1 |
| `SOC_215` | course | SOC_REQUIRED | courses: 01:920:215 | 1 |
| `SOC_311` | course | SOC_REQUIRED | courses: 01:920:311 | 1 |
| `SOC_312` | course | SOC_REQUIRED | courses: 01:920:312 | 1 |
| `SOC_316` | course | SOC_REQUIRED | courses: 01:920:316 | 1 |
| `SOC_ELECTIVES` | choose_n | SOC_MAJOR | min_count=6<br>min_at_level=300<br>min_at_level_count=3<br>constraint_subject_code=920<br>query {"exclude_course_numbers": ["101", "215", "311", "312", "316"], "offering_unit_code": "01", "subject_code": "920"} | 29 |

### Quotes and notes

- **SOC_MAJOR**: "The major in sociology consists of 11 courses totaling 36 credits. ... Of these eleven courses, five are required courses and six are electives."
- **SOC_REQUIRED**: "Of these eleven courses, five are required courses and six are electives."
- **SOC_ELECTIVES**: "3 Courses - any level (each in a different thematic - see checklist) ... 3 Courses - 300-level or higher (in any thematic)"
  - note: NOT modeled: the three any-level electives must each be in a different 'thematic', defined by a departmental checklist the catalog does not publish.

## Rules not yet modeled

- Three any-level electives each in a different thematic (see checklist).  
  _why:_ The thematic checklist is not published in the catalog.
- Students majoring in sociology must complete at least six courses (21 credits) at Rutgers University-New Brunswick. Each of the three 300-level core courses must be completed in New Brunswick.  
  _why:_ Course location/transfer provenance is not recorded on StudentCourse.
- Grades of C or better are required in each of the courses.  
  _why:_ Minimum grades are out of scope (Phase 6.3).

## How to record your decision

```
python -m coursepilot_ingestion.registry_cli review --program sas-920-major --year 2026-2027 --reviewer "<your full name>" --decision approved|changes_requested --notes "..."  (then, if approved: registry_cli publish --program ... --year ...)
```
