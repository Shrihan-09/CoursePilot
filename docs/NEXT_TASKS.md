# Next tasks (backlog)

Recorded work that is decided but not yet built. Each entry says what is
wrong today, what the published source says, and what done looks like.

## Section-level prerequisite notes that narrow the published prerequisite

Found in the Task 1 science-sequence audit (2026-10-27). CoursePilot reads a
course-level OR-list note that narrows `preReqNotes` (parser 4,
`prerequisite_lists`; e.g. 01:160:171). It does **not** read the same kind of
statement when Rutgers prints it in a SECTION note, so these courses are
currently judged on the broader published prerequisite:

| Course | Published prerequisite (`preReqNotes`) | Section note (Spring 2027 SOC) | Effect today |
|---|---|---|---|
| 01:160:160 | 01:160:159 or 161 or 163 or 165 | "PREREQ: 160:159 OR 161" | 163 and 165 are accepted although the sections name only 159/161 - eligibility too broad |
| 01:160:162 | 01:160:159/161/163/165, 21:160:115, 50:160:115 | "GRADE OF C OR BETTER IN 160:161" | the C minimum on 160:161 is not applied - a D in 161 counts |

Done means:

- section-level narrowing OR-lists are enforced like course-level ones, only
  when every course they name is inside the published prerequisite;
- a section-level minimum grade ("C OR BETTER IN <course>") is applied with
  the existing `named` minimum-grade scope;
- a note published on only SOME sections is handled the way corequisites are:
  the course is NEEDS CONFIRMATION, never silently satisfied;
- `scripts/reparse_conditions.py` reports the semantic changes (dry run first,
  backup before applying), and tests cover both courses.

## Enforce prerequisites published only in section notes

Found in the Task 1 reparse (2026-10-27). 37 course-term rows (10
distinct courses) have NO published prerequisite expression (`preReqNotes` is
empty) and a section note that is a plain list of prerequisite courses - the
note is the only prerequisite Rutgers states. Parser 4 leaves these
uninterpreted (the course is capped at UNKNOWN / needs confirmation), because
the narrowing rule applies only inside a published prerequisite. Examples:
11:067:322 ("11:067:327"), 16:700:519 ("16:700:501"), 19:912:543 ("19:912:500");
every row is in `docs/investigations/evidence/task1-reparse-narrowings.json`
(`status: reverted`, empty `published`).

Done means:

- such a note is enforced AS the prerequisite, labelled
  **"from section note (Rutgers SOC)"** wherever the prerequisite is shown
  (trace, eligibility evidence), so it is never mistaken for `preReqNotes`;
- the same parse and unknown rules as every other condition: only a list of
  fully resolved course codes is interpreted; anything else (permission,
  "or equivalent", unresolved codes, a note on only SOME sections) stays
  UNKNOWN / needs confirmation, never satisfied;
- `scripts/reparse_conditions.py` dry run first, backup before applying, and
  a report of every changed row; tests for an enforced note, a note on some
  sections only, and an uninterpretable note.

## Done: spread to the graduation target; the 120-credit degree total

Built on `feature/spread-to-graduation-target` (DATA_MODEL.md section 55).
Follow-ups it leaves:

- **Degree credits per course.** Record credits come from the Degree
  Engine's "credits applicable to the degree". Courses that carry no degree
  credit (the catalog names "noncredit remedial courses") are not yet marked,
  so their credits would count. Done means a cited per-course
  "no degree credit" flag, used by the audit and the total.
- **Other schools and catalog years.** Only SAS 2026-2027 is read. Other
  plans say no total is modelled. Done means each total cited the same way
  (SAS 2025-2026, SOE, SEBS, ...).
- **Residency (30 of the last 42 credits at Rutgers-New Brunswick).** It is
  on the same catalog tab and is not modelled.

## Backlog: schedule and credit-load rules (from Prompt 09)

- **Bus-heavy days.** Prompt 09's "avoid bus-heavy days" is not modelled. The
  Schedule Engine reports campus changes between consecutive classes
  (`CAMPUS_CHANGE_BETWEEN_CLASSES`) but neither ranks nor limits them. Done
  means a soft preference (fewer campus changes per day) in the option
  ranking, with the change count shown.
- **Overload credit rules.** "Never above the school's max without an
  overload" is not modelled: CoursePilot caps credits at 24 and shows a
  disclaimer. Done means the school's published credit-load and overload
  rules (cited), a plan that asks before planning above the limit, and a
  warning that names the rule.


## Backlog: catalog year notice (from Task 3)

The owner's stored catalog year is 2026-2027. Their first Rutgers term is
Fall 2025, though, so their real catalog year is likely 2025-2026, and
CoursePilot only has the 2026-2027 catalog. Not built.

Done means: when a student's entry term implies an earlier catalog year than
the one CoursePilot plans against, the plan and the Degree page show a notice:
"planned against the 2026-27 catalog". The notice names both years and never
changes the student's catalog year by itself.

## Backlog: registration (from Prompt 11 follow-ups)

- **WebReg autofill helper (post-beta).** A browser extension running only on
  the student's own device. The student logs into WebReg and approves Duo
  themselves; the extension reads the student's "Ready for WebReg" index list
  from CoursePilot and fills the WebReg add form. The student clicks Submit.
  Never: storing credentials or sessions, auto-submit, auto-refresh/retry
  loops, server-side access to WebReg. Before building, check Rutgers
  acceptable-use wording on browser extensions/automation or ask the
  Registrar.
- **Show each student their registration window** from their degree credits,
  with a reminder. The Spring 2027 tiers are on file
  (`backend/app/core/data/registration_schedule_20271.json`,
  `RegistrationSchedule.tier_for_credits`); done means the Plan/Schedule page
  names the student's tier and opening time ("You register from Thu, Nov 12,
  9:00 am - 60+ degree credits"), labelled as computed from CoursePilot's
  credit count (Degree Navigator's degree credits are authoritative), plus a
  reminder before it opens.

## Backlog: program expansion (from Prompt 08 B1)

- **Verify RBS admission** from an uploaded Degree Navigator audit or transcript (the
  school code), with an admin override. Until then nothing in the app marks an
  admission verified. Admitted RBS students keep the "after admission to RBS" labels,
  and pre-business students keep SAS Core as the requirement of record (owner
  decision 2026-11-13: fine as-is for now).
- **Recheck the CS intro seasons** when the Fall 2027 SOC is published. 01:198:120/121/122
  are assumed to run every fall and spring (approved flagged assumption,
  `course-transitions.json`, SEASONS-ASSUMED).

## Backlog: tooling

- Free-elective credits: show them as a course slot (e.g. "one 3-credit
  elective") instead of odd partial counts like "1 free-elective credit".

- `inject_*_failures.py`: report INVALID (not MISSED) when pytest can't load
  the app / no tests ran. Today a collection error (e.g. the wrong Python, so
  `app` won't import) prints MISSED, which looks like a test gap.
