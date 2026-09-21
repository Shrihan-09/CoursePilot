# Rutgers Data Sources — Investigation

**Investigated:** 2026-09-08, extended 2026-09-09 (sections) and 2026-09-12
(multi-term) · **Method:** direct HTTP probes, recorded in
`scripts/probe_*.py`. Every claim below was observed, not assumed.

> Nothing in this document is inferred from memory or from third-party
> descriptions. Where a widely-cited source turned out not to exist, that is
> recorded too.

---

## Summary

| Source | Type | Status | Use |
|---|---|---|---|
| SOC `courses.json` | Undocumented JSON | **Verified working** | **Primary source for Phase 1** |
| SOC `openSections.json` | Undocumented JSON | **Verified working** | Live open/closed status |
| `classes.rutgers.edu/soc/` | JS SPA | Verified | Human UI; not for ingestion |
| `catalogs.rutgers.edu` | HTML catalog | Not yet probed | Course descriptions, degree requirements |
| `api-docs.rutgers.edu` | — | **Does not resolve (DNS)** | Not usable |
| SOC `subjects.json` / `init.json` | — | **404** | Do not exist |

---

## 1. SOC `courses.json` — PRIMARY SOURCE

**URL**
```
https://classes.rutgers.edu/soc/api/courses.json?year=<YYYY>&term=<T>&campus=<C>
```

`sis.rutgers.edu/soc/api/...` **301-redirects** to `classes.rutgers.edu`. Use
the latter directly.

**Observed response** (`year=2026&term=9&campus=NB`, fetched 2026-09-08):

| Property | Observed value |
|---|---|
| HTTP status | 200 |
| Content-Type | `application/json` |
| Size | 21,203,955 bytes (~21 MB) |
| Top level | JSON array of 4,400 course objects |
| Auth | None required |

**Parameters.** `term` digits (0 Winter / 1 Spring / 7 Summer / 9 Fall) were
originally community-documented; **all four are now verified against real 200
responses** — see §1c. Campus codes remain partly unverified: only `NB` has
been requested, though `NB` and `OB` both appear in returned data.

### Field population (n = 4,400)

| Field | Populated | Note |
|---|---|---|
| `title` | 100% | **Abbreviated**, ~20 chars (`"COMICS MIDEAST"`) |
| `expandedTitle` | 60.8% | Full title; differs from `title` in 2,409 cases |
| `courseString` | 100% | `unit:subject:course`, e.g. `01:013:120`. 0 malformed |
| `credits` | 88.9% | **11.1% null** |
| `preReqNotes` | 28.0% | Prose + HTML, not structured |
| `synopsisUrl` | 72.3% | Links to department pages |
| `coreCodes` | 22.1% | Core-curriculum codes (e.g. `HST`) |
| `courseDescription` | **0.0%** | **Always empty. See limitations.** |

### Critical observations

**1. `courseDescription` is empty for all 4,400 courses.**
The SOC API does not carry descriptions. They must come from a different
source (the catalog). Any feature needing descriptions — notably semantic
search — is blocked on that second source.

**2. `credits` is not an integer.**
Observed types: `int` (3,820), `null` (489), `float` (91).
Distinct values: `0, 0.5, 1, 1.5, 2, 2.5, 3, 4, 4.5, 5, 6, 9, 12, 16`.
A schema typing this as non-null integer would reject or corrupt ~13% of rows.

**3. `courseString` is NOT unique** — 4,389 distinct across 4,400 records.
Eleven collisions, from two distinct causes:

| Cause | Count | Example | Differing fields |
|---|---|---|---|
| `supplementCode` differs | 2 | `01:750:193` `'  '` vs `'LB'` | credits (4 vs 0), title, sections |
| `campusCode` differs | 9 | `16:400:513` NB vs OB | campus, sections **only** |

This drives the schema design. In the campus cases, course-level attributes
(credits, title) are **identical** — only campus and section data differ. So:

- **course** identity = `(offering_unit_code, subject_code, course_number, supplement_code)`
- **campus** belongs to the **offering**, not the course

Verified: `courseString + supplementCode` leaves exactly the 9 campus cases,
all of which are one course offered at two campuses.

**4. Section `index` is unique within a term** — 11,992 distinct of 11,992.
It is NOT unique across terms (§1c), so it is only a sound key when paired
with the term.

**5. `preReqNotes` is prose with embedded HTML**, not a structured tree:

```
(01:013:141 ELEMENTARY ARABIC II )<em> OR </em>(01:013:145 ACCELERATED ARABIC )
```

Course strings are embedded and extractable, but the boolean structure needs a
real parser. **Not attempted in this phase.**

### Suitability

Appropriate for automated ingestion: no auth, stable JSON, no JS required.
Concerns: undocumented and unversioned, so the shape can change without
notice; 21 MB per term/campus means responses must be cached, not re-fetched.

---

## 1b. SOC sections — inside `courses.json`

**Investigated:** 2026-09-09 · `scripts/probe_sections*.py`

**There is no separate section endpoint.** Sections are nested inside each
course object in the payload above, under a `sections` array. Section
ingestion therefore costs no extra network traffic, and courses and sections
read from one archive are guaranteed consistent with each other.

