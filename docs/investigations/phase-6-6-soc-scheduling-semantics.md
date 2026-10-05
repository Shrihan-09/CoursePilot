# Phase 6.6 investigation: what Rutgers SOC section data means for scheduling

Evidence: the five archived New Brunswick SOC `courses.json` payloads in
`data/raw/` (Fall 2025 `20259`, Spring 2026 `20261`, Summer 2026 `20267`,
Fall 2026 `20269`, Winter 2027 `20270`, partial), the database loaded from
them, and probe scripts run against both. Counts are Fall 2026 unless stated.

## 1. What a section is

A section is one **registration index** (`index`, 5 digits, unique per term)
with a section number, campus, open/closed flag, notes, instructors,
restriction lists and an array of `meetingTimes`. CoursePilot stores it as
`course_section` (key `term_code, index_number`) under `course_offering`
(course + term + campus).

## 2. One index, several meetings

Yes. 7,965 sections have 1 meeting row, 2,661 have 2, 1,302 have 3, 56 have
4, 8 have 5. Selecting an index selects **every** one of them.

## 3. Lecture / recitation / lab

**Within one index** in the common case: 1,024 Fall 2026 sections carry both
`LEC` and `RECIT` rows. Example, 01:750:203 section 01 (index 13386):

    T 10:35-11:30 LEC   F 10:35-11:30 LEC   M 08:30-09:50 RECIT

The lecture rows repeat in every section; the recitation differs. There is
no separate recitation index to pick.

**Contradiction with the brief's working assumption (one index = one complete
registration):** Physics 01:750:193 and 01:750:194 publish TWO course records
with the same course string:

| record | supplement | credits | sections | note |
|---|---|---|---|---|
| PHYSICS FOR SCIENCES | (none) | 4 | 9 (lecture + recitation) | "MUST REGISTER FOR BOTH REC AND LAB SECTION IN SAME SEMESTER" |
| PHYSICS FOR SCI LAB | `LB` | 0 | 9 (lab only, e.g. W 15:50-18:50) | "MUST REGISTER FOR BOTH A REC & A LAB TOGETHER" |

So a student needs **two indexes**. SOC links no particular recitation to a
particular lab - any pairing is allowed by the published data. Across all of
Fall 2026, only these two course strings have a 0-credit supplement record
with such a note; the other 9 duplicated course strings are NB/OB campus
copies (16:400:513) or graduate duplicates with credits. 01:830:302 (5
sections: 3 LAB-only, 2 LEC-only) has no "both" note and is a single
registration per its notes ("CR GIVEN FOR ONLY 1").

## 4-6. Linked components

* One index fully determines its meetings: **yes**.
* Linkage between records is **not structural** in a single field; it is the
  combination of (same course string, non-empty supplement, 0 credits) and
  an explicit note. Phase 6.6 bundles a companion only when both hold, and
  reports any other supplement record as `LINKED_COMPONENT_UNVERIFIED`.
* Courses requiring multiple indexes: 01:750:193 and 01:750:194 in Fall 2026.

## 7-8. Asynchronous, arranged, TBA

36.8% of meeting rows have no day and no time. By `meetingModeDesc` (Fall
2026): `ONLINE INSTRUCTION(INTERNET)` 1,664, `RSCH-MA` 1,750, `PROJ-IND`
1,425, `MUS-INDV` 522, `GRADUATE 800-LEVEL` 383, `LEC` 147, `MUS-GRP` 146,
`CLINIC` 94, `INTERNSP` 88, `HONORS` 61. Classification:

| row | kind | free time? |
|---|---|---|
| no time, `ONLINE INSTRUCTION(INTERNET)` (campus `ONL`/`**`, no room) | asynchronous | yes |
| no time, class mode (`LEC`, `RECIT`, `LAB`, ...) - e.g. 01:750:203 sections 21-22 "THIS SECTION HAS ONLINE LECTURES" | TBA | **unknown** |
| no time, other modes (research, independent study, internship, lessons) - e.g. 01:198:493 | arranged | **unknown** |
| a published time that is not an interval | malformed | **unknown** |

