# Task 2: planning questionnaire, commitments and workload-aware plans (Prompt 09)

Branch `feature/task2-planning-questionnaire` → `main`, built on main after Task 1 (#23). The spec is `prompts/09-planning-interview-and-workload.md`, added in this PR.

## Checklist against Prompt 09

| Spec item | Before | Now |
|---|---|---|
| §1.1 Credit minimum is a hard floor | soft (14-credit terms on a 16 minimum) | **hard**; see "Credit floor" below |
| §1.2 Honors-only / admission-required courses gated | not gated (01:198:495/496 recommended) | catalog title + SOC honors restriction + "special permission on every section" |
| §1.3 Excluded courses never count as major | true in the audit | also never counted toward the major range; a pin warns (`PIN_WILL_NOT_COUNT`); tested |
| §2 Questionnaire: 12 topics, skippable, onboarding + Plan page, per semester | level and filler questions only | all 12 except "avoid bus-heavy days"; "Clear my answers" |
| §3 Hours baseline → budget; "your rush semester…" note; floor vs budget conflict | not built (`WORKLOAD_VS_GRADUATION_TARGET` documented, never implemented) | built, plus the pace/level conflict warning |
| §3 Fixed blocks as blocked time in the schedule engine | before/after times, days off | + job/club/… blocks; honors-only sections; preferred campuses |
| §4 Fill order | anchors → multi → ONE major-subject course → fillers | retakes → pins → major-related up to the range → other multi → fillers (decided in review; see the follow-up below) |
| §5 Per term: totals, role badges, "Why this fits you", shortfalls | totals, role text, notes | all four |
| §6 Tests, scenarios, wording grep, plan_quality, three-profile preview | – | all; below |

## What changed

### Questionnaire (one tested module: `app/services/planning/questionnaire.py`)

| Question | Answer | Default (skipped) |
|---|---|---|
| Rigor | Light / Balanced / Semi-rigorous / Challenging | Balanced |
| Credits | the level's range, or the student's own min/max | level's range |
| Major focus | the level's default, or 1 / 2–3 / 3–4 | level: Light 1–2, Balanced 2–3, Semi 3, Challenging 3–4 |
| Retakes & musts | pins (retakes come from the record) | none |
| Job | none / <10 / 10–20 / 20+ h/week (stored as 0/8/15/25) | none |
| Greek life | none / rushing / new member / active (0/6/10/4 h) | none |
| Clubs & orgs | none / light / heavy (0/3/8 h) | none |
| Athletics, research, recruiting | h/week each | 0 |
| Schedule shape | not before 10 AM / Fridays / evenings + custom fixed blocks | none |
| Format | flexible (async first) / in person / no preference | no preference |
| Campus | preferred campuses (orders schedules only) | none |
| Honors | yes / no / skip | skip = "no" |

- **Per-semester answers.** A semester can have its own credit range, major range, commitments and fixed times. The precedence is semester → the student's own → level.
- **Privacy.** Only commitment types and hours are stored. The models have no field for any name, and unknown fields are rejected.
- **Assistant proposals.** Everything lives on the planning profile, so the future assistant can propose answers through the same validated structure. The student still confirms.

**Budget.** The baseline is 60 hours a week for school; commitments come off it. The budget drops 1 point per 3 hours (to the nearest 0.5) and never goes below the credit minimum.

**Credit floor.** Below the minimum, limits give way in this order: commitments' reduction → major range → level budget. Each is noted on the term. The last one also raises `CREDIT_FLOOR_OVER_BUDGET`, which offers the choice to lower that semester's minimum. The credit max and the course limit never give way.

### "Major-related"

A course is major-related if it counts toward the major's requirement tree, including its required math and science. The range can be exceeded only in three cases, and each is said on the term:
- anchors alone exceed it;
- the graduation target needs more (the plan is never silently extended);
- the credit floor needs it.

### Honors and admission (§1.2)

**Honors.** A course is honors-only when either is true:
- its catalog title is `\bHONORS?\b`. 01:198:495/496 have no sections or restriction rows in any loaded term, so the title is the only evidence;
- every section carries only SOC `honorPrograms` entries.

It is planned only with honors = yes, and stays needs-confirmation.

**Permission-only.** A course where every section needs a special permission number (e.g. 01:198:494) is never planned for a requirement. Pinned, it is placed with `PIN_NEEDS_PERMISSION`.

**Not applied to prerequisite-path courses.** 01:640:001 and 01:355:095 carry a permission code on every section, but students enter them by placement. Excluding them stranded every B.S. science sequence for a student with no record, which re-planned that plan three times and doubled its time. Measured and fixed.

### Every term shows

- totals: credits, contact hours, workload / budget;
- role badges: Anchor / Counts twice / Major / Filler;
- notes and shortfalls (below the range or the floor, with the reason);
- a fit line, e.g.:

  > Why this fits you: Light + rushing + job 15 h/week → *credits*, *n* major-related courses (your range: 1-2), async filler, budget 12 instead of 16 for your commitments, workload *w* of 12.

A final light term says "these are the last courses your requirements need".

### Speed

- `rank()` is memoized per term. The floor passes re-run the filler loop, and it re-ranked hundreds of candidates on every pick.
- The workload index loads all candidates' meetings in one query.

## Migrations

- **`t2quest01`:**
  - `commitments` JSON;
  - `honors` boolean;
  - `term_overrides` JSON.

  Existing rows are backfilled through a temporary server default, which is then dropped.
- **`t2quest02`:** `credit_min`, `credit_max`, `major_courses_min`, `major_courses_max`, all nullable integers.

Both are additive. `verify_migration_roundtrip.py` passes (upgrade → downgrade → upgrade → `alembic check` clean).

**Dev DB:**
- backed up first to `coursepilot_pretask2_202611021456.dump` in the private backups folder (verified readable, 58 table-data entries);
- upgraded to `t2quest02`. The one existing profile has every default.

**Test DB** upgraded too. No other data steps.

## Tests

All on the final code, against the test database:

| Suite | Result |
|---|---|
| backend | **1,191 passed**, 4 skipped |
| ingestion | **1,104 passed**, 1 skipped, 1 setup error |
| frontend | **107 passed**, `tsc` clean |
| targeted Task 2 + science-alternative + planning-engine | 119 passed |

The ingestion setup error is a `FileNotFoundError` for a temp folder in `test_monitoring_postgres.py`, which this PR doesn't touch. That file re-run alone: 6 passed.

**New or updated tests:**
- **`backend/tests/test_planning_questionnaire.py`** (43 tests):
  - the answers → settings table;
  - per-question defaults;
  - precedence;
  - the hour and budget mappings;
  - validation;
  - fixed-time overlap and the schedule engine's blocked and honors-only sections;
  - the honors title regex;
  - wording.
- **`ingestion/tests/test_workload_levels.py`** (real public SOC fixture, fabricated student), adds:
  - spec scenario 1 (unchanged), plus scenarios 2 (Light, rushing, 15 h job) and 3 (Challenging);
  - each level's range;
  - a per-semester override;
  - a profile-wide range;
  - anchors over the range;
  - graduation forcing more;
  - commitments;
  - the hard floor (19-credit minimum on Light);
  - honors (SOC restriction and catalog title);
  - permission-only;
  - exclusions;
  - fill order;
  - a grep of the full plan JSON for easy/hard.
- **Changed:** the in-person filler test. Linear Algebra counts toward the major now, so it's no longer a "filler", and the in-person Core candidate is over budget. The test now pins what the preference still controls.

**Failure injection** (`scripts/inject_questionnaire_failures.py`, fresh `--basetemp`, test DB): **14/14 faults detected by assertion failures**, 0 setup errors:
- **A–E:** no range cap; anchors dropped at the cap; no graduation raise; override ignored; commitments ignored.
- **F, L, M:** honors-only planned, catalog-title honors planned, permission-only planned.
- **G:** fixed times not blocked.
- **H:** major means only the major's own subject.
- **I:** no pace/level warning.
- **J:** no fit line.
- **K:** floor gives way to the range.
- **N:** the student's credit range ignored.

Two faults (B, K) were missed in a first run. Each led to a stronger test: the anchors test now has no graduation target that could hide the cap, and a 19-credit minimum on Light exercises the floor.

The permission-gate regression above (prerequisite path) has no fixture test: the test world has no prerequisite-only course. It is covered by plan_quality's "nothing yet" persona instead (re-plan count back to 1, same as main).

## Plan quality and speed

`plan_quality.py`, 36 plans per program. It now also runs Light, Balanced and Challenging, and adds a `major/term` column. "Main" is `e4ece8b`, run via `COURSEPILOT_PQ_ROOT`.

| | main | **this PR (spec order)** | alternative order (decision 1) |
|---|---|---|---|
| B.A. full coverage | 27/36 | 27/36 | 27/36 |
| B.S. full coverage | 23/36 | **25/36** | 26/36 |
| Level terms with major-related count in range, B.A. | 44% | 53% | 61% |
| Level terms with major-related count in range, B.S. | 39% | 29% | **50%** |
| Level terms under the credit floor, B.A. | 36 | 20 | 21 |
| Level terms under the credit floor, B.S. | 40 | 15 | 15 |
| First-term major-related, B.A. (mean) | 0.00 | 1.81 | 2.25 |
| First-term major-related, B.S. (mean) | 0.75 | 1.56 | 2.19 |
| B.A. p50 / max | 6.6 s / 12.8 s | 3.1 s / 7.0 s | 5.7 s / 12.8 s |
| B.S. p50 / max | 10.9 s / 23.7 s | 8.3 s / 20.0 s | 5.8 s / 12.9 s |

**Machine noise.** This machine's timings drift between runs: main measured 23.7 s for a plan that takes 9.9 s back to back. So speed was also measured **interleaved A/B**: main and this branch alternate on the six slowest B.S. cases, 3 runs each, medians shown.

| case | main | this branch |
|---|---|---|
| first-year, Light | 9.9 s | 8.1 s |
| first-year, Balanced level | 6.9 s | 8.3 s |
| first-year, Challenging | 5.2 s | 7.1 s |
| nothing yet, balanced 15/18 | 9.1 s | 8.8 s |
| nothing yet, Challenging | 7.9 s | 8.0 s |
| second-year, Semi-rigorous | 4.5 s | 3.6 s |

All are under 10 s. Within one plan_quality run, B.S. max is exactly 20.0 s with the spec order and 12.9 s with the alternative order.

## Your plan under three profiles (private, not committed)

`C:\coursepilot-private\owner-task2-preview.txt` (+ `owner-task2-plan-{A,B,C}.json`). All three use async fillers first and graduation Spring 2029.

Not quoted here: the preview is built from your real record. The three plans, side by side, are in the STOP report and the private file. All three cover every requirement:
- A: 3 semesters;
- B: 5 semesters;
- C: 3 semesters.

## Please decide or check

1. **Fill order (§4) vs. the major range.** The spec puts multi-requirement courses before major courses. This PR does that, keeping room for the term's major-related *minimum*. On B.S., early terms then fill with Core double-dips and push major courses later, where the graduation target forces 4–5 a term. In-range terms drop to 29%, against 50% with major-related courses first.
   - The alternative is one constant: `MULTI_BEFORE_MAJOR = False` in `questionnaire.py`.
   - It's better on every measure here (coverage 26 vs 25, in range 50% vs 29%, B.S. max 12.9 s vs 20.0 s).
   - **I recommend flipping it;** I left the spec's order as the default because it's your spec.
2. **Hard credit floor vs. a light finish.** Once a student's Core is done, keeping the minimum means extra major electives: more major-related courses than the range, and over the budget. Each is noted, with `CREDIT_FLOOR_OVER_BUDGET`. The STOP report shows where this hits your own plan.
3. **Hour mappings** (Greek 6/10/4, clubs 3/8, job bands 8/15/25, 60-hour baseline, 1 point per 3 hours) are my proposal. They're all in one table.
4. **Skipped honors = no.** That excludes courses whose SOC sections are all honors-only. Title-only honors courses stay plannable with a needs-confirmation note (see the follow-up below).
5. **Not built:**
   - "Avoid bus-heavy days": the schedule engine reports campus changes but doesn't rank on them.
   - "Never above the school's max without overload": CoursePilot doesn't model Rutgers credit-load rules. Max is capped at 24, and the disclaimer stays.
   - The fit line doesn't mention schedule-shape answers ("no Friday classes"): those shape schedules, not plans.

## Follow-up commit: review decisions

1. **`MULTI_BEFORE_MAJOR = False`.** The fill order is now:
   1. anchors;
   2. major-related courses (a major course that also fills Core keeps "counts twice");
   3. other double-counters;
   4. fillers.

   The fill-order test is updated, and a test pins the setting.
2. **No stranded final term.** At a workload level, the existing all-or-nothing compression now folds a last term of at most half its credit floor into earlier terms. It runs within their credit caps, and nothing else moves. Your Profile B now ends in Fall 2028 (4 terms of 12–14 credits) instead of a 4-credit Spring 2029.
   - **New message:** when commitments would push the budget below the credit floor, the plan says so once (`COMMITMENTS_VS_CREDIT_MINIMUM`): "Light + your commitments don't fit a 12-credit minimum; consider lowering commitments, the minimum, or adding a term."
3. **Honors.**
   - **Hard block:** only when SOC honors restriction data exists.
   - **Title-only matches** (e.g. 01:160:163/164 "HONORS GENERAL CHEM", 01:198:495/496): stay plannable, with the note "Needs confirmation: may require an honors program or permission." and `HONORS_TITLE_NEEDS_CONFIRMATION`.
   - **Tests:** a new test covers this. Injection fault L now removes that flag.
4. **`docs/NEXT_TASKS.md`:**
   - a new task: spread plans to the graduation target unless pace = Early, plus the cited SAS 120-credit total with a cumulative credit count per plan;
   - backlog entries for bus-heavy days and overload credit rules.

**Tests**, run once at the end on the final code (affected tests only while working):

- backend: **1,191 passed**, 4 skipped;
- ingestion: **1,108 passed**, 1 skipped;
- frontend: **107 passed**, `tsc` clean.

**Not re-run:** plan_quality and failure injection, per your instructions. Fault L's pattern was updated to the new code but not executed.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
