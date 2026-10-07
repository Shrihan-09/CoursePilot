# Phase 6.6.1 investigation: labs, recitations, workshops and linked components

Evidence: the archived New Brunswick SOC `courses.json` payloads in
`data/raw/` - Fall 2025 `20259`, Spring 2026 `20261`, Summer 2026 `20267`,
Fall 2026 `20269`, Winter 2027 `20270` (partial) - plus the Phase 6.4
`course_corequisite` rows loaded from them. Probes: every course/section note
in every term was scanned for co-requisite markers (including misspellings),
"X IS A CO-REQUISITE", registration-link phrasing, section-pairing phrasing,
"OPTIONAL", time ranges in prose, and same-course-string supplement records.

## Three relationships, three owners

| relationship | Rutgers form | owner |
|---|---|---|
| MEETING - "index A has lecture + recitation meetings" | several `meetingTimes` rows under one `index` | section meeting model (Phase 3/6.6) |
| REGISTRATION - "course X requires indexes A + B" | a second course record with the SAME course string, a supplement code, 0 credits, and a "register for both" note | Schedule Engine bundle |
| ACADEMIC - "course X requires course Y concurrently" | a co-requisite clause naming another course | Phase 6.4 eligibility (`check_proposal`) |

## Patterns found (letters follow the Phase 6.6.1 brief)

### A. Lecture + recitation under ONE index - very common, handled

01:160:161 GENERAL CHEMISTRY, 20259, index 09171 (section 01), 4 credits:
`M/W/F 14:15-15:10 LEC LIV` + `M 21:35-22:30 RECIT **`. Same shape:
01:160:162, 01:160:307/308 ORGANIC CHEMISTRY (20259 index 24463: M/W
15:50-17:10 LEC + W 08:45-09:40 RECIT), 01:750:203/227 physics (20269 01:750:203
index 13386: T/F 10:35-11:30 LEC + M 08:30-09:50 RECIT). 1,024 Fall 2026
sections carry LEC+RECIT under one index. One registration; every row is
checked (Phase 6.6). Not a second registration requirement.

### B. Lecture + lab under ONE index - handled

01:119:117 BIO RESEARCH LAB (2 credits), 20259 index 09037: `W 10:20-11:40 LEC`
+ `M 13:10-17:10 LAB`. 01:160:311 ORGANIC CHEM LAB (2 credits), 20259 index
09329: `T 08:45-09:40 LEC` + `M 13:00-17:00 LAB`. One registration.

### H. Workshop under ONE index - handled; prose can disagree (see J2)

01:119:115 GENERAL BIOLOGY I (4 credits), 20259 index 08952: `M/W 17:40-19:00
LEC` + `T 08:30-09:50 WORKSHOP`; all 72 sections are LEC+WORKSHOP. 01:119:116
the same.

### C / F. Separate 0-credit lab record - a registration component

| term | base record (credits) | companion | note | evidence location |
|---|---|---|---|---|
| 20259, 20261, 20269 | 01:750:193 PHYSICS FOR SCIENCES (4) | `LB` PHYSICS FOR SCI LAB (0), 8-9 sections | "MUST REGISTER FOR BOTH A REC & A LAB TOGETHER" | companion sections |
| 20261, 20269 | 01:750:194 PHYSICS FOR SCIENCES (4) | `LB` (0) | 20269: "PREREQ: 750:193 MUST REGISTER BOTH LEC/REC & LAB [FOR ALL SECTIONS]" | base AND companion |
| 20259 | 01:750:202 EXTENDED GEN PHYSICS (5) | `LB` (0), 6 sections | "MUST REGISTER FOR BOTH REC AND LAB SECTION IN SAME SEMESTER" (base index 11793); "MUST REGISTER FOR BOTH A REC AND A LAB SECTION" (LB index 11791) | both records, one section each |

