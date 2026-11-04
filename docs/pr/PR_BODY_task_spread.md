# Spread plans to the graduation target; model the SAS 120-credit total

Branch: `feature/spread-to-graduation-target`. This is the "Spread plans to the graduation target, and model the 120-credit degree total" task from `docs/NEXT_TASKS.md`. The full design is in `docs/DATA_MODEL.md` section 55.

## What changed

### 1. The degree credit total is a requirement

- **Source:** the 2026-2027 Rutgers catalog, "Requirements (SAS)", tab "Credits and Residency": https://newbrunswick-26-27-undergrad.catalogs.rutgers.edu/pages/xv72Yj9vsrh3FCdUV1kI
  > "All students must earn at least 120 credits, with a minimum cumulative grade-point average of 2.000."
- **Provenance:** retrieved 2026-11-03 and archived locally at `data/raw/catalog/catalog_sas-requirements_2026_2027.html` (sha256 `981a914cebc86bce8dc723aa349acbf6f8a6b30852c2ae525ab10d5c94da3a81`). `data/raw/` is not committed.
- **Code:** `backend/app/services/planning/degree_total.py` holds the citation, keyed by (school, catalog year). Only SAS 2026-2027 is read.
- **Other programs:** any other school or catalog year (SAS 2025-2026 included) gets no assumed total. Its plan carries the assumption `degree_credit_total_not_modelled` instead.
- **Cumulative credits** add up:
  - the record: the Degree Engine's credits applicable to the degree (a repeat counts once), plus in-progress credits;
  - planned courses, except a repeat of a passed course;
  - planned free-elective credits.
- **Where it shows:**
  - `PlanTerm.cumulative_credits` is the running total after each term;
  - `PlanResult.degree_credits` gives the parts, the total, whether it is met, and the citation;
  - the requirement also appears in `requirements` as `DEGREE_CREDITS`.
- **Status:** `covers_all_requirements` now needs the total too. A plan short of 120 is `partial`, with `DEGREE_CREDITS_SHORT`.

### 2. Free electives

The credits a plan still needs toward the total are free-elective credits: any degree-credit courses the student chooses. They are stored as `PlanTerm.free_elective_credits`. CoursePilot never invents a course for them, and they carry no workload weight.

1. Every term with courses is topped up to its credit floor.
2. Unless pace = Early, the rest spreads over the terms up to the target. An empty term gets its floor while the total still needs a whole floor. After that, the lowest terms rise one credit at a time, never past a maximum.
3. A plan that needs fewer terms ends sooner instead of leaving a last term below its floor.
4. Pace = Early fills each term toward its maximum.

Plans without a workload level have no floor. There, electives go only where the total needs them.

### 3. Spreading at a workload level

This applies with a workload level and a graduation target, unless pace = Early.

- **Major-related cap:** a term takes no more major-related courses than its range's minimum, or than what the target needs from that term on, whichever is more.
- **Credit floor:** it is reached with free-elective credits, not with major courses past the range or over the budget. The Task 2 floor relaxation still applies with pace = Early, or when no total is modelled.
- **Fallback:** if spreading would miss the graduation target (a prerequisite chain starts too late), the plan is rebuilt with the Task 2 behaviour. It carries `SPREAD_WOULD_MISS_TARGET`, so graduation is never silently delayed.

### 4. Two counting fixes: a lab and its lecture are one major-related course

1. **Placing a term.** The engine counted a lab placed after its lecture (Gen Physics 205 after 203) as a second major-related course. That filled the term's cap early. The term's summary already counted them once.
2. **The graduation-target need.** The per-term need counted lab and lecture as two open units. The B.S. physics sequence is four requirement leaves but two courses, so the fixture student needed "4 a semester" instead of 3.

Both now count co-requisite partners once, as documented. With the first fix alone, the Spring 2027 scenario took a fourth major course because of the overstated need. With both fixes it is again Calc II + Gen Physics 203/205 + one CS course + an async filler: 3 major-related, 16-19 credits. Those scenario tests pass unchanged.

### 5. Frontend

- Each term shows "N credits + M free-elective credits · running total T", with a dashed "Free electives" card.
- The plan summary shows "Degree credits: X of 120 (record + planned courses + free electives)" with a link to the source.
- The plan status wording now mentions the credit total.

## Tests

New tests:

- `backend/tests/test_degree_total.py` (9 tests): the citation, no total assumed for an unread school or year, and the spreading rules (floor first, even spread, no stranded last term, short only at the caps, Early in order).
- `ingestion/tests/test_workload_levels.py`:
  - a later target spreads the plan within the credit and major ranges and reaches 120;
  - the running total adds each term's courses and electives;
  - a plan short of 120 credits is `partial`, with `DEGREE_CREDITS_SHORT`;
  - pace = Early finishes in fewer terms than the spread plan. This uses a 12-19 credit range and a Spring 2031 target, so Early has real room.

Updated tests:

- **Term credit checks:** these count free-elective credits (`_credits(term)`).
- **The 19-credit floor-relaxation test:** it now runs with pace = Early, the only case where that relaxation still applies.
- **Three legacy pace/engine tests:** they used a fabricated record that cannot reach 120 credits. They now assert that every modelled requirement is covered (`_requirements_covered`), not the overall status.
- **Version pins:** updated to 7.8.0.

Suites, run once on the final code against the test database:

| Suite | Result |
|---|---|
| backend | **1,200 passed**, 4 skipped |
| ingestion | **1,112 passed**, 1 skipped |
| frontend | **107 passed**, `tsc` clean |

Failure injection and plan_quality were not run for this task.

## Preview

A private 3-profile preview of the owner's plan is in `C:\coursepilot-private\owner-spread-preview.txt`. It is not quoted here. In it:

- the Semi-rigorous and Challenging profiles reach 120 within the target, every term in range;
- the Light + commitments profile needs the fallback (`SPREAD_WOULD_MISS_TARGET`).

## Migrations

None. The new fields live in the plan payload only (`PlanTerm`, `PlanResult`).

## For you to decide or check

1. **Ending before the target.** A spread plan still ends before the target when 120 is reached with every term at its floor. A later term would fall below the floor, so the rule prefers finishing sooner.
2. **Free-elective credits are exact.** A term can show "1 free-elective credit". Rounding to 3-credit courses is not done.
3. **Saved plans.** These will read as stale ("engine updated"), since the planning engine version moves from 7.7.0 to 7.8.0.
4. **Not modelled yet** (recorded in `docs/NEXT_TASKS.md`):
   - courses that carry no degree credit;
   - totals for other schools and catalog years;
   - the residency rule (30 of the last 42 credits).

## Data and backfill

None.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
