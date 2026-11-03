# Prompt 09: Planning Interview, Commitments and Workload-Aware Plans

**Read first:**
- `docs/PHASE_7_3_PLAN.md`;
- `backend/app/domain/planning_profile.py`;
- `backend/app/services/planning/`;
- `backend/app/services/scheduling/`;
- `frontend/src/features/onboarding/`;
- `frontend/src/features/planning/preferences-form.tsx`;
- `docs/DATA_MODEL.md` §40, §49.

## Goal

Every student's plan should fit **their** situation. That means:
- what they still need;
- how hard they want each semester to be;
- what else they have going on (job, Greek life recruitment, clubs, sports, recruiting season).

The aim is a manageable semester that protects their GPA. The app learns this from a **short, simple questionnaire**. Answers map **deterministically** to planner settings; no LLM decides anything. The plan explains how each answer shaped it.

Never promise a GPA outcome. The wording is "keeps your workload manageable", not "won't lower your GPA".

## 1. Bugs to fix first (from the dev student's plan)

1. **Credit minimum is a hard floor.** Min 16 / max 19 produced 14-credit terms. Treat the minimum like the max. If a term truly can't reach it, place what's possible and show the reason on that term. Never fall short silently.
2. **Restricted courses.** 01:198:495/496 (Honors Capstone) were recommended to a non-honors student.
   - Use SOC restrictions, notes and the catalog for honors-only or admission-required courses.
   - Never place one unless the student's profile meets the restriction. Add an "Are you in an honors program?" question (§2).
   - Add tests.
3. **Major exclusions.** Courses a major gives no credit for, e.g. CS majors and 01:198:142, must never count as major courses. Warn the student if they pin one.

## 2. The planning questionnaire

A short Q&A of **8–12 questions**, multiple choice wherever possible. Every question is skippable with a sensible default. It runs once in onboarding and is editable any time from the Plan page. Answers can differ **per semester**, because commitments change.

| Topic | Example question | Answer type |
|---|---|---|
| Rigor | "How intense should this semester be?" | Light / Balanced / Semi-rigorous / Challenging |
| Credits | "How many credits?" | min / max (default 15–18, never above the school's max without overload) |
| Major focus | "How many major courses per semester?" | 1 / 2–3 (default) / 3–4 |
| Retakes & musts | "Any course you need to retake or must take this term?" | course picker (pins) |
| Work | "Do you have a job?" | none / <10 / 10–20 / 20+ hrs/week + optional fixed shifts |
| Greek life | "Rushing or pledging this semester?" | no / rushing / new member / active member |
| Clubs & orgs | "Clubs, professional orgs or leadership roles?" | none / light / heavy (+ optional fixed meeting times) |
| Athletics / other | "Sports, research, internship recruiting or other big commitments?" | multi-select + hrs/week |
| Schedule shape | "When do you NOT want class?" | before 10am / Fridays / evenings / custom blocks |
| Format | "For lighter requirement courses, do you prefer…" | flexible/async / in-person / no preference |
| Campus | "Preferred campus / avoid bus-heavy days?" | campus picker (existing CSP-style preference) |
| Honors | "Are you in an honors program?" | yes / no (gates honors-only courses) |

**Privacy:**
- Store only what planning needs: hours/week, time blocks, and the commitment type as a category.
- No free-text personal details, and no organization names.
- The student can clear their answers.

## 3. From answers to planner settings (deterministic, in one tested module)

- **Weekly hours available for school:** a baseline (e.g. 60 hrs/week), minus the student's commitments:
  - job hours;
  - club/athletics hours;
  - Greek life by stage (rushing = high in the first weeks; new member = high all term).
- **Workload budget for the term** = f(rigor level, hours available). Heavy commitments lower the budget, even at "Challenging". In that case, show the student a note like "your rush semester lowered this term's target load".
- **Credit target:** inside the student's min/max. Leaning lower when the budget is tight never breaks the hard floor; if the floor and the budget conflict, say so and let the student choose.
- **Fixed time blocks** (job shifts, club meetings, "no class before 10am") become **blocked time** in the schedule engine's section search.

**Course workload weight**, from real data only:
- credits;
- weekly contact hours from SOC meetings (lecture + lab + recitation);
- whether it has a lab;
- course level;
- whether it's a technical subject.

Show the numbers. Never call a course "easy" or "hard". The wording is "lighter / heavier workload".

## 4. How each term is filled (re-validate every step with the engine)

1. **Anchors:** required retakes (from history), then pinned courses.
2. **Multi-requirement courses:** courses that satisfy 2+ open requirements at once, within the sharing policy. Example: a science that counts for both Core and the major.
3. **Major courses:** up to the student's major-per-term setting, and only courses that actually count toward the major (respect exclusions). Prefer courses that unlock prerequisite chains, or that are rarely offered.
4. **Fillers:** fill toward the credit target with courses that complete a remaining requirement. Choose by format preference (async first when "flexible"), and by low workload weight when the budget is nearly spent.
5. **Constraints:** stay within the budget, the credit min/max and the blocked times. If the anchors alone exceed the budget, keep them and say so ("Calc II + Physics with lab make this a heavier term — consider fewer other commitments").

## 5. What the plan shows per term

- **Totals:** credits, contact hours per week, and workload against budget.
- **Role badges:** anchor / counts twice / major / filler.
- **Why this fits you:** one line tying it to the answers. For example: "Semi-rigorous + rushing this spring → 16 credits, 2 major courses, async filler, no Friday classes."
- **Shortfalls:** any constraint that couldn't be met, with the reason.

## 6. Tests

- **Answer mapping:** the answers → settings function, table-driven.
- **Per-question defaults:** defaults when a question is skipped.
- **Credit floor:** the floor is enforced, and a shortfall is explained.
- **Restriction gate:** honors-only courses need a "yes" on the honors question.
- **Exclusions:** excluded courses are never counted as major courses.
- **Blocked time:** blocked times are respected by the schedule engine.
- **Scenario tests** (frozen October 2026, Spring 2027):
  1. **Semi-rigorous CS student**, who must retake Calc II, needs a science for both Core and the major, and prefers flexible fillers. Expected: Calc II + Gen Physics with lab + one counting CS course + an async requirement course, 16–19 credits.
  2. **Light, rushing, 15 hrs/week job:** a lower budget, 2 major courses, no heavy stacking.
  3. **Challenging, no commitments:** a high load, 3–4 major courses.
- **Wording:** no "easy/hard" in API output (grep test).
- Run `scripts/plan_quality.py`. Show the dev student's plan under three questionnaire profiles, side by side.

## STOP

Report:
- the three side-by-side plans;
- what each answer changed;
- any constraint conflicts found.
