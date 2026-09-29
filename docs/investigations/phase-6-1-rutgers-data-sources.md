# Phase 6.1 — Rutgers Data Sources & Coverage Investigation

**Status:** investigation only. No production code, schema or data was
changed. Evidence was gathered 2026-09-28/29 by anonymous, rate-limited GETs
(project User-Agent, 1.5 s between requests), from code, and from read-only
queries against the development database.

**Evidence files:**

- `docs/investigations/evidence/phase-6-1-probes.json`: system probes, SOC
  semantics and prerequisite parseability
- `docs/investigations/evidence/phase-6-1-discovery.json`: catalog discovery
- **tooling:** `scripts/probe_rutgers_sources.py` and
  `scripts/discover_catalog_programs.py`
- **raw pages:** archived under `data/raw/probes/` (gitignored, like all raw
  archives)

Every URL touched was either linked from an official Rutgers page already
archived by CoursePilot, or found inside a public page fetched here. Every
authentication boundary was recorded and not crossed.

---

## 1. Executive summary

1. **SOC already gives CoursePilot almost everything a scheduler and a
   notifier need, and most of it is already stored.** Course identity,
   sections, index numbers, meeting times, meeting mode (online/hybrid),
   instructors, campuses, cross-listings, exam codes, special-permission codes,
   section eligibility text and open status are all persisted: 11,992 Fall 2026
   sections and 17,678 meetings. What is missing is **freshness**. Open status
   is a snapshot from 2026-09-11, and nothing refreshes it.

2. **SOC exposes a second, lightweight endpoint built for polling.**
   `openSections.json` is referenced by the public SOC app's own JavaScript.
   It is about 96 KB (gzipped), returns in 0.08 s, and is served with
   `Cache-Control: max-age=30`. `courses.json` is 21 MB and `max-age=900`.
   This is the right source for "a section opened" notifications. It is a
   **superset**: all 8,546 sections with `openStatus: true` are in it, plus
   3,437 indexes that do not appear in the NB `courses.json`. Those must be
   ignored after joining against the term's `courses.json`.

3. **SOC prerequisites are mostly machine-readable.** `preReqNotes` is a
   boolean expression over course codes. **929 of 1,234 (75.3%)** stored
   strings are pure `and`/`or`/parenthesis expressions. The rest include
   "Any Course EQUAL or GREATER Than: (…)" and program restrictions.
   CoursePilot stores the raw string and parses nothing.

4. **Degree Navigator is authenticated end to end.** `dn.rutgers.edu`
   redirects straight to Rutgers CAS NetID login (`renew=true`), and the login
   page states "You agree to the terms of use by logging in". Its public site
   says it holds program definitions for "any undergraduate program", "what if
   I change majors?" audits, and prerequisites, co-requisites and
   equivalencies. None of that is reachable anonymously. **Verdict: not a
   CoursePilot data source. At most, a human validation reference.**

5. **The Course Schedule Planner (CSP) is a login-gated application, not a
   data source.** Its landing page is "Login - Course Schedule Planner"
   (NetID). Its advertised value is behaviour: automated schedule generation,
   wish list, WebReg and Degree Navigator integration. The only public
   Rutgers data interfaces observed are SOC's two endpoints. **Verdict: build
   CSP-like scheduling from SOC. Do not integrate CSP.**

6. **WebReg forbids automation, in its own words.** "The use of automated
   software for registration is prohibited. If detected, the student's online
   registration privileges will be suspended." **Verdict: CoursePilot must
   not automate WebReg.** At most it deep-links students to WebReg with the
   index numbers they chose. Notifications do not need WebReg.

7. **Rutgers program requirements are prose, everywhere sampled.** Coursedog
   ships form-schema and field-configuration objects for `requirementType`,
   `courseCount`, `minimumGrade`, `degreeMapName` and similar on **every** page.
   Across 14 sampled program pages in 8 schools, **0** objects contain
   program-specific structured requirement data. The platform could hold
   structured requirements; Rutgers has not populated them.

8. **Programs can be discovered automatically; they cannot be supported
   automatically.** The navigation tree embedded in every catalog page
   enumerates the NB undergraduate catalog:
   - **516 pages** in 2026-2027, across 11 schools;
   - **172 subject-coded department/program pages** (151 distinct codes;
     18 codes appear on two pages);
   - 137 further program-area pages;
   - **430 of 433** URLs present in both years keep the same Coursedog
     `pageId`.

   Pages are not programs, though: the Mathematics page alone holds a major
   with three options, a minor, a certificate and two interdisciplinary
   majors. The catalog's `/sitemap.xml` returns 404.

9. **Two existing bugs were found, documented here and not fixed** (per the
   brief):
   - **(a)** A course passed twice fills two slots. `01:198:314` taken twice
     filled two of the five `CS_ELECTIVES` slots and counted 8 credits.
   - **(b)** Search ranks code-shaped queries poorly. This carries over from
     Phase 6.0.

