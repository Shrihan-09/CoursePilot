# Review packet: `sas-730-major` (2026-2027)

- **Lifecycle:** `validated`
- **Encoded by:** AI assistant (Claude), Phase 6.3 - encoder only, not a reviewer - encoding is not review
- **Definition:** `data/programs/rutgers/nb-undergrad/2026-2027/sas-730-major.json`  sha256 `993d96acfff9dd0e`
- **Source:** https://newbrunswick-26-27-undergrad.catalogs.rutgers.edu/schools/sas/program-listing/philosophy-730
- **Archive:** `data/raw/catalog/catalog_philosophy-730_2026_2027.html`  prose sha256 `3afe684f3adc05b4`

## Machine checks (not review)

- passed: **True**, quotes found verbatim: 10/10
- 27 course(s) named by the definition are not in the loaded course table (reported, never created), e.g. 01:730:407, 01:730:408, 01:190:322, 01:190:353, 01:730:306, 01:730:311

## Review questions

- [ ] Confirm the degree designation - the page does not state it.
- [ ] 01:190:322 and 01:190:353 are Classics courses counted as 'in philosophy' by the page - confirm they count toward the eleven.

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
| `PHIL_MAJOR` | all_of |  |  | 0 |
| `PHIL_LOGIC` | choose_n | PHIL_MAJOR | min_count=1<br>courses: 01:730:109, 01:730:201, 01:730:315, 01:730:407, 01:730:408 | 3 |
| `PHIL_ANCIENT` | choose_n | PHIL_MAJOR | min_count=1<br>courses: 01:190:322, 01:190:353, 01:730:301, 01:730:302, 01:730:304, 01:730:305, 01:730:306, 01:730:311, 01:730:352, 01:730:401, 01:730:402, 01:730:403 | 6 |
| `PHIL_MODERN` | choose_n | PHIL_MAJOR | min_count=1<br>courses: 01:730:307, 01:730:308, 01:730:309, 01:730:404, 01:730:405, 01:730:406, 01:730:416, 01:730:417 | 4 |
| `PHIL_ETHICS` | choose_n | PHIL_MAJOR | min_count=1<br>courses: 01:730:330, 01:730:341, 01:730:342, 01:730:345, 01:730:441, 01:730:442, 01:730:445, 01:730:450, 01:730:459, 01:730:470 | 6 |
| `PHIL_CORE_AREAS` | choose_n | PHIL_MAJOR | min_count=2<br>min_at_level=400<br>min_at_level_count=1<br>constraint_subject_code=730<br>courses: 01:730:210, 01:730:215, 01:730:220, 01:730:225, 01:730:226, 01:730:256, 01:730:303, 01:730:319, 01:730:320, 01:730:327, 01:730:328, 01:730:329, 01:730:360, 01:730:370, 01:730:410, 01:730:412, 01:730:413, 01:730:415, 01:730:418, 01:730:419, 01:730:420, 01:730:421, 01:730:422, 01:730:423, 01:730:424, 01:730:425, 01:730:426, 01:730:427, 01:730:428, 01:730:429, 01:730:435 | 20 |
| `PHIL_ELECTIVES` | choose_n | PHIL_MAJOR | min_count=5<br>query {"offering_unit_code": "01", "subject_code": "730"} | 75 |

### Quotes and notes

- **PHIL_MAJOR**: "Students must pass a minimum of 11 classroom courses of 3 or more credits each in philosophy to earn the major. ... Among these courses must be the following:"
- **PHIL_LOGIC**: "One semester of logic from among the following:"
- **PHIL_ANCIENT**: "One semester of ancient or medieval philosophy from among the following:"
- **PHIL_MODERN**: "One semester of modern philosophy from among the following:"
- **PHIL_ETHICS**: "One semester of advanced ethics or political philosophy from among the following:"
- **PHIL_CORE_AREAS**: "Two courses from among the following, at least one of which must be at the 400 level:"
- **PHIL_ELECTIVES**: "Students must pass a minimum of 11 classroom courses of 3 or more credits each in philosophy to earn the major."
  - note: 11 courses minus the 6 named above = 5 more 01:730 courses. NOT modeled: 'classroom' courses only, at least one 200-level (excluding 201, 202, 295, 296), at least six at the 300/400 level (excluding 495, 496) across all eleven.

## Program rules

- `PHIL_MAX_D` (max_grade_count, evaluable=True): "No more than one D grade can be applied toward the major."

## Rules not yet modeled

- At least one of these courses must be at the 200 level, excluding 201, 202, 295, and 296. At least six of these courses must be at the 300 or 400 level, excluding 495 and 496.  
  _why:_ Level distribution across the whole major (all eleven courses) is not an engine constraint today.
- A student may petition the department to substitute other courses for those on this list.  
  _why:_ Petitions are individual decisions, not data.

## How to record your decision

```
python -m coursepilot_ingestion.registry_cli review --program sas-730-major --year 2026-2027 --reviewer "<your full name>" --decision approved|changes_requested --notes "..."  (then, if approved: registry_cli publish --program ... --year ...)
```
