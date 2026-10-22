# Phase 7.3 investigation: Course Schedule Planner and Degree Navigator

Method: manual, developer-operated browsing in headed Chrome
(`scripts/discover_csp.py`, `scripts/discover_dn.py`). Sign-in is never
recorded or automated; recording starts only inside the application. HARs
live outside the repository (`COURSEPILOT_PRIVATE_DIR`). Everything below
was measured from those captures with `scripts/analyze_har.py` and
field-level comparisons; no cookie, token or response body is reproduced.

## 1. Course Schedule Planner (CSP) - captured 2026-10-22 (Spring 2027)

### Endpoints

| Endpoint | Method | Returns | Size | Auth |
|---|---|---|---|---|
| `/csp/` | GET | HTML login shell ("Login - Course Schedule Planner") | 7.6 KB | none |
| `/csp/dashboard.htm` | GET | HTML shell | 7.5 KB | NetID session |
| `/csp/builder.htm?semester=` | GET | HTML shell for the schedule builder | 13.8 KB | NetID session |
| `/csp/getSubjects.json` | POST | JSON object: the subject list for a term/campus/level | 8 KB | NetID session |
| `/csp/sectionsLookup.json` | POST | JSON `{courseOfferings: [...]}` - **every section of one subject** | 57-418 KB | NetID session |

Request parameters (POST form): `semester`, `campus`, `levelOfStudy`, and
`subject` for `sectionsLookup`. The pages are Dojo 1.10 shells; all section
data arrives through `sectionsLookup.json`, one request per subject.

### Response shape (`sectionsLookup.json`)

```
courseOfferings[]: offeringUnit{code} course{subject, number} supplementCode
                   campusCode title fullTitle credits coreCodes synopsisUrl
                   courseNotes unitNotes subjectNotes subjectGroupNotes
                   prereqNotes courseFee
  sections[]: yearterm registrationIndex sectionNumber open examCode
              sectionCourseType sessionDates finalExam legendKey
              sectionNotes subtitleText subtopicText sectionEligibility
              specialPermissionAdd comments[] crossListedSections[] majors[]
              instructors[]{name}
              meetingTimes[]{meetingDay startMinute endMinute meetingModeCode
                             meetingModeDesc campusAbbrev campusName
                             campusLocation buildingCode buildingNum
                             roomNumber baClassHours}
```

### CSP vs SOC parity

Compared: **every section in the capture - 962 Spring 2027 sections in five
subjects (070, 198, 220, 640, 750)** - against SOC `courses.json`
(year=2027, term=1, campus=NB, fetched the same day), matched on
`(term, index)`. 25 of the 31 named sample sections
(`phase-7-3-parity-sample.md`) were in the capture; the 6 Chemistry (160)
samples were not, because 160 was not opened.

- Every CSP index exists in SOC; CSP had **no section SOC lacks**.
- After normalising representation, **0 semantic disagreements** on course
  string, section number, title, credits, instructors, cross-listings,
  eligibility, special permission, exam code, session dates, meetings (day,
  start, end, mode, campus, building, room) and open/closed.
- Representation differences (not disagreements), which a CSP adapter must
  normalise:

| Field | CSP | SOC |
|---|---|---|
| meeting day | `Monday` … `Thursday` | `M` … `H` |
| times | minutes since midnight, as strings | `startTimeMilitary` `HHMM` |
| campus | in `campusAbbrev`: `Livingston`, `Busch`, `College Avenue`, `C/D`, `Online` | `LIV`, `BUS`, `CAC`, `D/C`, `ONL` |
| eligibility | trailing whitespace differs | |
| majors | `{code}` or `{description}`; adds a label-only `"MAJ:"` entry (84 sections) | `{code, isMajorCode, isUnitCode}` |
| credits | absent on 57 offerings (variable credit) | null |

### Fields only one source has (for the same sections)

| Only CSP | Sections | Only SOC | Sections |
|---|---|---|---|
| `buildingNum` (meeting) | 874 | `minors` restrictions | 29 |
| `baClassHours` (by-arrangement hours) | 154 | `unitMajors` restrictions | 28 |
| human-readable major/unit descriptions | 84 | `openToText` | 84 |
| | | `honorPrograms`, `sectionCampusLocations`, `sessionDatePrintIndicator`, `crossListedSectionsText` | various |

**Reading.** For section data CSP is a re-presentation of the same
registrar data SOC publishes: identical identities, meetings, instructors and
status in this sample, with a few presentational extras (building numbers,
by-arrangement hours, major names) and **without** SOC's minor and unit-major
restrictions. On this evidence CSP adds no section fact CoursePilot cannot
already get from public SOC; the extras are conveniences.

### Session lifetime