A section carries **no field naming its own course** — the parent is
structural. The parser must carry the enclosing course's identity down with
each section or the linkage is unrecoverable.

### Volumes (year=2026, term=9, campus=NB)

| Entity | Count |
|---|---|
| Sections | 11,992 |
| Meeting patterns | 17,457 |
| Instructor entries | 12,067 |
| Cross-listing references | 966 |

### Identity

| Candidate | Result |
|---|---|
| `index` | **11,992 distinct of 11,992** — unique, 5-digit, numeric in 100% of cases |
| course + campus + `number` | also unique (0 collisions) |

`index` is the Rutgers registration index — the number a student types into
WebReg. It is unique **within** a term but **heavily reused across terms**,
and a reused index usually points at a different course. Measured across five
terms in §1c; this is why the section key is `(term_code, index_number)`.

Section `number` is 2 characters but **not always numeric**: 344 distinct
non-numeric values such as `1R`, `9A`, `A1`, `C2`.

### Section → course linkage

`campusCode` on a section equals its parent course's `campusCode` in
**11,992 of 11,992** cases. The measured NB/OB duplicate pair confirms the
model: `16:400:513` has section `19370` at NB and `19371` at OB — one course,
two offerings, distinct sections. The `01:750:193` supplement pair likewise
keeps its lecture sections (`13352`+) separate from its lab sections
(`13361`+).

### Enrollment data — DOES NOT EXIST

**The SOC payload contains no seat counts of any kind.** Probed for keys
containing `capacity`, `enroll`, `seat`, `wait`, `avail`, `max`, `limit`, and
`count`: **zero matches**.

The only availability signal is a boolean `openStatus` (8,000 OPEN / 3,992
CLOSED) with no numbers behind it. Any future registration-likelihood feature
must therefore either find a different source or be built from repeated
observations of this boolean over time.

### Meeting patterns — genuinely one-to-many

| Patterns per section | Sections |
|---|---|
| 1 | 7,965 |
| 2 | 2,661 |
| 3 | 1,302 |
| 4 | 56 |
| 5 | 8 |

Fields, and how often populated (n = 17,457):

| Field | Populated |
|---|---|
| `meetingModeCode` / `Desc` | 100% |
| `campusName` / `campusLocation` | 75.6% |
| `meetingDay`, `startTimeMilitary`, `endTimeMilitary` | **63.2%** |
| `buildingCode` / `roomNumber` | 60.8% |

**36.8% of meetings are TBA** — no day, no time. These are research,
independent-study, and asynchronous online meetings.

Day and time are strictly **all-or-nothing**: 0 rows have a day without a
time, 0 have a start without an end, and 0 have a building without a room.

`meetingDay` uses single characters: `M T W H F S U` (H = Thursday,
U = Sunday). `startTimeMilitary` is 4 digits, 82 distinct values, 0 malformed.

**27 meeting modes**, including `90` = `ONLINE INSTRUCTION(INTERNET)` on 1,833
meetings.

### Data-quality anomalies

**3 meetings have `end <= start`** and they are genuine Rutgers records:

| Course | Index | Day | Start | End |
|---|---|---|---|---|
| 07:966:123 | 15777 | S | 1100 | 1100 |
| 07:966:333 | 15813 | T | 2330 | 1250 |
| 07:966:333 | 15813 | F | 2330 | 1250 |

A `CHECK (end > start)` constraint would reject authentic data. CoursePilot
warns instead. See `DATA_MODEL.md`.

### Instructors

| Instructors per section | Sections |
|---|---|
| 0 | 898 |
| 1 | 10,121 |
| 2 | 973 |

The instructor object has exactly one field: `name`. No id, no email, no
netid. 4,004 distinct name strings; `WANG, HAO` alone appears 92 times.

**53 sections list the same name twice** (e.g. `01:447:380` lists
`GLODOWSKI TROTT` twice), so `(section, name)` is not a usable key.

### Cross-listings

Present on 827 sections (6.9%), 966 references total. Each carries the partner
course's full identifier components plus `registrationIndex` and
`primaryRegistrationIndex`; all 966 have a `registrationIndex`.

**36 of the 966 point at an index not present in this campus/term payload** —
the partner section lives elsewhere. This is why CoursePilot stores identifier
components rather than a foreign key.

### Section-level credits — DO NOT EXIST

Probed the section object for keys containing `credit`, `unit`, and `hour`:
the only hit is `unitMajors` (a restriction list). **Credits are a COURSE
attribute only**; no section overrides them.

`meetingTimes.baClassHours` is populated on 6,433 meetings but its only value
is the literal `'B'` — an annotation on a meeting, not a credit figure.

### Section-level prerequisites — DO NOT EXIST

Probed for `prereq`, `prerequisite`, `coreq`, `restrict`: **zero matches** on
the section object. `preReqNotes` is a COURSE field, and **0 sections carry
their own copy**, so every section of a course shares its prerequisites.

Two adjacent section fields do exist, and they are *eligibility*, not
prerequisites:

| Field | Populated | Example values |
|---|---|---|
| `sectionEligibility` | 10.8% | `ALL EXCEPT 1ST YEAR` (360), `1ST YEAR ONLY` (290), `JUNIORS AND SENIORS` (239), `SENIORS ONLY` (203) |
| `openToText` | 30.1% | `UNIT: 07 (Mason Gross School of the Arts (Undergrad))` |
| `specialPermissionAddCode` | 24.2% | `03` (2,307), `04` (337), `26` (116) |

