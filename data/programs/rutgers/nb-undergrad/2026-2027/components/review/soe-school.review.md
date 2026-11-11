# Review packet: `soe-school` (component, gen_ed)

- **Lifecycle:** `pending_review`
- **Encoded by:** manual encoding, Prompt 08 B1 (Claude Code session, 2026-11-11) - encoding is not review
- **Definition:** `data/programs/rutgers/nb-undergrad/2026-2027/components/soe-school.json`  semantic sha256 `c676c356666399d2`

## Sources

| id | role | status | retrieved (UTC) | representation | sha256 | url |
|---|---|---|---|---|---|---|
| `eng-catalog-degree-requirements` | catalog | archived | 2026-11-11T19:09:05+00:00 | raw_html | `719d2b26b265cae8` | https://newbrunswick-26-27-undergrad.catalogs.rutgers.edu/schools/eng/degree-requirements |
| `eng-catalog-first-year` | catalog | archived | 2026-11-11T19:09:07+00:00 | raw_html | `9913d2ee379fdb27` | https://newbrunswick-26-27-undergrad.catalogs.rutgers.edu/schools/eng/programs-study/first-year |
| `soc-core-code-descriptions` | soc | not in manifest |  |  | `` |  |
| `soe-engineering-curriculum` | department | failed (open evidence gap) | 2026-11-11T19:09:10+00:00 | failed | `` | https://soe.rutgers.edu/academics/undergraduate/engineering-curriculum |
| `soe-pass-no-credit` | department | failed (open evidence gap) | 2026-11-11T19:09:14+00:00 | failed | `` | https://soe.rutgers.edu/academic-advising-and-policies/academic-policies/passno-credit-courses |
- `soc-core-code-descriptions`: SOC coreCodes as loaded in offering_core_code (each row's data_source is its archived SOC payload).
- `soe-engineering-curriculum`: HTTP 403 to a plain GET on 2026-11-11: open evidence gap (H/SS distribution rules).
- `soe-pass-no-credit`: HTTP 403 to a plain GET on 2026-11-11: open evidence gap.

## Machine checks (not review)

- passed: **True**, quotes found verbatim: 9/9

## Review questions

- [ ] Confirm that SOC's SOEHS certification ('SOE: Approved Humanities/Social Science') is the authoritative H/SS list for every term a student may take H/SS.
- [ ] Confirm the two first-year H/SS electives are school-wide (curriculum 004, 'common to all four-year engineering programs') and that each major's own H/SS slots are in addition to them.

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
| `SOE_SCHOOL` School of Engineering requirements | all_of |  |  | 0 |
| `SOE_HSS_FIRST_YEAR` Humanities/Social Sciences electives (first-year program) | choose_n | SOE_SCHOOL | min_count=2<br>SOC certification per term: SOEHS | 1371 |

### Verbatim quotes per node

- **SOE_SCHOOL**:
  - `eng-catalog-degree-requirements`: "Students must complete a prescribed program of study in their declared major, outlined in the Programs of Study chapter, including the distribution requirements for departmental, technical, general, and humanities/social sciences electives."
- **SOE_HSS_FIRST_YEAR**:
  - `eng-catalog-first-year`: "Elective: Humanities/Social Sciences (H/SS) (3cr) * &"
  - `eng-catalog-first-year`: "& May be completed at any time before graduation; it is recommended that students take one lower H/SS during the first year if their course load is not too heavy with the required courses."
  - `soc-core-code-descriptions`: "SOEHS: SOE: Approved Humanities/Social Science"
  - note: Two H/SS electives appear in the first-year program common to all four-year curricula; one may be completed any time before graduation. Eligibility: SOC's per-term SOEHS certification.

## Rules

- `SOE_MIN_GPA` (min_gpa, evaluable=True): Cumulative GPA of at least 2.000
  - `eng-catalog-degree-requirements`: "Students must earn a cumulative grade point average (GPA) of at least 2.000"
- `SOE_MAJOR_GPA` (min_gpa, evaluable=False): GPA of at least 2.000 in the major (M-prefixed courses)
  - not evaluated: The major GPA is over courses designated with an 'M' prefix on the record; CoursePilot's records do not carry that designation.
  - `eng-catalog-degree-requirements`: "a cumulative grade point average in the major (as designated by "M" prefixes) of at least 2.000 to be awarded the degree of bachelor of science from the School of Engineering"
- `SOE_30_RUTGERS_CREDITS` (residency_window, evaluable=False): At least 30 credits earned at Rutgers
  - not evaluated: Needs certified attempt institution per credit; not evaluated yet.
  - `eng-catalog-degree-requirements`: "The degree of bachelor of science from the School of Engineering is not awarded to any candidate who has earned fewer than 30 credits at Rutgers."
- `SOE_LAST_42` (residency_window, evaluable=False): No more than 12 of the last 42 credits outside Rutgers
  - not evaluated: A chronological window over the last 42 credits; not evaluated yet.
  - `eng-catalog-degree-requirements`: "No more than 12 of the last 42 credits for the degree may be taken outside Rutgers."

## Rules not yet modeled

- Degree total: 120 to 132 credits depending on the major.  
  _why:_ No school-wide total exists; each major's encoding states its own (program_version.degree_credits). A plan without one says so - 120 is never assumed.  
  `eng-catalog-degree-requirements`: "The total number of credits required for graduation varies from 120 to 132 credits in the four-year program, depending on the major."
- H/SS distribution: total number of H/SS courses/credits and how many at the 200 level or above.  
  _why:_ Stated on soe.rutgers.edu (HTTP 403 to a plain GET); unknown until archived.
- Pass/No Credit limits for SoE students.  
  _why:_ Department page returned HTTP 403; unknown.
- Departmental, technical and general elective categories.  
  _why:_ They differ per major and are encoded with each major's curriculum.

## Unknowns

- H/SS distribution rules (soe.rutgers.edu engineering curriculum page, HTTP 403).
- SoE Pass/No Credit policy (HTTP 403).

## How to record your decision

```
Components have no review command yet: record the decision in the PR / review notes; the component stays 'pending_review' (and every audit and index label says so) until a component review command exists.
```