**Bottom line:** the Planning Engine is **not** the next phase. Its two
hardest inputs are missing: a parsed prerequisite graph and multi-term
availability. Program support is still two majors. The next phase should
turn SOC's prerequisite expressions into data and fix the retake bug, before
any planner is built on top.

---

## 2. Current architecture (traced from code)

```
Rutgers SOC  classes.rutgers.edu/soc/api/courses.json?year&term&campus
   │  SocFetcher (fetchers/soc.py): tenacity retry, archive = cache
   ▼
data/raw/soc_courses_<y>_<t>_<campus>.json      (5 terms archived; 2 loaded)
   │  SocParser -> SocNormalizer -> SocValidator -> CourseLoader     pipelines/courses.py
   │  SocSectionParser -> SocSectionNormalizer -> validator -> SectionLoader   pipelines/sections.py
   ▼
subject, course, course_offering, course_section, section_meeting,
section_instructor, section_cross_listing, data_source
   │
   ├─ SOC coreCodes ──> CoreEligibilityParser ─┐
   │                                            ▼
   │  sas_core_26_27.json (curated) -> CoreIngestionPipeline -> requirement(system=core)
   │
Rutgers catalog (Coursedog)  newbrunswick-<yy-yy>-undergrad[-archive].catalogs.rutgers.edu
   │  CatalogFetcher (fetchers/catalog.py), hosts listed explicitly (not derivable from year)
   ▼
data/raw/catalog/catalog_<program>_<year>.html
   ├─ CatalogParser (__NUXT_DATA__) -> CatalogNormalizer -> CatalogValidator -> CatalogLoader
   │      -> catalog_course_entry  (descriptions, catalog-year scoped)
   └─ human/AI encoding of prose -> <program>_requirements_<yy>.json (tests/fixtures)
          -> RequirementLoader -> program, program_version, requirement,
             requirement_course_option, program_rule
   ▼
Services:
  DegreeAuditEngine (+ cached_audit)   /student/audit
  scenarios, programs                  /programs, /student/scenarios/*
  search (BM25 over course + catalog entries, rebuilt via search_version)
  explanations (evidence + optional LLM)   /explanations/recommendation
```

- **Schedulers:** none. There are no background, periodic or cron jobs;
  every ingestion is a manual CLI or script run.
- **Rutgers configuration:** `.env.example` declares `RUTGERS_COURSE_DATA_BASE_URL`,
  `RUTGERS_CATALOG_BASE_URL` and `RUTGERS_ACADEMIC_TERM`, blank and unused.
  URLs live in `sources/soc.py` (`SOC_BASE`) and `sources/catalog.py`
  (`CATALOG_HOSTS`, `PROGRAM_PATHS`).
- **Caching:** the raw archive is the fetch cache. The audit cache is
  `student_audit_cache`. The search index registry is keyed by `search_version`.

## 3. Current data inventory

| entity.field | source | stored in | persisted | nature | refresh | weakness |
|---|---|---|---|---|---|---|
| course identity (unit:subject:number:supplement) | SOC | `course` | yes | stable | manual | only terms ingested |
| title, expanded title | SOC | `course.title`, `title_abbrev` | yes | stable | manual | |
| description | **catalog** (SOC returns "" for 100%) | `catalog_course_entry` | yes, per catalog year | versioned | manual | only CS and Math pages ingested (212 entries) |
| credits | SOC; catalog ranges | `course.credits`; `catalog_course_entry.credits_min/max` | yes | stable | manual | two sources, never reconciled |
| level, school | SOC | `course.level`, `school_code` | yes | stable | | |
| prerequisites | SOC `preReqNotes` | `course.prereq_notes_raw` | raw only | versioned by term | manual | **unparsed**; 1,234 of 4,415 have text |
| corequisites, equivalencies | not in SOC; Degree Navigator (authenticated) | none | no | | | **missing** |
| SAS Core certifications | SOC `coreCodes` | `requirement_course_option` (core) | yes | term-observed | manual | SAS only |
| course notes, subject notes, synopsis URL | SOC | `synopsis_url` only | partly | | | notes dropped |
| offering (course × term × campus) | SOC | `course_offering` | yes | term | manual | Fall 2026 full; Winter 2027 sample (116) |
| section index, number | SOC | `course_section` | yes | term | manual | |
| open status | SOC `openStatus` | `course_section.open_status` | **snapshot** | **live** | none | stale since 2026-09-11 |
| meetings (day, military times, mode, building, room, campus) | SOC | `section_meeting` | yes | term | manual | |
| instructors | SOC | `section_instructor` | yes | term, changes | manual | |
| cross-listings | SOC | `section_cross_listing` | yes | term | manual | |
| exam code, special permission, eligibility text, open-to text, comments | SOC | `course_section.*` | yes (text) | term | manual | restrictions are text, not structured |
| section majors, minors, unitMajors restrictions | SOC | none | **no** | term | | dropped |
| honors programs, session dates | SOC | none | no | term | | dropped |
| program, degree type, school | curated JSON | `program`, `school` | yes | versioned | manual | 2 programs |
| catalog year | curated JSON | `program_version` | yes | versioned | | |
| requirements | curated JSON from prose | `requirement`, `requirement_course_option`, `program_rule` | yes | versioned | manual reload (refresh fixed in 6.0) | eligibility additive-only |
| requirement source text | archived catalog page | `source_prose` on each node | yes | versioned | | |
| verification status | curated JSON | `curation_status` | yes | | | no reviewer identity or timestamp |
| per-option minimum grade | none | `requirement_course_option.min_grade` | **column exists, 0 of 1,531 populated, never evaluated** | | | dormant |
| student record | test/admin | `student`, `student_course` | yes | student | | self-reported only |