These constrain *who may register*, not *what must be completed first*. Both
are stored verbatim; neither is parsed.

### Campus appears in three places, and they are not the same

| Level | Values |
|---|---|
| `course.campusCode` | NB 4,370 · OB 30 |
| `section.campusCode` | NB 11,923 · OB 69 — **always equals its course's** |
| `meeting.campusName` | BUSCH, COLLEGE AVENUE, DOUGLAS/COOK, LIVINGSTON, ONLINE, DOWNTOWN NB, OFF CAMPUS, STUDY ABROAD, and 4,259 blank |

**830 sections have meetings on more than one campus** — most commonly
`COLLEGE AVENUE + ONLINE` (hybrid delivery), but physical pairs occur too.

This is scheduling-critical: a student in such a section must travel between
campuses between meetings, so campus must be recorded **per meeting**, not
only per section. The `section_meeting` table does this, and the finding is
queryable today:

```sql
SELECT count(*) FROM (
  SELECT section_id FROM section_meeting WHERE campus_name IS NOT NULL
  GROUP BY section_id HAVING count(DISTINCT campus_name) > 1) x;  -- 830
```

### Section subtitle is a topic label, not a title override

Present on 9.6% of sections (`VOICE`, `VIOLIN`, `PIANO`,
`Poetry, Fiction, Creative Nonfiction`). It labels a variable-topic section;
it does **not** replace the course title.

### Registration status — complete enumeration

`openStatus` is strictly boolean: 8,000 `True` / 3,992 `False`, with
`openStatusText` exactly `OPEN` / `CLOSED`. No third state, no waitlist
indicator, no seat count.

## 1c. Multi-term verification (Phase 2.5, 2026-09-12)

### Spring 2027 is NOT PUBLISHED

```
GET .../courses.json?year=2027&term=1&campus=NB
-> 200 OK · application/json · 2 bytes · []  (0 courses, 0 sections)
```

The term is a valid parameter combination — the API answers 200 rather than
404 — but Rutgers has not published it. **An empty array, not an error.** Any
ingestor must distinguish "term not yet published" from "fetch failed"; both
are HTTP 200.

No substitute term was ingested.

### Term availability map (campus=NB, measured 2026-09-12)

| Term | `year` / `term` | Courses | Sections |
|---|---|---|---|
| Fall 2025 | 2025 / 9 | 4,421 | 12,100 |
| Spring 2026 | 2026 / 1 | 4,572 | 11,777 |
| Summer 2026 | 2026 / 7 | 1,046 | 1,698 |
| Fall 2026 | 2026 / 9 | 4,396 | 12,004 |
| Winter 2027 | 2027 / 0 | 116 | 138 |
| **Spring 2027** | 2027 / 1 | **0** | **0** |

Confirms the term digits (0=Winter, 1=Spring, 7=Summer, 9=Fall) against real
responses; previously only `9` had been verified. Summer and Winter are an
order of magnitude smaller than Fall/Spring.

### Rutgers REUSES registration indexes across terms — verified

The central Phase 2.5 question. Measured with
`scripts/probe_cross_term_index.py`:

| Pair | Shared indexes | % of first |
|---|---|---|
| Spring 2026 vs Fall 2026 | **9,829** | 83.5% |
| Fall 2025 vs Spring 2026 | 8,852 | 73.2% |
| Fall 2025 vs Fall 2026 | 8,664 | 71.6% |
| Summer 2026 vs Fall 2026 | 8 | 0.5% |
| Fall 2026 vs Winter 2027 | 21 | 0.2% |

Within any single term, `index` is perfectly unique (12,100 / 11,777 / 1,698 /
11,992 / 138 — zero duplicates in all five).

**A reused index almost always points at a different course:**

| Pair | Shared | → different course | → same course |
|---|---|---|---|
| Fall 2026 vs Fall 2025 | 8,664 | **8,664 (100%)** | 0 |
| Fall 2026 vs Spring 2026 | 9,829 | **9,330 (94.9%)** | 499 |

Concrete example — index `10193`:

```
Fall 2026    -> 01:070:111 section 01
Spring 2026  -> 01:013:130 section 01
```

**Conclusion: the registration index is a term-scoped identifier with no
cross-term meaning.** Keying `course_section` on `index_number` alone would
have collided ~83% of rows on the second term ingested and attached sections
to the wrong courses — silently. `(term_code, index_number)` is **required**,
not merely cautious.

### Winter 2027 ingested alongside Fall 2026 (Phase 2.75, 2026-09-13)

The cross-term identity is no longer only measured - two terms now coexist in
the development database.

| | Fall 2026 | Winter 2027 |
|---|---|---|
| Courses in payload | 4,400 | 116 |
| Sections | 11,992 | 138 |
| New course rows created | 4,391 | **+24** (92 already existed) |
| Offerings | 4,400 | +116 |

**21 registration indexes are shared between the two terms, and all 21 point
at a different course.** In the live database each produced exactly 2 rows,
2 distinct terms, 2 distinct courses - zero collisions.

