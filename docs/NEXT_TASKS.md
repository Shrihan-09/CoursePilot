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