## 4. Degree Navigator findings

**Evidence** (in `phase-6-1-probes.json` → `systems.degree_navigator` and
`degree_navigator_followup`):

- **Two hosts.** `http://nbdn.rutgers.edu` → 301 → `https://nbdn.rutgers.edu/`,
  a public, server-rendered **information site** (Joomla-style, jQuery). The
  **application** is `https://dn.rutgers.edu`, linked from that site, from CSP
  and from WebReg. There is also an advisor app at `dnadvisor.rutgers.edu`.
- **The app requires login immediately.** `https://dn.rutgers.edu` → 302 →
  `/Home.aspx?pageid=default` → 302 →
  `https://cas.rutgers.edu/login?renew=true&service=https://dn.rutgers.edu/Default.aspx/`.
  It is an ASP.NET application. The CAS page has a password field and says
  "You agree to the terms of use by logging in".
- **The public About page** (`/about`) says: "Browse the database and run an
  audit on any undergraduate program", "What if I change majors?", "View
  course descriptions and the prerequisites, co-requisites, and equivalencies",
  and "View all programs and requirements in any major".
- **The student FAQ** says: "Search Programs of Study … by department name or
  department number", and "officially change/add a major … contact your
  college's or school's dean's office".

**Established:** Degree Navigator holds exactly what CoursePilot lacks:
machine-evaluated requirement definitions for every undergraduate program,
co-requisites and equivalencies. None of it is reachable without a NetID
session, and no anonymous program-definition endpoint was observed.

**Not established:** whether it has an internal structured API, whether its
definitions are exportable, and what its terms of use say. All of these are
behind the login and were not investigated.

**Would CoursePilot gain information?** Yes: co-requisites, equivalencies and
the registrar's own encoding of requirements. But only through an authenticated
student session, which the project must not use for automated collection.

**Verdict:** not a primary or secondary source. It could be a **validation
reference used by a person**: a reviewer with their own account compares a
curated definition against Degree Navigator's display during REVIEW (§13). An
institutional data agreement with Rutgers would be the only legitimate route to
machine access. **Flagged for human review.**

## 5. Rutgers catalog findings

**Where program data lives.** Rutgers runs separate Coursedog catalogs, as
linked from the NB undergraduate site:

- **New Brunswick:** undergraduate and graduate;
- **Camden:** undergraduate and graduate;
- **Newark:** undergraduate and graduate;
- **Separate catalogs:** RBS, Health and Law;
- **Archive:** `scarlethub.rutgers.edu/registrar/course-catalog-archive/`.

This phase investigated **NB undergraduate only**.

**Host naming changes between years.** `newbrunswick-26-27-undergrad` becomes
`newbrunswick-25-26-undergrad-archive`. Hosts must be discovered, not derived
from the year, which the existing `sources/catalog.py` already documents.

**Discovery source.**

- **No usable index URLs.** The linked `/sitemap.xml` returns 404, and
  `/schools/<slug>` returns 404 (they are Nuxt "Page not found" pages).
- **The navigation tree is the index.** Every page's `__NUXT_DATA__` embeds
  the full site navigation: `group{label, slug, url, children}` →
  `link{label, slug, url, pageId, linkType}`. The discovery prototype walks it.

**What was found:**

| | 2026-2027 | 2025-2026 |
|---|---|---|
| navigation leaves | 516 | 459 |
| subject-coded program/department pages | **172** | 174 |
| distinct subject codes | 151 | |
| program-area pages without a code (degree requirements, options, credits and residency, …) | 137 | 96 |
| other pages | 207 | 189 |

**Per school, 2026-27 (coded / uncoded program-area pages):**

| school | coded | uncoded |
|---|---|---|
| SAS | 85 | 16 |
| RBS NB | 28 | 6 |
| SEBS | 22 | 65 |
| Mason Gross (mgsa) | 14 | 4 |
| Engineering | 11 | 13 |
| Pharmacy | 6 | 10 |
| SC&I (sci) | 6 | 0 |
| Bloustein (ejbsppp) | 0 | 12 |
| SMLR | 0 | 9 |
| Social Work (ssw) | 0 | 2 |
| Honors College | 0 | 0 |

**Label and structure conventions differ by school:**

- **Program labels:** SAS writes "Mathematics 640"; RBS writes "010 Accounting".
- **URL structure:** SAS uses `program-listing/`, SEBS `programs/programs-of-study`,
  SSW `degree-programs/`, and SMLR its own tree.