```
index 11485   Fall 2026 -> 01:198:142  DATA 101
              Winter 27 -> 17:194:502  TOPICS
index 11623   Fall 2026 -> 01:198:439  INTRODUCTION TO DATA SCIENCE
              Winter 27 -> 01:050:267  AMERICAN FILM DIRECTORS
```

Had the section key omitted `term_code`, a student looking up index 11623
would have been shown *American Film Directors* instead of *Introduction to
Data Science*.

Fall 2026 was left byte-identical (same 11,992 section rows, verified by id,
not merely by count), and re-running either term inserts nothing.

### Course attributes drift between terms

Among 2,159 courses present in both Fall 2026 and Spring 2026:

| Field | Differs |
|---|---|
| `preReqNotes` | 94 (4.4%) |
| `title` | 23 (1.1%) |
| `credits` | 3 (0.1%) |
| `level` | 0 |

Real examples:

```
16:640:591   Fall 2026 = 3 credits   Spring 2026 = 4 credits
18:820:511   Fall 2026 = null        Spring 2026 = 3
11:709:481   "NUTRITION SEMINAR"  vs  "SEMINAR IN NUTRITION"
```

Credits are modeled on `course`, so a later term's value overwrites an
earlier one. At 0.1% this is a known limitation, not a redesign trigger — see
`docs/DATA_MODEL.md`.

### Structure is stable across terms

Comparing Fall 2026 with Spring 2026:

* **section keys: identical** — no field present in one term and absent in the other
* non-numeric `index`: **0** · unknown `meetingDay`: **0** · `end <= start`: **0** in Spring (3 in Fall)
* max meetings per section **5** in both; max instructors **2** in both
* proportions shift slightly: TBA 36.8% → 38.6%, open 66.7% → 69.4%

Every Phase 2 CHECK constraint holds against a term it was not designed from.

### The published term keeps changing after classes begin

| | Courses | Sections |
|---|---|---|
| Archived 2026-09-08 (ingested) | 4,400 | 11,992 |
| Live 2026-09-12 | 4,396 | 12,004 |

Four days, after the term started. A payload is a **point-in-time
observation**, never a durable fact — which is exactly why `data_source`
carries a content hash and retrieval timestamp.

### Always empty — no columns created

`sessionDates`, `subtopic`, and `legendKey` are empty on 100% of 11,992
sections. `printed` is the constant `'Y'` on 100%.

### Restriction lists — deferred

`majors` (21.9%), `unitMajors` (8.9%), `minors` (2.1%), and `honorPrograms`
(1.2%) are real one-to-many lists, not modeled in this phase. Their
human-readable equivalents (`openToText`, `sectionEligibility`) are stored, so
nothing is lost.

---

## 2. SOC `openSections.json`

```
https://classes.rutgers.edu/soc/api/openSections.json?year=2026&term=9&campus=NB
```

**Verified:** 200, `application/json`, 89,273 bytes, array of 11,159 section
index strings.

Live registration status. Volatile by nature — a point-in-time observation,
never a durable fact. Out of scope this phase.

---

## 3. `classes.rutgers.edu/soc/` (human UI)

JavaScript SPA that loads data from the JSON endpoints above. **Scraping it
would be strictly worse** than calling the JSON directly: it needs a browser
engine, is far more fragile, and yields the same data. This is why the project
does not use Playwright/BeautifulSoup for course data.

Carries the notice: *"The University reserves the right to change, add and
delete course offerings and to alter, add or cancel course sections without
further general notice."* — i.e. this data is inherently volatile.

---

## 4. `catalogs.rutgers.edu` — NOT YET INVESTIGATED

Expected to hold course descriptions and degree requirements, which SOC lacks.
Server-rendered HTML, so parsing is required. **Deliberately deferred** —
Phase 1 proves the pipeline on the structured source first.

---

## 5. Sources that do NOT exist

Recorded so nobody re-investigates them:

- **`api-docs.rutgers.edu`** — appeared in search results as an official
  Rutgers API hub. **DNS does not resolve.** Not usable.
- **`/soc/api/subjects.json`** — HTTP 404.
- **`/soc/api/init.json`** — HTTP 404.

---

## Limitations and risks

| Issue | Impact | Mitigation |
|---|---|---|
| **No course descriptions in SOC** | Blocks semantic search | Need catalog source |
| **API is undocumented/unversioned** | Shape may change silently | Validate every field; fail loudly; archive raw payloads |
| **No official terms of use located** | Legal/ethical uncertainty | Rate-limit, identify the client, cache aggressively. **Confirm before scaling** |
| **Prerequisites are prose** | Cannot deterministically validate prereqs yet | Store `raw_text`; parse later; report `INDETERMINATE` |
| **Term/campus codes unverified** | Wrong params yield wrong data | Only `2026/9/NB` verified |
| **Data is volatile** | Sections change without notice | Provenance timestamps on every row |

## Still open — `TODO(rutgers-source)`

- [ ] Terms of use / acceptable-use policy for the SOC endpoint
- [ ] Authoritative source for course descriptions
- [ ] Authoritative source for degree requirements, per school
- [ ] Confirm the full term-code and campus-code sets
- [ ] Whether historical terms remain queryable