The companion's sections are lab-only (20269 01:750:193 LB index 13361: `W
15:50-18:50 LAB`). SOC links no particular recitation to a particular lab.

**Gap found:** Phase 6.6 recognized a companion only from the COMPANION'S own
notes and only the wording "REGISTER FOR BOTH" / "BOTH A REC" / "TOGETHER".
01:750:194 in 20269 ("MUST REGISTER BOTH LEC/REC & LAB" - no "FOR") was
therefore reported `LINKED_COMPONENT_UNVERIFIED` and scheduled WITHOUT the
lab - flagged, not silent, but incomplete. (01:750:202 was already handled:
its LB index 11791 carries the note. An earlier draft of this document said
otherwise after reading only three sections; corrected.) Phase 6.6.1 also
reads the base record's notes; every observed companion states the
requirement on both records, so this is symmetry, not new evidence.

**Second gap, found while testing:** the LB record is a second course row
with the same course string, and Phase 6.4 eligibility gathered prerequisite
rows by course string. 01:750:194's LB prerequisite note differs by "FOR ALL
SECTIONS", so the two rows "differed" and every student got UNKNOWN
(`campus_prerequisites_differ`) - the Planning Engine never placed 01:750:194.
Eligibility now reads the BASE record's rows when a course has one. The
note's "MUST REGISTER BOTH LEC/REC & LAB" tail - the registration bundle,
enforced by the Schedule Engine - is now registration logistics in the
condition reader, not an uninterpreted condition. Spring 2026's "750:193 AND
MUST REGISTER BOTH ..." keeps "AND" and stays uninterpreted (the reader never
drops a clause joined by AND).

The only other supplement record in the archive, 77:705:455 `BW` (20261,
4 credits, same title), is a positive-credit variant with no "both" note: never
bundled (`LINKED_COMPONENT_UNVERIFIED`).

### D + E. Separate positive-credit lab COURSE with an academic co-requisite

| course | credits | co-requisite as published | Phase 6.4 before 6.6.1 |
|---|---|---|---|
| 01:750:205 GENERAL PHYSICS LAB | 1 | "01:750:203 IS A CO-REQUISITE" (20259/20269, every section); "CO-REQ: 750:203" on 17 of 18 sections (20261) | `unsupported` (UNKNOWN) in 20259/20269; NOTHING stored in 20261 |
| 01:750:206 GENERAL PHYSICS LAB | 1 | "01:750:204 IS A CO-REQUISITE"; "COREQ: 750:204" on 30 of 32 sections (20261) | `unsupported`; nothing in 20261 |
| 01:750:229 ANAL PHYS II LAB | 1 | "01:750:227 IS A **CO-REQUSITE**" (Rutgers' spelling) on 26 of 28 sections (20259) | **nothing stored - eligibility said SATISFIED** |
| 01:750:230 ANAL PHYS II LAB | 1 | "COREQ: 750:228" | `parsed` (20261) |
| 01:119:117 BIO RESEARCH LAB | 2 | "PRE-REQ: 119:115 CO-REQ: 119:116" on all 35 sections (20261), on 2 of 25 (20259), 2 of 27 (20269) | parsed in 20261; **nothing stored in 20259/20269** |
| 14:332:223 / 224 / 233 / 363 / 368 ECE labs | 1 | "COREQ 14:332:221" etc. on some sections | **nothing stored** |
| 01:160:171 INTR EXPERIMENTATION | 1 | prerequisite only - no co-requisite published | correct (no rule) |
| 01:160:311 ORGANIC CHEM LAB | 2 | prerequisite only | correct (no rule) |

These are separate ACADEMIC courses with their own credits: the Planning
Engine plans them as courses, and the co-requisite belongs to Phase 6.4.

**Gap found (the most important one - neither engine caught it):** Phase 6.4
lifted a section-note co-requisite to the course only when EVERY section
published the same rule. **60 offerings** across the five terms publish a
co-requisite on SOME sections only; for all of them nothing was stored, so
`check_proposal` reported SATISFIED for a student without the co-requisite.
Two more silent forms: the misspelling "CO-REQUSITE" (72 section notes) never
matched the marker, and "X IS A CO-REQUISITE" (179 notes) was not
interpreted.

### F (cross-course). Registration statements naming another course

* 01:617:201 (20259, BTAA courses): "STUDENTS MU ST ALSO REGISTER FOR
  01:078:117" and "MUST ALSO REG ISTER FOR 01:013:252 OR 01:563:1 31" (wrap
  damage is Rutgers').
* 03:691:101 / 201 / 391 / 491 Army ROTC (20269): "MUST REGISTER FOR LAB
  03:691:103 F 8:00AM - 1:00PM"; 03:691:103: "MUST REGISTER FOR LECTURE
  03:691:101"; one copy reads "03:61:103".
* 01:160:161 Summer 2026: "STUDENTS AUTO-REGISTERED FOR 01:160:101:E1
  (RECITATION)" - the recitation is a different course, registered by Rutgers.

Before 6.6.1: none produced a rule or a flag.

### G. Specific section pairings

No machine-readable pairing exists anywhere in the archive. The only
pairings are prose, e.g. 01:160:308 Summer 2026 index 00557: "STUDENTS ALSO
REGISTERED FOR LAB SECTION 01:160:314:H1 OR H2 MUST TAKE THIS SECTION OF
LECTURE" - and 01:160:314 in that term has sections R1-R4, no H1/H2. The
01:119:115 Summer workshops say "SEE SECTION B1". CoursePilot therefore never
invents compatibility; it flags a chosen section whose note names another
requested course (`SECTION_NOTE_REFERENCES_REQUESTED_COURSE`).

### I. Optional components

"OPTIONAL" appears 30 times, only for field trips, Zoom sessions, ROTC
tracks and in-person dates - never for a lab or recitation. No optional lab
component exists in the data. The required-companion rule needs an explicit
"both" statement, so nothing is forced.

### J. Components in prose only

J1 - co-requisites and registration links: above.

J2 - **meeting times in prose that the structured meetings lack or
contradict** (not found in Phase 6.6): 01:160:308 Summer 2026 index 00557 has
only LEC rows; its note says "RECIT: TWH 8:00-8:50AM". 01:119:115 Summer 2026
index 00350 has `W 09:30-10:50 WORKSHOP` while its note says "WORKSHOP:MW
9:30AM-10:50". Time ranges in section notes: 91 (20259), 86 (20261), 41
(20267), 145 (20269) sections - mostly Nursing (77:705), ROTC, Summer
Biology/Chemistry and BTAA online courses. A schedule cannot be called
conflict-free for these sections.

### K. Structured fields

Only: several `meetingTimes` rows (A/B/H), `supplementCode` + `credits`
(C), `crossListedSections` (L), `sessionDates` (N), restriction lists (O).
There is no SOC field that links one index to another index, or one course
to another.

### L. Cross-listing with components

01:160:314 Summer 2026 lab sections are cross-listed in pairs (index 00578 <->
00579, 00580 <-> 07836). Cross-listing rules (Phase 6.6) apply to every
component index.

### M. TBA / asynchronous component meetings

01:750:204 (20259) index 11837: `LEC` with no time (online lectures) + `T
08:30-09:50 RECIT`. 01:830:302: LAB rows + an asynchronous online row.

### N. Separate session dates

01:160:308 Summer 2026: index 00555 May 26 - Jul 2, indexes 00557/00558 Jul 6 -
Aug 12; 01:160:314 labs Jul 6 - Aug 12. Date-aware conflicts apply per index.

### O. Restrictions differing between components

Within one course, sections differ (01:160:308 20261 index 11028: "MAJ: 115,
125, 155, 160, 694" while others are open). No companion (LB) section in the
archive has a restriction its base lacks; every component's restriction is
evaluated anyway.

## Planning Engine finding

A co-requisite partner that serves no requirement (01:750:227 for 01:750:229)
crashed the Phase 6.5 planner (`KeyError` ranking the partner). Phase 6.5's
test partner (01:750:203 for 01:750:205) happened to be a candidate. Fixed;
covered by `test_planner_plans_courses_and_the_scheduler_adds_the_lab`.

## Summary: what was missed before 6.6.1

| pattern | before | silent? |
|---|---|---|
| co-requisite on SOME sections (60 offerings) | not stored, eligibility SATISFIED | **yes** |
| "CO-REQUSITE" spelling | not matched | **yes** |
| "X IS A CO-REQUISITE" | unsupported (UNKNOWN) | no |
| "MUST [ALSO] REGISTER FOR [LAB] <course>" | not read | **yes** |
| "MUST REGISTER BOTH LEC/REC & LAB" wording (01:750:194) | LINKED_COMPONENT_UNVERIFIED, lab omitted | no, but incomplete |
| LB record's prerequisite row differs from the base's | course UNKNOWN for everyone, never planned | no, but wrong |
| meeting times only in prose | treated as verified | **yes** |
| prose pairing with another course's section | not read | **yes** |