- **Where requirements live:** Engineering and SEBS keep degree requirements
  on separate pages. Sampled "Aerospace Engineering 021" and "Agricultural
  Business and Food Systems" quote no course codes at all.

**Identifiers:**

- **URLs:** 433 appear in both years; **430 keep the same `pageId`** and 3 do
  not. 83 URLs are new in 2026-27 and 26 were dropped.
- **Subject codes are not unique program identifiers.** 18 codes label two
  pages (e.g. 119, 189, 206).
- **A program is not a page.** A stable CoursePilot key needs school, program
  code, degree type and, where it exists, option (Phase 6.0 key plus option).

**Structure of requirement content.** Sampled 2 subject-coded pages per school
(14 pages, all HTTP 200):

- **0** program-specific structured requirement objects;
- **2** Coursedog field-configuration maps per page (identical everywhere);
- requirement wording on 9 of 14 pages; course codes quoted on 12.

Confirmed at scale: requirements are prose. This matches Phase 3's CS finding.

**Realistic automatic coverage:**

- **Discovery:** automatic, about 172 coded pages plus the uncoded program
  areas.
- **Prose location and archiving:** automatic.
- **Requirement structure:** **not** automatic. Every program needs a curated
  definition that quotes the prose (the Phase 6.0 Mathematics workflow).
- **Support:** requires human REVIEW.

A realistic first wave is SAS, whose 85 pages share one convention, then RBS.
Engineering and SEBS need page-set definitions, because their requirements are
spread over several pages.

## 6. SOC findings

**Endpoints.** Observed in the public SOC app's own JavaScript
(`soc_app.*.js`); nothing guessed:

| endpoint | used by CoursePilot | size | cache header | content |
|---|---|---|---|---|
| `/soc/api/courses.json?year&term&campus` | yes | 21.2 MB (gzip) | `max-age=900` | every course with sections, meetings, instructors |
| `/soc/api/openSections.json?year&term&campus` | **no** | 95.9 KB | **`max-age=30`** | JSON array of index strings |

**Fields.** Keys observed in a live record:

- **Course level:** `campusCode, campusLocations, coreCodes, courseDescription`
  (always empty), `courseFee, courseFeeDescr, courseNotes, courseNumber,
  courseString, credits, creditsObject, expandedTitle, level, mainCampus,
  offeringUnitCode, offeringUnitTitle, openSections, preReqNotes, school,
  sections, subject, subjectDescription, subjectGroupNotes, subjectNotes,
  supplementCode, synopsisUrl, title, unitNotes`.
- **Section level:** `campusCode, comments, commentsText, crossListedSectionType,
  crossListedSections, examCode, finalExam, honorPrograms, index, instructors,
  legendKey, majors, minors, meetingTimes, number, openStatus, openStatusText,
  openToText, sectionCampusLocations, sectionCourseType, sectionEligibility,
  sectionNotes, sessionDates, specialPermissionAdd/DropCode(+Description),
  subtitle, subtopic, unitMajors`.
- **Meeting level:** `buildingCode, campusAbbrev, campusLocation, campusName,
  startTime/endTime(+Military), meetingDay, meetingModeCode, meetingModeDesc,
  pmCode, roomNumber, baClassHours`.

**Not used by CoursePilot:**

- `openSections.json`;
- section `majors` / `minors` / `unitMajors`, which are structured
  registration restrictions (e.g. 01:198:344 lists majors 198, 185, …);