Not yet measured. Known so far: the session saved by a manual sign-in at
00:40 local was reused for a second sign-in phase within the hour. Expiry
behaviour (redirect to CAS vs an HTTP error from the JSON endpoints) still has
to be observed - an open question.

## 2. WebReg schedule PDF - layout and field map (developer's own export)

Measured on the browser-saved "View / Print Schedule" PDF (kept outside the
repository; only shapes and counts recorded).

- **Text layer:** yes - 1 page, 4 fonts, 81 positioned fragments. (A copy
  printed through "Microsoft Print To PDF" has NO text layer - glyphs are
  outlines; refused, see 7.2.4.)
- **Reading-order hazard:** a five-column weekday grid; 16 baselines carry text
  from more than one column, so plain extracted text interleaves days.
  Positions are required (the 7.2.2 parser uses them).
- **By-arrangement region:** the index is a SEPARATE fragment on the same
  baseline as `(course:section)`; joined by baseline.

| Field | Present? | Shape | CoursePilot model |
|---|---|---|---|
| Term | yes (1) | `Season YYYY` | `student_registration.term_code` |
| Course string | yes | `SS:SSS:CCC` inside identities | `course_string` |
| Section number | yes | identity middle part | `section_number` |
| Index number | yes | `…:IIIII` (grid) / separate `IIIII` (arrangement) | `index_number`; SOC match key `(term_code, index_number)` |
| Credits | yes | `(n.n)` per block; `Credits: n.n` for arrangement; total `nn.n` | not stored - SOC credits are authoritative |
| Meeting times | yes | `h:mm AM - h:mm PM` per block; axis labels | not trusted from the PDF - meetings come from the EXACT SOC match |
| Weekdays | yes | column position (5 headers) | as above |
| Campus | yes | name (`Busch`, `Livingston`, …) | as above |
| Building-room | yes | `AAA-A999` | as above |
| Instructor | **no** | - | from SOC only |
| Location note | yes | `Location: … (Online)` | modality evidence only |

**Synthetic fixture:** `backend/tests/fixtures/synthetic/webreg_schedule.SYNTHETIC.pdf`
(generated by `realistic_objects()` in `tests/support/synthetic_webreg.py`):
invented subject 999, building `XYZ`, `example.invalid`. It reproduces all 15
element kinds found in the real print, with none missing and none extra, and
`test_committed_synthetic_webreg_fixture_matches_the_real_layout_kind` pins it.

**DN audit PDF:** pending - `dn_audit.pdf` not yet provided.

## 3. Degree Navigator (DN) - captured 2026-10-22

DN is an **ASP.NET WebForms** application: server-rendered HTML with
`__VIEWSTATE`/`__EVENTVALIDATION` postbacks. **There is no JSON API** - every
fact is inside a rendered page. All pages require a NetID (CAS) session.

### Pages

| Page | Content | Size |
|---|---|---|
| `Home.aspx` | landing; links to each of the student's degrees | 31 KB |
| `DN/Audit/DegreeAudit.aspx?degreeID=&pageid=` | **the audit** | 154 KB |
| `DN/Transcript.aspx` | transcript, nested academic level → term → course | 49 KB |
| `DN/Audit/ManageCoursePlan.aspx` | the student's DN course plan (a WebForms postback, POST → 302) | 32 KB |

Third parties loaded by DN pages: a YouTube embed and Google ad/measurement
requests - recorded because a student-facing integration should not depend on
them.

### Audit structure (`DegreeAudit.aspx`; structure only)

- Requirement blocks: `report` / `requirement_body` (38 in the captured audit),
  grouped in collapsible categories (`DeAcCollapsibleCategory`, 6).
- Per requirement: a status icon, condition bullet lists
  (`conditionBulletListPadding`), a "courses used" grid (`ReportGrid`,
  rows `ReportGridItemA/B`, `coursesUsedSpacer`) and a "needs" line
  (`report_need`).
- Transcript page rows carry a per-course `lblSpecialCodes` label (special
  codes - candidates for transfer/AP/repeat markers; to confirm on the PDF).

### Status vocabulary (DN's own legend, alt text)

| Icon | DN meaning | CoursePilot concept |
|---|---|---|
| `check.gif` | Completed / Fulfilled | completed course; satisfied requirement |
| `x.gif` | Incomplete | unsatisfied requirement |
| `course_cur.gif` | Current | in-progress course |
| `doubletilde.gif` | In Progress or Planned | provisionally satisfied - **DN merges two states CoursePilot keeps apart** |
| `course_pl_Square.gif` | Planned | planned course |

**Reading.** DN's audit is a rendered report, not data. Its per-student facts
can enter CoursePilot only as the student's uploaded DN audit PDF (trust
boundary 3); this capture establishes the vocabulary and structure that PDF
parsing must map, and shows no structured endpoint that would make the PDF
unnecessary.