---

## 2. Rutgers catalog + degree requirements (Phase 3, 2026-09-13)

### Source authority matrix

| Data | Preferred source | Authority | Notes |
|---|---|---|---|
| course code / identity | SOC `courses.json` | **authoritative** | Phase 1; `unit:subject:number` |
| course title (abbrev) | SOC | authoritative | ~20 chars |
| course title (full) | **Catalog** | authoritative | SOC's `expandedTitle` only 60.8% populated |
| **course description** | **Catalog** | **authoritative** | **SOC has ZERO descriptions** |
| course credits (term) | SOC | authoritative | numeric, per term |
| course credits (catalog) | Catalog | authoritative | can be a RANGE, e.g. `3-4` |
| sections / meetings / instructors | SOC | authoritative | Phase 2 |
| open/closed status | SOC | point-in-time | no seat counts exist |
| prerequisites | SOC `preReqNotes` | authoritative but **prose** | unparsed |
| **major requirements** | **Catalog** | **authoritative but PROSE** | must be hand-curated |
| Core Curriculum | Catalog (SAS pages) | authoritative | not yet ingested |
| catalog year | Catalog subdomain | authoritative | encoded in the URL |
| degree audit | Degree Navigator | **NOT authoritative** | see below |

Different facts come from different sources on purpose. Forcing everything
through one Rutgers system would mean either no descriptions (SOC) or no
sections (catalog).

### The catalog: `catalogs.rutgers.edu`

`catalogs.rutgers.edu` -> `main.catalogs.rutgers.edu`. **One subdomain per
catalog year**, so catalog year is part of the URL:

```
https://newbrunswick-26-27-undergrad.catalogs.rutgers.edu          (current)
https://newbrunswick-25-26-undergrad-archive.catalogs.rutgers.edu  (archive)
```

Both verified HTTP 200. Note the naming is **inconsistent** between the two
(`26-27-undergrad` vs `undergrad-25-26`), so URLs cannot be generated from a
year - they must be discovered from `main.catalogs.rutgers.edu`.

**Platform: Coursedog.** A Nuxt 3 SPA.

| Access path | Result |
|---|---|
| `app.coursedog.com/api/v1/ca/rutgers-catalog/catalogs` | **HTTP 401** - auth required |
| Rendered page `__NUXT_DATA__` payload | **HTTP 200, works** |

The public Coursedog API requires credentials we do not have, and no attempt
was made to circumvent that. The pages themselves embed a `__NUXT_DATA__` SSR
payload - structured JSON inside the HTML - so **no browser engine is needed**.
Playwright and BeautifulSoup remain unnecessary.

Nuxt serializes that payload as a flat array of integer pointers; resolving it
is a few lines (`scripts/probe_cs_program.py`).

### Course descriptions - FOUND

Measured on the CS 198 catalog page, both years:

| | 26-27 | 25-26 |
|---|---|---|
| course entries parsed | 43 | 43 |
| **with a real description** | **43 (100%)** | **43 (100%)** |
| distinct credit strings | `1`, `3`, `3-4`, `4` | same |

This closes the Phase 1 gap. Note `3-4`: the catalog publishes credit
**ranges**, which SOC's single numeric field cannot express - hence
`catalog_course_entry.credits_min/credits_max`.

### Major requirements are PROSE, not structured data

The decisive finding of this phase. Coursedog's platform schema contains
requirement primitives (`requirementType`, `courseCount`, `requirementSelect`,
`courseRequirementGroup`) but Rutgers has **not populated them** for the
programs inspected. The CS major requirement is a single 1,661-character
English paragraph.

Consequence: CoursePilot cannot *extract* requirements. It must **curate**
them - a human reads official prose and encodes the structure. Every curated
row therefore stores `source_prose`, `source_url`, and a `curation_status` of
`curated_from_prose`, and there is deliberately **no `extracted` status**.

### Catalog-year behaviour

| Compared | Result |
|---|---|
| CS major requirements 25-26 vs 26-27 | **byte-identical** |
| CS minor / entry requirements / learning goals | identical |
| 43 course descriptions, titles, credits | identical, 0 changes |

So real requirement drift could not be demonstrated. Versioning is still
mandatory - the schema must not *assume* stability - but the isolation
property is proved with clearly-labelled **synthetic** fixtures
(`tests/test_catalog_versioning.py`), per the rule that invented data is never
mixed with Rutgers data.

### Degree Navigator - NOT authoritative, and not scraped

`degreenavigator.rutgers.edu` does not resolve. The real hosts are
`nbdn.rutgers.edu` / `dn.rutgers.edu`, behind **NetID (CAS) authentication**.

Rutgers' own description settles its authority:

> "it is not an official transcript of your academic record, nor does it
> constitute a contract between you and Rutgers. Verification of college and
> degree requirements can only be certified by an academic advisor."

Degree Navigator is a **derived audit tool**, not a source of requirements. It
is therefore not scraped: it is authenticated, explicitly non-authoritative,
and duplicates what the catalog already publishes. CoursePilot owns its own
deterministic requirement engine, and adopts the same disclaimer.

**What Degree Navigator has that we cannot otherwise get:** a student's
official transcript and Rutgers' own allocation decisions. Both would require
authenticated per-student access, which is out of scope.