- `honorPrograms`, `sessionDates`, `courseNotes` and `subjectNotes`
  (e.g. "A grade below a 'C' in a prerequisite course will not satisfy
  prereq");
- parsing of `preReqNotes`.

**Static versus volatile, measured.** Fall 2026, live on 2026-09-29 against the
2026-09-11 archive:

- 12,010 sections, compared with 11,992: **41 added and 23 removed**;
- **1,094 open/closed flips** (811 opened, 283 closed) in 18 days.

**`openSections.json` semantics:**

- every section with `openStatus: true` is present (8,546, with 0 missing);
- **3,437 indexes are not in the NB `courses.json`**, and their origin is
  unresolved. Consumers must join by index against the same term's
  `courses.json` and ignore unknown indexes.

**Suggested cadence** (conceptual, not deployed):

- `courses.json`, per active term: daily, and every 1 to 3 hours during
  registration windows. Its own header says 15 minutes is the freshest
  meaningful interval.
- `openSections.json`: no faster than its 30 s `max-age`. A 60 to 120 s
  cadence for terms with watchers is a responsible default, and needs
  human sign-off on rate before deployment.

## 7. CSP findings

**Evidence:** `https://sims.rutgers.edu/csp/` returns 200 with the title
"Login - Course Schedule Planner - Rutgers University". It shows "Continue to
NetID Login" and loads only `dojo.js`, with no forms or data endpoints before
login. The page says CSP was built by OIT, LCSR and the Registrar and lists:
"Automated schedule generation, Calendar & list view, Advanced filtering,
Wish List, Course Catalog search, WebReg integration, Re-planning during
registration, Degree Navigator integration". It also warns: "CSP calculates
many complex algorithms and will perform best on newer PCs", which suggests
generation happens **client-side**. That is inference, not verified.

**Established:** CSP requires authentication, and no public data interface
exists outside SOC's.

**Not established:** its internal APIs, which are behind the login and were
not investigated.

**Verdict:** CoursePilot does not need CSP's data. Every schedulable fact
CSP could display (sections, index, meetings, mode, campus, instructors, open
status) is published by SOC anonymously and is already stored. Build
CSP-like scheduling in a CoursePilot Schedule Engine over SOC data. Integrating
CSP would add an authenticated dependency for no unique data.

## 8. WebReg findings

**Evidence:**

- `http://webreg.rutgers.edu` → 302 → `https://sims.rutgers.edu/webreg/`,
  titled "WebReg | Home Page". Login is by NetID, or RUID and PAC.
- Posted hours: "Monday–Sunday 12:00 AM - 1:59 AM, 6:00 AM - 11:59 PM".
- **"The use of automated software for registration is prohibited. If
  detected, the student's online registration privileges will be suspended."**

**Public, non-student-specific information on the page:** hours of operation
and links to per-campus registration schedules.

**Authenticated and student-specific:** add/drop, registration eligibility,
holds, time tickets and current registrations. All of it is out of scope and
must not be automated.

**Does CoursePilot need direct integration?** No:

- live section availability comes from SOC `openSections.json`;
- registration windows are published on registrar pages linked from WebReg
  (a static, per-term source; not yet investigated);
- the acceptable registration assist is a **hand-off**: show the student the
  index numbers of their chosen schedule, and link them to WebReg to register
  themselves.

## 9. Source authority matrix

Key: ✓ = observed publishes. ◐ = partial or unstructured. 🔒 = behind
authentication (claimed, not observed). ✗ = not provided. ? = not
investigated.

| data | CoursePilot today | Catalog | DegreeNav | SOC | CSP | WebReg | **Authority** | why |
|---|---|---|---|---|---|---|---|---|
| program existence | curated JSON | ✓ nav tree | 🔒 | ✗ | ✗ | ✗ | **Catalog** | public and versioned; nav enumerates it |
| program title | curated | ✓ | 🔒 | ✗ | ✗ | ✗ | **Catalog** | |
| school | curated | ✓ (nav group) | 🔒 | ◐ `school` per course | ✗ | ✗ | **Catalog** | a program's school is a catalog fact |
| degree type | curated | ◐ prose | 🔒 | ✗ | ✗ | ✗ | **Catalog** (curated) | stated in prose, not fields |
| catalog year | curated | ✓ (host) | 🔒 | ✗ | ✗ | ✗ | **Catalog** | |
| degree requirements | curated from prose | ◐ prose | 🔒 structured | ✗ | ✗ | ✗ | **Catalog prose → reviewed curation** | the only public authoritative text |
| requirement source text | `source_prose` | ✓ | 🔒 | ✗ | ✗ | ✗ | **Catalog** (archived) | |
| admission to major | not modeled | ◐ prose | 🔒 | ✗ | ✗ | ✗ | **Catalog** (curated, policy layer) | |
| minimum grades | not evaluated | ◐ prose | 🔒 | ◐ courseNotes | ✗ | ✗ | **Catalog** | |
| GPA requirements | not modeled | ◐ prose | 🔒 | ✗ | ✗ | 🔒 | **Catalog** | |
| minors | none | ✓ pages | 🔒 | ◐ section minor restrictions | ✗ | ✗ | **Catalog** | |
| options/concentrations | none | ◐ prose | 🔒 | ✗ | ✗ | ✗ | **Catalog** | |
| course identity | SOC | ◐ code text | 🔒 | ✓ | ? | ? | **SOC** | structured and complete per term |
| course title | SOC | ✓ | 🔒 | ✓ | ? | ? | **SOC** | display from SOC; catalog title is versioned context |
| description | catalog | ✓ | 🔒 | ✗ (empty) | ? | ✗ | **Catalog** | SOC publishes none |
| credits | SOC + catalog | ✓ ranges | 🔒 | ✓ | ? | ? | **SOC per term**; catalog range kept as context | per-term fact; never overwrite either |
| prerequisites | raw text | ◐ prose | 🔒 | ✓ expression (75%) | ? | ? | **SOC** | structured, term-scoped |
| corequisites | none | ◐ prose | 🔒 | ✗ | ? | ✗ | **Catalog** (curated) | DN is the only structured holder, and is authenticated |
| equivalencies | none | ◐ | 🔒 | ✗ | ? | ✗ | **Catalog / registrar** (curated); open | |
| offerings | SOC | ✗ | 🔒 | ✓ | ? | ? | **SOC** | |
| sections, index | SOC | ✗ | ✗ | ✓ | 🔒 | 🔒 | **SOC** | |
| instructors | SOC | ✗ | ✗ | ✓ | 🔒 | 🔒 | **SOC** | |
| meeting times, campus | SOC | ✗ | ✗ | ✓ | 🔒 | 🔒 | **SOC** | |
| section availability | snapshot | ✗ | ✗ | ✓ `openSections.json` | 🔒 | 🔒 | **SOC openSections** | public, 30 s cache, designed for it |
| registration restrictions | text | ✗ | ✗ | ✓ majors/minors, SP codes | 🔒 | 🔒 | **SOC** (display); WebReg enforces | |
| student registration state | none | ✗ | 🔒 | ✗ | 🔒 | 🔒 | **Student-entered** or never | authenticated; not ours to scrape |

**No silent overwrite.** Each field has exactly one owning source adapter.
Where two sources publish the same fact (credits: SOC `4` vs catalog `3-4`;
title: SOC vs catalog), both are stored in their own columns with provenance
and never merged into one. A disagreement is recorded as a data-quality
finding, surfaced to review, and never auto-resolved.

## 10. Static, term-scoped, live and student-specific data

| class | examples | CoursePilot should |
|---|---|---|
| **static / versioned** | requirements, program structure, catalog descriptions, catalog years, source prose | persist permanently, version by catalog year, archive raw source, never overwrite history |
| **term-scoped** | offerings, sections, index numbers, meetings, instructors, prerequisites, restrictions | persist per term, refresh on a schedule, keep prior terms (availability history for planning) |
| **live / volatile** | open/closed status, seat counts, a section appearing or disappearing | query on a short cadence, keep the **latest value plus change events** (not a full history table), never serve it as if permanent |
| **student-specific** | completed courses, registrations, holds, eligibility | only what the student provides or links; **never** scraped from Rutgers systems; never shared across accounts |

## 11. Requirement IR gap analysis

Primitives measured against real Rutgers prose:

| primitive | real example | status |
|---|---|---|
| required course | "01:198:111" (CS) | **supported** (`course`) |
| one-of | "01:640:244 or 01:640:252" | **supported** (`choose_n` 1) |
| N-of | "five electives" | **supported** (`choose_n`) |
| nested AND/OR | CS core `all_of`; B.S. "physics OR chemistry" | **supported** (`all_of`, `any_of`, tree) |
| minimum credits | SAS Core CORE_NS | **supported** (`credits`) |
| course-level constraint | "at least two at the 300 level" | **supported** (`min_at_level*`) |
| subject constraint | "at most two outside CS" | **supported** (`max_outside_subject`) |
| ranged elective pool with exclusions | "300–400 level, excluding 491, 492" | **supported** (6.0 query keys) |
| category coverage | "one analysis and one algebra"; SAS AH | **supported** (`min_distinct_categories`) |
| exclusions | CS "no credit for 105, 107, …" | **supported** (`course_exclusion` rule) |
| residency | "four at Rutgers-NB" | **represented, not evaluable** (no transfer provenance) |
| per-course minimum grade | Math "250, 251, 252 … C or better" | **schema extension required**: `min_grade` column exists, 0 rows populated, engine ignores it |
| grade quota ("all but one C or better") | Math Option A | **schema extension required**: grade rule scoped to one requirement, not the whole record |
| minimum GPA | biomathematics honors 3.4; major GPA rules | **schema extension required** (GPA over a requirement's allocation) |
| sequences ("411-412 as a pair") | Math | **requires policy** (ambiguous prose; representable as `all_of` inside `any_of` once decided) |
| options/concentrations | Math Options A/B/C | **representable** as separate Program/degree keys or an option dimension; **requires a keying decision** |
| minors | Math minor | **representable** (Program `degree_type='minor'`); the engine audits one version per call |
| double majors / cross-program sharing | "Double Majors" pages (SEBS, RBS) | **requires policy** plus multi-version evaluation |
| equivalencies / substitutions | "14:332:252 may be substituted"; DN equivalencies | **partly representable** (list both courses); general equivalence needs **schema** |
| repeated courses | any retake | **bug**: counts twice (§1.9a); needs a rule ("best attempt counts once") |
| admission to major | "three semesters of calculus with C or better" | **should not be in the Degree Engine**; a separate eligibility layer |
| prerequisites | SOC `preReqNotes` | **should not be in the Degree Engine**; it belongs to the Planning Engine's prerequisite graph |
| placement ("Any Course EQUAL or GREATER Than") | 01:750:115 | **requires policy**; a planning concern |

## 12. Rutgers-wide program discovery results

Lifecycle, never collapsed:

```
DISCOVERED  navigation leaf exists               516 pages / 172 coded (NB UG 26-27)
FETCHED     page archived                        14 sampled + 4 CS/Math pages
PARSED      requirement prose located            9 of 14 sampled
VALIDATED   curated JSON passes loader + verbatim-quote test    2 (CS, Mathematics)
REVIEWED    a person verified it against the archive            1 (CS)
PUBLISHED   support_status == supported                          1 (CS)
```

The lifecycle maps onto existing fields: VALIDATED and REVIEWED correspond to
`curation_status` `unverified` → `curated_from_prose`, and PUBLISHED is the
derived `support_status`. The first three stages need a small discovery
registry, not new engine code. A discovered page must never appear in
`/programs`.

## 13. Provenance and verification design

**Already exists:**

- `data_source`: kind, URL, `retrieved_at`, `content_hash`, academic year,
  archive path, record count;
- `source_prose` and `curation_status` on every requirement, rule and version;
- `source_url` on each version;
- the raw archive;
- the verbatim-quote test (Mathematics).

**Missing:**

- `extractor_version` (which curation or parser produced it);
- `reviewed_by` and `reviewed_at`;
- `last_checked_at` (when the source was last re-fetched and still matched);
- `source_page_id` (Coursedog `pageId`, stable across years);
- a link from each definition to the archive hash it quotes. The Mathematics
  hash lives only in a README string.

**Proposal:** extend `data_source` and `program_version`, not every
requirement row:

- a `program_version` → `data_source` link with the archive sha256 and `pageId`;
- `reviewed_by` / `reviewed_at` on `program_version`;
- a periodic "source still matches?" check, which re-fetches, hashes and
  compares, and downgrades to `pending_review` on change.

## 14. Planning Engine readiness

| dependency | status | evidence |
|---|---|---|
| remaining requirements | **READY** | `DegreeAuditResult` statuses and counts |
| eligible courses per requirement | **READY** | `eligible_not_allocated`, `requirement_course_option` |
| completed / current courses | **READY** | `student_course` with status |
| credits | **READY** | engine credit accounting |
| catalog year | **READY** | binding plus versions |
| course exclusions | **READY** | `course_exclusion` rule |
| prerequisite graph | **MISSING** | raw text only; 75% parseable; 1,834 of 5,497 references point at courses not in the DB |
| corequisites | **MISSING** | not published in structured form publicly |
| availability by semester | **PARTIAL** | 1 full term loaded (5 archived); no multi-term history |
| repeated-course behaviour | **BLOCKED (bug)** | double counting (§1.9a) |
| min grades for prerequisites | **MISSING** | `courseNotes` "below a 'C' … will not satisfy prereq" is prose |

**Overall: PARTIAL.** A planner built today would recommend courses whose
prerequisites the student lacks.

## 15. Schedule Engine readiness

| dependency | status | evidence |
|---|---|---|
| sections, index numbers | **READY** | 11,992 Fall 2026 |
| meeting times | **READY** | 17,678 meetings, military times |
| campus / building | **READY** | per meeting |
| instructors | **READY** | 12,179 rows |
| asynchronous / online / hybrid | **READY** | `meeting_mode_code` / `_desc` |
| cross-listed sections | **READY** | 974 rows |
| open/closed state | **PARTIAL** | stored snapshot is stale; live source identified, not integrated |
| conflict detection | **MISSING** | no code |
| travel / campus constraints | **MISSING** | needs a campus-travel policy (bus times are not in SOC) |
| user preferences | **MISSING** | no model |
| registration restrictions | **PARTIAL** | text plus unstored structured majors/minors |

**Overall: PARTIAL, but the data is essentially ready.** What's missing is
engine code, not data.

## 16. Notification data strategy

| event | detectable from | how |
|---|---|---|
| a section opened or closed | **SOC `openSections.json`** | diff the index set per poll, joined to `courses.json` |
| a new section appeared or was removed | SOC `courses.json` | diff indexes per refresh |
| meeting time, room or instructor changed | SOC `courses.json` | diff meeting and instructor rows |
| registration period approaching | registrar schedule pages (public; linked from WebReg; not yet investigated) | static per-term calendar |
| student eligible, registered, holds | authenticated only | **not detectable; do not attempt** |

**Monitor `openSections.json`, never WebReg.** Watch only (term, index) pairs
that some student's plan cares about. Emit change events into a table; push
fan-out reads those events. Each poll is about 96 KB, whatever the number of
watchers.

## 17. Security and access boundaries

| source | classification |
|---|---|
| catalog (Coursedog pages) | **public**; rate-sensitive (about 270 KB per page, and every page embeds the full navigation) |
| SOC `courses.json` | **public**, heavy (21 MB); `max-age=900` |
| SOC `openSections.json` | **public**, light; `max-age=30` (polling-friendly, but rate still needs human sign-off) |
| Degree Navigator app | **authenticated** (CAS `renew=true`); terms accepted at login |
| Degree Navigator info site | public, informational only |
| CSP | **authenticated** (NetID) |
| WebReg | **authenticated**, **explicitly prohibits automated registration** |
| registrar calendars | public (linked); not investigated |

**Rules for every future phase:**

- no credential collection;
- no stored Rutgers cookies or tokens;
- no CAS automation;
- no bypassing rate or anti-bot controls.

**Flagged for human review:**

1. The terms of use for public catalog and SOC access at scale. No
   robots or terms text was reviewed in this phase.
2. Whether an institutional data agreement with Rutgers (Registrar or OIT)
   could provide Degree Navigator program definitions properly.
3. An acceptable `openSections.json` polling rate.

## 18. Recommended architecture

```
Rutgers sources (public only)
  Catalog (Coursedog pages) ── SOC courses.json ── SOC openSections.json ── registrar calendars
        │                              │                     │
   Source adapters: one per source, each owns its fields (no cross-source overwrite)
        │                              │                     │
   Raw archive (hash, retrieved_at, pageId)        Live poller (latest state + change events)
        │                              │                     │
   Normalize ─ Validate            Normalize ─ Validate        │
        │                              │                     │
   Versioned program data       Term-scoped course data      Live availability
   (DISCOVERED→…→PUBLISHED,     (offerings, sections,        (open status, events)
    human REVIEW gate)           meetings, prereq graph)
        └──────────────┬───────────────┘                     │
                 Degree Engine  ──>  Planning Engine  ──>  Schedule Engine
                 (what remains)     (what next; prereqs)  (which sections; conflicts)
                                                          │
                                               Notification service (events → push)
                                                          │
                                         CoursePilot API (one contract)
                                                          │
                                                  Web · Mobile
```

This differs from the brief's draft in three ways:

- **Live availability is a separate lane.** It has its own store and change
  events and is never folded into the versioned data.
- **Program data has an explicit human REVIEW gate** before publication.
- **Degree Navigator, CSP and WebReg are absent** as sources. WebReg appears
  only as a hand-off link.

## 19. Recommended next phases

### Phase 6.2 — Prerequisite graph from SOC, and the retake fix

- **Objective:** turn `preReqNotes` into a structured, term-scoped
  prerequisite expression, and make a repeated course count once.
- **Dependencies:** existing SOC archives; the audit engine.
- **Deliverables:**
  - a grammar parser for the 75% of pure expressions, with the rest
    classified and stored raw (never guessed);
  - a `prerequisite` table (course × term → expression tree);
  - course identities for referenced but unloaded courses;
  - load all 5 archived terms;
  - the retake rule ("best passing attempt counts once") with an
    `AUDIT_ENGINE_VERSION` bump.
- **Risks:** placement-style forms; codes from other campuses; prerequisites
  changing between terms.
- **Definition of done:** at least 75% of strings parse to trees whose
  canonical reprint round-trips; the rest are recorded with reasons; a
  retaken course fills one slot; the full suite is green.

### Phase 6.3 — Program discovery registry and SAS curation wave

- **Objective:** make "add a major" a reviewed data operation at scale.
- **Dependencies:** the 6.1 discovery prototype; 6.0 loaders.
- **Deliverables:**
  - a `catalog_page` registry (pageId, URL, year, school, class,
    archive hash, lifecycle state);
  - prose-location tooling;
  - provenance fields (§13);
  - a review workflow;
  - human review of the Mathematics definition;
  - a first curation wave of about 10 SAS majors with contrasting structure.
- **Risks:** curation throughput is human-bound; option and minor keying
  decisions.
- **Definition of done:** 10 or more programs VALIDATED, a named share
  REVIEWED, discovery re-runnable per year, and no unreviewed program shown as
  supported.

### Phase 6.4 — Requirement primitives: grades, GPA, options

- **Objective:** add the schema extensions from §11 without engine branching.
- **Dependencies:** 6.3 (real programs that need them).
- **Deliverables:**
  - evaluated per-option `min_grade`;
  - requirement-scoped grade quotas;
  - GPA rules;
  - an option dimension on program keys;
  - a sequence representation, after a policy decision.
- **Risks:** Rutgers grade semantics (C vs C+); ambiguous prose.
- **Definition of done:** the Mathematics grade rules move from "not modeled"
  to evaluated, and a test proves no program-specific branches were added.

### Phase 6.5 — Deterministic Planning Engine (v1)

- **Objective:** answer "what could I take next?" with only
  prerequisite-satisfiable courses that advance remaining requirements.
- **Dependencies:** 6.2 (hard); 6.3 and 6.4 (breadth).
- **Deliverables:**
  - candidate generation from `eligible_not_allocated` plus the prerequisite
    graph plus term availability;
  - deterministic ordering, with no ranking of majors;
  - a read-only API.
- **Risks:** combinatorial explosion across terms; incomplete prerequisites.
- **Definition of done:** it never recommends a course whose prerequisites are
  unmet, and every recommendation cites its requirement and prerequisite
  evidence.

### Phase 6.6 — Schedule Engine and live availability lane

- **Objective:** build CSP-like schedules from SOC, and watch open status.
- **Dependencies:** SOC sections (ready); 6.5 for course sets.
- **Deliverables:**
  - conflict detection and schedule enumeration;
  - an `openSections.json` poller with change events (rate approved by a
    human);
  - a WebReg hand-off link carrying index numbers.
- **Risks:** polling ethics and rate; travel-time policy.
- **Definition of done:** generated schedules are conflict-free against real
  meetings; open/closed events reproduce from recorded polls.

Web deployment, the mobile app and push notifications follow 6.6. They
consume the same API and the 6.6 event store.