Malformed rows are real: 07:966:333 index 15813 publishes T and F
`2330-1250` (its 12-hour fields say 11:30-12:50 PM); 07:966:123 publishes S
`1100-1100`. Hybrid sections combine timed `LEC` rows with an asynchronous
online row (01:013:140: M/W 17:40-19:00 + online).

## 9. Cross-listing

`crossListedSections` lists partner indexes. 01:013:120 index 10052 and
01:074:120 index 10053 each list the other and have identical meetings
(T/H 15:50): one class under two codes. 827 Fall 2026 sections carry 966
references (36 point to other campuses). The scheduler never chooses an
index together with its partner, and warns when two requested courses are
cross-listed. Whether both may count for credit is a degree question it
does not answer.

## 10-11. Restrictions: persisted vs dropped

Persisted before 6.6: `openToText` (prose), `sectionEligibility` (prose,
e.g. "JUNIORS AND SENIORS"), special-permission add/drop codes.
**Dropped** by Phase 3 ingestion: the structured lists `majors` (2,623
sections), `unitMajors` (1,071), `minors` (248), `honorPrograms` (141).
Shapes:

    majors:      {"code": "198", "isMajorCode": true}   or   {"code": "14", "isUnitCode": true}
    unitMajors:  {"unitCode": "01", "majorCode": "202"}
    minors:      {"code": "014"}
    honorPrograms: {"code": "A"}

`openToText` renders them as one "open to" list:
"UNIT/MAJOR: 01/202 (School of Arts and Sciences / MAJ: Criminal Justice);
MAJ: 202 (Criminal Justice); MINOR: 204 (Criminology)" - read as: any
listed attribute admits the student.

## 12. What safe scheduling needs that was missing

1. The structured restriction lists - prose cannot be evaluated.
2. `sessionDates` - see 13.

Both are now stored (migration `87bec76b788c`, table `section_restriction`,
columns `course_section.session_dates_raw/_start_date/_end_date`), backfilled
idempotently from the archived payloads: 19,033 restriction rows; 1,698
Summer sections with dates; no other row changed.

Still NOT available, and therefore never claimed: the student's minors,
second majors, honors membership and class standing (restrictions on those
are UNKNOWN); seat counts or waitlists (SOC has none); final-exam conflicts
(only `examCode` text).

## 13. Session dates

`sessionDates` is empty on every Fall and Spring section and present on
**all 1,698** Summer 2026 sections, in one shape: `"05/26/2026 -
07/02/2026"` (10 distinct ranges, e.g. May 26-Jul 2: 500 sections; Jul 6-Aug
12: 597). The Phase 3 note "empty on 100%" was measured on Fall only.
Real consequence: 01:014:386 (Jul 6-31) and 01:202:201 (May 26-Jul 2) both
meet M/W 18:00-22:00 and do **not** conflict.

## 14-15. Open/closed

`openStatus` is a boolean per section in each download - no seats, no
waitlist. CoursePilot holds it per term as of the download time
(`data_source.retrieved_at`), i.e. **archived snapshots**. No live
availability source exists yet (the `openSections.json` poller is a later
phase). The database can tell a structurally schedulable section (meetings,
restriction) from one that was open in a snapshot; it cannot tell whether a
section is registerable now.

## Real examples used by the tests

`ingestion/tests/fixtures/soc_schedule_fall2026_sample.json` and
`soc_schedule_summer2026_sample.json` (built by
`scripts/make_schedule_fixture.py`, whole unmodified records): 01:198:344
(open to major 198), 01:694:383 (open to major 694, no prerequisite),
01:090:120 (unit 01), 01:750:193/203, 01:830:302, 01:013:120 / 01:074:120,
01:013:143 (asynchronous), 01:013:321 (TBA lecture), 01:198:493 (arranged),
07:966:333 (malformed), 01:070:105 (two instructors), 01:090:182 (Saturday),
Summer 01:014:386 / 01:202:201 (disjoint) and 01:014:490 / 01:202:305
(same session, same time).