### Requirement edge cases measured in the real CS prose

The single paragraph contains, verbatim:

| Clause | Structure needed |
|---|---|
| "six required courses ... 111, 112, 205, 206, 211, and 344" | `all_of` |
| "three courses in mathematics" | `all_of` |
| "five electives from a designated list" | `choose_n(5)` |
| "at most, two ... outside the Department of Computer Science" | group constraint |
| "at least two must be ... at the 300 level or above" | group constraint |
| "four physics courses ... or chemistry ..." | **nested `any_of` of `all_of`** |
| "requires 51-55 credits" | credit **range** |
| "No more than one grade of D" | cross-requirement grade rule |
| "will not receive credit for ... 105, 107, 110, 142, 170, 405" | exclusion rule |
| "minimum of seven courses ... in the Rutgers-NB Department" | residency rule |

A flat course -> requirement table cannot represent any of the last five.

### Limitations

* **The designated elective list is NOT published in the catalog.** The prose
  defers to "a computer science adviser or the departmental website". Only the
  constraints the prose states directly are encoded; non-CS elective options
  are deliberately absent rather than invented.
* Grade rules, exclusions, and residency are recorded in the fixture under
  `rules_not_yet_modeled` but are **not evaluated** by the engine.
* Core Curriculum requirements are not yet ingested.
* One program (CS B.A.) only. The B.S. variant is modeled in the prose but not
  encoded.
* No official terms of use located for the catalog site.

---

## 3. Catalog course ingestion (Phase 3.5)

The Phase 3 probe script is now a real pipeline:
`fetch -> archive -> parse -> normalize -> validate -> load`.

### Measured (CS 198 page, both catalog years)

| | 2026-2027 | 2025-2026 |
|---|---|---|
| Course entries parsed | **44** | **44** |
| With a description | 44 (100%) | 44 (100%) |
| Credit ranges | 1 (`01:198:442` = `3-4`) | 1 |
| No published credits | 1 (`01:198:110`) | 1 |
| Mapped to a `course` row | 31 | 31 |
| Stored unlinked | 13 | 13 |

### Two parser bugs found by real data

**1. A split title silently swallowed the next course.**
`01:198:110`'s title contains an internal `</em></strong><strong><em>` break.
The original single-regex approach (promoted from the probe script) let the
title group run past the end of its own entry and consume `01:198:111` whole -
**a course the CS major requires** - with no parse failure reported. Entry
counts looked plausible at 43.

Fixed by splitting the block on the course-code marker first, which is
unambiguous. A malformed chunk can now only lose itself, never its neighbour.
The true count is 44, not 43.

**2. Not every course publishes credits.**
`01:198:110` has a title and a description but no `(N)`. The original regex
required the parentheses, so it returned nothing for all three fields. Title
and credits are now parsed independently.

### Why 13 catalog courses have no `course` row

Investigated per category. **None is a formatting or supplement problem** -
all 13 are absent from `course` under any supplement:

| Category | Count | Evidence |
|---|---|---|
| Real course, not offered in an ingested term | 4 | `415`, `431`, `442`, `452` appear in Spring 2026 / Fall 2025 archives |
| Catalog-only: no offering in any archived term | 9 | absent from all five archived terms |

**The catalog is a superset of what SOC offers in any given term.** A required
FK would have made 30% of the authoritative description source unstorable, and
a degree audit must be able to name a course a student took years ago that
nobody is teaching this term.

So `catalog_course_entry.course_id` is **nullable**, the natural key is
`(course_string, catalog_year)`, and the link is backfilled if SOC later
offers the course. This does not duplicate `course`: no SOC identity or
credits are copied, only the catalog's own description of a course code.

### SOC credits are never overwritten

`course.credits` (SOC, term-scoped, single numeric) and
`catalog_course_entry.credits_min/max` (catalog-year, can be a range) are
different facts from different sources. A test pins that catalog ingestion
leaves `course.credits` untouched.

---

## 4. Core Curriculum - source investigation only (Phase 3.5 §18)

**Not implemented.** Findings sufficient to plan it:

| Question | Answer |
|---|---|
| Official source? | `sasundergrad.rutgers.edu` (SAS Advising) and `sasoue.rutgers.edu/core/core-learning-goals` (SAS Office of Undergraduate Education) |
| Catalog-based? | **Partly.** The catalog links to it; the authoritative goal definitions and certified-course lists live on the SAS sites |
| Catalog-year versioned? | Yes - SAS states requirements are specific to a student's catalog year |
| Course lists, learning goals, or both? | **Both.** Goals are the requirement; certified course lists are the eligibility |
| Can it fit ProgramVersion/Requirement? | Yes, with one change - see below |

### The important discovery: SOC already publishes the mapping

SOC's `coreCodes` field is the machine-readable course -> core-goal mapping we
would otherwise have to scrape:

* **972 of 4,400 courses (22.1%)** carry core codes
* **19 distinct codes**, matching the published SAS goal codes: `WCd` (92),
  `HST` (80), `AHp` (77), `CCD` (65), `CCO` (64), `WCr` (62), `NS` (60),
  `SCL` (58), `AHo` (53), `QQ` (35), `ITR` (34), `QR` (33), `AHq` (23),
  `AHr` (13), plus non-SAS codes (`SOEHS` 633, `GVT`, `ECN`)
