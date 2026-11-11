# Review packet: `soe-major-declaration` (component, entry_stage)

- **Lifecycle:** `pending_review`
- **Encoded by:** manual encoding, Prompt 08 B1 (Claude Code session, 2026-11-11) - encoding is not review
- **Definition:** `data/programs/rutgers/nb-undergrad/2026-2027/components/soe-major-declaration.json`  semantic sha256 `e0ddcfc5380ac19b`

## Sources

| id | role | status | retrieved (UTC) | representation | sha256 | url |
|---|---|---|---|---|---|---|
| `eng-catalog-programs-study` | catalog | archived | 2026-11-11T19:10:56+00:00 | raw_html | `de71815c025c38a2` | https://newbrunswick-26-27-undergrad.catalogs.rutgers.edu/schools/eng/programs-study/programs |
| `eng-catalog-degree-requirements` | catalog | archived | 2026-11-11T19:09:05+00:00 | raw_html | `719d2b26b265cae8` | https://newbrunswick-26-27-undergrad.catalogs.rutgers.edu/schools/eng/degree-requirements |
| `soe-declaring-major` | department | failed (open evidence gap) | 2026-11-11T19:09:12+00:00 | failed | `` | https://soe.rutgers.edu/academic-advising-and-policies/academic-policies/declaring-major |
- `soe-declaring-major`: HTTP 403 to a plain GET on 2026-11-11: open evidence gap.

## Machine checks (not review)

- passed: **True**, quotes found verbatim: 2/2

## Review questions

- [ ] Are there entry requirements for declaring an SoE major in 2026-2027 (Phase A noted current guidance says majors are open without a GPA minimum, from an unarchived page)?

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

### Verbatim quotes per node


## Rules not yet modeled

- Students declare a major after the first-year program (curriculum 004).  
  _why:_ The stage itself is modeled (labels); its timing and any prerequisites are on soe.rutgers.edu (HTTP 403) and remain unknown.  
  `eng-catalog-programs-study`: "Students in the first year of a four-year curriculum are listed under curriculum code 004."
- Students complete the prescribed program of study in their DECLARED major.  
  _why:_ Modeled: each SoE major's own nodes come after this stage (program_version.admission_stage).  
  `eng-catalog-degree-requirements`: "Students must complete a prescribed program of study in their declared major"

## Unknowns

- Declaration timing, deadlines and any minimum grades/GPA (soe.rutgers.edu declaring-major page, HTTP 403).

## How to record your decision

```
Components have no review command yet: record the decision in the PR / review notes; the component stays 'pending_review' (and every audit and index label says so) until a component review command exists.
```