* Each entry carries `year`, `term`, and `effective` - **already term-scoped**

So Core is *more* tractable than major requirements: the course->goal mapping
is structured data, not prose. Only the goal definitions and credit rules need
curating.

### One architectural consequence

SAS states that **"a course used to meet core goals may also be used to
fulfill a major or minor requirement"**, and **375 courses satisfy more than
one core goal**.

The current allocator assigns each course to **at most one** requirement slot.
Core will therefore need explicit double-counting support - the
`requirement_sharing_policy` table already sketched in `DATA_MODEL.md` §5.
This is a real design change, not a configuration flag, and it should be
settled before Core ingestion begins.

Also noted: `WCr` **"must be fulfilled by taking a class at Rutgers-New
Brunswick; transfer and AP courses are not certified"** - the same residency
information gap that makes the CS residency rule unevaluable.

---

## 5. SAS Core Curriculum (Phase 4)

### Authority matrix

Core is the first CoursePilot subject where two official Rutgers sources are
each authoritative for a DIFFERENT fact. Neither is authoritative for both.

| Fact | Authoritative source | Why |
|---|---|---|
| Goal codes, names, official wording | `sasoue.rutgers.edu/core/core-learning-goals` | The SAS Office of Undergraduate Education defines the goals |
| Area groupings, course/credit counts | same page, plus `sasundergrad.rutgers.edu/degree-requirements/core` | Published requirement structure |
| Sharing with major/minor | `sasundergrad.rutgers.edu/degree-requirements/core` | States the permission verbatim |
| **Course to goal eligibility** | **Rutgers SOC `coreCodes`** | SOC publishes certification as STRUCTURED data, per course, per term |
| Course identity, credits | SOC `courses.json` (Phase 1) | unchanged |

The split matters: the SAS pages never list which courses are certified, and
SOC never states how many courses an area requires. Forcing either to answer
the other's question would mean inventing data.

**Degree Navigator remains non-authoritative and unscraped** - Rutgers itself
says only an academic advisor can certify requirements.

### The 13 official goals

From the SAS OUE page: `CCD`, `CCO` (Contemporary Challenges); `NS`, `HST`,
`SCL`, `AHo`, `AHp`, `AHq`, `AHr` (Areas of Inquiry); `WCr`, `WCd`, `QQ`, `QR`
(Cognitive Skills and Processes).

Structure, quoted from the source:

```
Contemporary Challenges            2 courses; 1 from each category (CCD, CCO)
Natural Sciences [NS]              6 credits            <- credits, NO course count
Historical and Social Analysis     6 credits; 2 courses (HST 3cr + SCL 3cr)
Arts and the Humanities [AH]       6 credits; 2 courses; at least 2 goals
Writing and Communication          9 credits; 3 courses (WCr, WCd)
Quantitative and Formal Reasoning  6 credits; 2 courses (QQ, QR)
```

NS states **credits but no course count**, so it is modeled as a `credits`
requirement rather than inventing a number of courses.

### SOC coreCodes - measured field semantics

Verified before use (`scripts/probe_core_codes.py`), not assumed:

| Field | What it ACTUALLY is |
|---|---|
| `code` / `coreCode` | identical in all 1,541 entries - redundant |
| `description` / `coreCodeDescription` | identical in all entries |
| `year`, `term`, `effective` | **echo the PAYLOAD's term**, not a goal's validity window. Every Fall 2026 entry reads year=2026, term=9, effective=20269 |
| `lastUpdated` | epoch **milliseconds**, range 2016-07-13 to 2024-10-07 - a certification timestamp, NOT a catalog year |

**This is why `effective` is not used as a catalog year.** The name suggests
it; the data says it is the term we asked for.

### coreCodes is NOT purely SAS Core

19 distinct codes in Fall 2026 NB, of which only 13 are SAS Core goals:

| Code | Entries | Why excluded |
|---|---|---|
| `SOEHS` | 633 | "SOE: Approved Humanities/Social Science" - School of Engineering |
| `CE` | 145 | description literally reads "**Non-Core**: Community Engagement" |
| `ITR` | 34 | "Information Technology and Research" - **source conflict**, see below |
| `GVT` | 7 | "SEBS Core: Government/Regulatory Analysis" |
| `ECN` | 4 | "SEBS Core: Economic Analysis" |
| `WC` | 3 | "Writing and Communication 01:355:101" - **source conflict** |

826 of 1,541 entries belong to codes that are not SAS Core.

### Source conflicts - recorded, not silently resolved

**`ITR` and `WC`** are certified on courses in SOC but appear on neither
official SAS page as current goals. Most likely retired goals whose course
certifications persist.

Resolution: **the SAS pages are authoritative for which goals exist**, so no
requirement node is created for them. Their eligibility entries are counted
and reported as unmapped rather than dropped or guessed at. A future catalog
year may confirm retirement.

**Case conflict:** the official page writes `WCR`/`WCD`; SOC writes
`WCr`/`WCd`. Same goals. Matched case-insensitively; the CURATED spelling is
stored, since the SAS page is authoritative for goal identity.

### Real-data validation (2026-2027, Fall 2026 NB payload)

| Measure | Value |
|---|---|
| Core goals defined | **13** |
| Core requirement nodes | **13** |
| SOC coreCode entries seen | **1,541** |
| Entries for the 13 SAS goals | **715** |
| Entries excluded (non-SAS codes) | **826** |
| Eligibility rows loaded | **714** (Phase 4.1; was 616) |
| Distinct courses represented | **470** |
| Unresolved course mappings | **0** |
| Duplicate mappings detected | **1** |
| Invalid mappings | **0** |
| Source conflicts | **6 codes** |

715 goal-certifications become 714 `(requirement, course, category)` rows -
one row per certifying goal, with a single exact duplicate reported. Several
goals still feed one requirement node (AHo/AHp/AHq/AHr to CORE_AH, WCr/WCd to
CORE_WC, QQ/QR to CORE_QFR), but the goal is no longer discarded: it is the
`category` column, and CORE_AH cannot be evaluated without it.

Phase 4 collapsed these to 616 rows keyed on `(requirement, course)` alone.
The rows that reappeared are exactly the multiply-certified courses:

Per requirement: CORE_AH 166 (was 135), CORE_WC 154 (was 112), CORE_HST 80,
CORE_QFR 68 (was 43), CORE_CCD 65, CORE_CCO 64, CORE_NS 59, CORE_SCL 58.
AH splits AHo 53, AHp 77, AHq 23, AHr 13.

A course certified for two AH goals is now two rows on CORE_AH. It still
fills ONE slot - eligibility is deduplicated per course before allocation,
which `test_dual_certified_course_fills_one_slot_not_two` pins.

### Limitations

1. ~~**"At least 2 goals" on Arts and Humanities is NOT enforced.**~~
   **RESOLVED in Phase 4.1.** `RequirementCourseOption.category` now records
   the certifying goal and `Requirement.min_distinct_categories` records how
   many distinct goals are required, so CORE_AH carries both conditions the
   source states. Two AHp courses no longer satisfy it. See DATA_MODEL.md 16.
2. ~~**A `credits` requirement can be starved.**~~ **RESOLVED in Phase 4.1.**
   Credit requirements contribute no slots to the matching and are settled
   after it, taking only what count requirements left and stopping at the
   minimum. Verified on real data: a student holding 01:070:111 (CCD/CCO/NS)
   plus two NS-only courses now gets CORE_CCO satisfied AND CORE_NS at
   6.0/6.0, where CORE_NS previously claimed all three.
2b. **The allocator still maximises filled SLOTS, not satisfied
   REQUIREMENTS.** Where two arrangements fill equally many slots it is
   indifferent, and most-constrained-first breaks the tie - so a student can
   see CORE_CCD satisfied and CORE_AH at 1 of 2 where the reverse was equally
   available. Investigated in Phase 4.2: Rutgers publishes an objective that
   contradicts this (see below), and two concrete defects are pinned by
   `tests/test_allocation_objective.py`. Recorded in DATA_MODEL.md 17.
   The category half was fixed in Phase 4.2 (DATA_MODEL.md 18); the global
   objective remains unchanged pending a design decision.

### Allocation precedence (retrieved 2026-09-20)

| Fact | Source | Authority |
|---|---|---|
| "DN will always adjust the audit so that the maximum number of requirements are complete" | `sasundergrad.sas.rutgers.edu/resources/faq/faq-detail/i-already-took-a-course-certified-for-both-hst-and-scl...` | SAS Academic Advising, describing the OFFICIAL AUDIT'S intent. Not authoritative for requirement content. |
| "Courses may be applied to multiple learning goals, as long as they are applied to different requirements" | SAS Core Curriculum FAQ | SAS Academic Advising |
| "HST and SCL - two different classes must be taken"; one course on both lists still needs "TWO courses (6 credits)" | SAS Core Curriculum FAQ | Confirms one course fills one slot WITHIN Core |
| "Courses may be counted as meeting multiple learning goals" | `sasundergrad.rutgers.edu/majors-and-core-curriculum/core/about-sas-core` | ELIGIBILITY, not simultaneous satisfaction - read together with the row above |
| "students generally will complete the core in 10 to 14 courses of 3 or 4 credits each" | about-sas-core | 13 goals in 10-14 courses corroborates ~one course per goal |
| "any credits satisfying the Core Curriculum may count toward a major or minor unless prohibited by the major or minor" | SAS degree requirements | Sharing is a per-program permission - matches `sharing_policy` granularity |

**Not found in any Rutgers source:** a precedence rule between major and core;
a rule for choosing among equally complete allocations; DN's actual
algorithm. Degree Navigator was NOT scraped and no authenticated access was
used - these are public advising pages.
3. **Only catalog year 2026-2027** is loaded. The model is year-versioned and
   the validator rejects cross-year mixing, but 2025-2026 Core is not ingested.
4. **Eligibility reflects one term's payload** (Fall 2026 NB). A course
   certified only in another term is absent. The mapping is highly stable -
   555 of 558 shared courses had identical goals between Fall 2026 and Spring
   2026 - but it is not a catalog-wide extract.
5. `ITR`/`WC` retirement is inferred from absence, not from a Rutgers
   statement of retirement.
