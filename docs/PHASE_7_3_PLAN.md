# Phase 7.3 plan: CSP, Degree Navigator and WebReg as sources

Every Phase 7.3 prompt reads this file first. Status: **plan and discovery
only** - no production code is written until the discovery report is reviewed.

---

## 1. Decision record

**Date:** 2026-10-22 · **Decided by:** the project owner · **Status:** revised
the same day after discovery (§1a). The "never automate / never scrape an
authenticated Rutgers session" rule in `AUTHENTICATION.md` **stands
unchanged**.

**Context.** Rutgers students plan in **Course Schedule Planner (CSP)**,
register in **WebReg**, and track progress in **Degree Navigator (DN)**.
CoursePilot is only useful if it agrees with what students see in those tools.

**Decision.**

| Data | Preferred source | How it reaches CoursePilot | Fallback |
|---|---|---|---|
| Section data (term-scoped, identical for every student) | **SOC** (existing public pipeline) - see §1a | Unchanged | - |
| A student's requirement status | **DN audit** | The student uploads a DN audit **PDF**; staged, reviewed, confirmed | CoursePilot's own Degree Engine |
| What the student is taking now | **WebReg schedule PDF** | The student uploads it (7.2.2 importer); staged, reviewed, confirmed | Manual section confirmation |

The combined data view keeps every field's provenance. Where sources
disagree, the disagreement is recorded and surfaced (the 7.2.4 rule) - never
silently resolved. CoursePilot's Degree Engine stays CoursePilot's engine: a
confirmed DN status is a **student-provided comparison and status record**,
not a replacement for the deterministic audit.

> **Revised 2026-10-24 (Phase 7.4, §10 decision 5).** Once the student
> confirms a DN audit, DN's status **drives planning** for every *mapped*
> requirement - DN reflects exceptions and equivalencies CoursePilot cannot
> see. It decides a requirement's STATUS only; it never changes a CoursePilot
> requirement rule. CoursePilot's own audit stays the cross-check, and every
> disagreement is shown as NEEDS REVIEW.

### 1a. Revision after discovery (2026-10-22): CSP is not adopted

Discovery compared **962 Spring 2027 sections** captured from CSP against
public SOC `courses.json` and found **0 semantic disagreements** on every
shared field (`investigations/phase-7-3-csp-dn.md` §1). CSP adds only
presentational extras (building numbers, by-arrangement hours, major names)
and lacks SOC's minor and unit-major restrictions.

**Decision (owner):** SOC remains the primary and only source for section
data. CSP is **out** - no collector, no saved CSP session in use - unless a
later need for one of its extra fields justifies revisiting this. The
proposed amendments to `AUTHENTICATION.md` and `DATA_SOURCES.md` are **not
approved**; both documents stay as they are. The discovery tooling
(`scripts/discover_csp.py`) remains for re-checking parity only.

DN and WebReg data still enter only as student uploads (trust boundary 3).

---

## 2. Trust boundaries (non-negotiable for every later prompt)

1. **The CoursePilot server never receives, stores or proxies** any student's
   NetID password, Duo approval or Rutgers session cookie. Nothing in the
   product asks a student to sign in to Rutgers through CoursePilot.
2. **No CSP collector exists (§1a).** Discovery re-checks only: manual,
   headed sign-in by the developer (Duo never automated), recording only
   after sign-in, files only under `COURSEPILOT_PRIVATE_DIR` (outside
   OneDrive, gitignored). A saved CSP session is never used for automated
   collection and never reaches the CoursePilot server, database, logs or Git.
3. **Per-student data (DN, WebReg) enters only as a document the student
   uploads (PDF)**: extraction → staging → student review → explicit
   confirmation, mirroring `services/transcripts/`. A document never writes
   history directly; original extracted values are kept and never overwritten.
4. **Polite client**: an honest User-Agent; at most one CSP request every
   2-3 s; no parallel sessions; cache everything; **never collect during
   registration windows**. Rutgers' acceptable-use policy is recorded in §4.
5. **Existing ingestor rules still apply**: archive raw data before parsing;
   never fabricate; term-scoped; idempotent; versioned; `(term_code,
   index_number)` is the section key; unknown is reported as unknown.
6. **Committed fixtures are synthetic**: real layout, invented courses, no real
   identifiers. Real exports stay in `data/private/`.
7. **`.gitignore` covers `data/private/`, `*.har`, `*state*.json`**, and a
   pre-commit check (`scripts/hooks/pre-commit`) fails if any are staged.

---

## 3. Glossary

| Term | Meaning |
|---|---|
| **track** | A program plus its variant/option: `sas-198-ba`, `sas-640-ba-option-a` (the program natural key, `services/programs.py`). |
| **school Core** | The Core system a student's school requires. SAS Core is the only one encoded today (`sas-core.json`, a `SchoolRequirementSetVersion`). |
| **combined data view** | The field-by-field merge of all sources (CSP, SOC, live availability, confirmed student documents), each field carrying its source, timestamp and version. |
| **progress record** | Confirmed academic history plus confirmed DN requirement status. |
| **current schedule** | The student's confirmed in-progress sections (WebReg import or manual confirmation). |

---

## 4. Rutgers acceptable-use policy (found and recorded)

**Rutgers Policy 70.1.1, Acceptable Use Policy for Information Technology
Resources** - adopted 02/01/2000, last reviewed 04/06/2021 (official copy:
policies.rutgers.edu). Clauses that bear on this plan, verbatim:

- iii. "Each user may use only those information technology resources for
  which he or she has authorization." Violations include "sharing passwords or
  log-in IDs. Users are responsible for any activity conducted with their
  computer accounts" and "using resources without specific authorization".
- iv. Resources "must be used only for their intended purpose(s) relating to
  University business." Violations include "using University computing or
  network resources for private advertising or other private commercial
  purposes", "circumventing, disabling, or attempting to circumvent or disable
  security mechanisms without authorization" and "making University systems
  and resources available to those not affiliated with the University".
- "using third party cloud storage or data sharing tools (i.e., iCloud,
  Carbonite, Dropbox) to store University information classified as Critical or
  Restricted without prior OIT approval."

**What it does not say.** It has no explicit clause on scripts or automated
browsing of a system the user is authorized to use.

**Risks this plan carries (the owner's decision, recorded here):**

| Risk | Why | Mitigation in this plan |
|---|---|---|
| A CSP collector republishes data obtained through an authenticated session to other users | iv ("making University systems and resources available to those not affiliated…") and iii ("specific authorization") | Developer-only, cached, polite; SOC remains the fallback. **Recommendation:** ask OIT / the Registrar for written permission before the collector runs on a schedule or serves production users. |
| `data/private/` sits inside **OneDrive** | The repo is under OneDrive, so `data/private/` (session state, HARs, DN/WebReg PDFs - a student record) syncs to a third-party cloud | Prefer an out-of-OneDrive location via `COURSEPILOT_PRIVATE_DIR`, or exclude the folder from sync. **Owner to decide.** |
| HAR files contain cookies and tokens | A HAR records every request header | `*.har` gitignored + pre-commit check; analysis tools read URLs, status, content type and sizes only, never header or cookie values. |

---

## 5. Changes to earlier documents - NOT APPROVED (§1a)

These amendments were drafted, then **declined by the owner** after
discovery. `AUTHENTICATION.md` and `DATA_SOURCES.md` stay unchanged. Kept here
only as the record of what was considered:

- `AUTHENTICATION.md` "CoursePilot never" - to be narrowed: the **server**
  still never asks for, stores or proxies a Rutgers credential and never
  automates a sign-in; the developer-only CSP collector (trust boundary 2)
  would be the single documented exception to "scrapes an authenticated
  Rutgers session".
- `DATA_SOURCES.md` - CSP and DN entries to point here.
- `SOURCE_AUTHORITY.md` (7.2.4) - CSP and DN rows to be updated once the
  discovery results (`docs/investigations/phase-7-3-csp-dn.md`) are accepted.

Discovery evidence relevant to that decision (§1 of the investigation): in a
962-section sample CSP matched public SOC on every shared field and added only
presentational extras, while lacking SOC's minor/unit restrictions.

---

## 5a. Upload guidance for students (DN audit and WebReg schedule)

Both documents must be saved with the **browser's own "Save as PDF"**
(Chrome: Print → Destination "Save as PDF"). **Do not use "Microsoft Print
to PDF"**: it draws every character as a shape, so the file has no text layer
and CoursePilot cannot read it (no OCR - verified on a real WebReg print,
7.2.4). The importers refuse such files with a message saying exactly this.

## 6. Discovery steps (this prompt)

1. This plan, the gitignore and the pre-commit check.
2. `scripts/discover_csp.py`, `scripts/discover_dn.py`: headed Playwright, the
   developer logs in by hand, the session is saved, every response goes to a HAR
   in `data/private/har/` while the developer clicks through. Findings go to
   `docs/investigations/phase-7-3-csp-dn.md`: endpoints, CSP-vs-SOC parity on
   30+ sections, session lifetime.
3. DN audit and WebReg PDF layout investigation on the developer's own
   exports; synthetic fixture PDFs that copy each layout with invented data.
4. Stop and report. No production code.

## 7. Known issues

### 7a. Plan generation time (recorded 2026-10-22, not optimized yet)

p50 of `scripts/benchmark_planning.py 3 20279` on the development data, after
the latest-term rules, section-constraint and placement changes (one machine,
measured back to back with the previous code):

| Case | p50 | Over target |
|---|---|---|
| small (dev student, CS) | 2.6 s | - |
| what-if sas-220 | 4.0 s | yes |
| what-if sas-640 | 4.5 s | yes |
| large sas-198 (nothing taken) | 7.1 s | yes |
| large sas-640 (nothing taken) | 8.3 s | yes |

`plan_quality.py` "nothing yet" plans: 7.1-9.6 s. **Target: under 3 s p50
for every case.** Known contributors: prerequisite rows for 24 loaded terms,
one `check_proposal` pair per distinct rules term per planning term, and
longer plans now that the math chains are no longer blocked. Not optimized
in Phase 7.3 by decision; plans stay deterministic (identical across runs).

## 8. Workload levels, fillers and pins (2026-10-23)

Decisions by the product owner: Pace stays separate as the GRADUATION TARGET
("When do you want to graduate?" - On time 8 terms / Early 7 / No rush 12;
stored values unchanged) and only sets the horizon; the WORKLOAD LEVEL sets
each term's load. The proposed formula and budgets were accepted as written.

| level | credits | workload budget |
|---|---|---|
| Light | 12-14 | 16 |
| Balanced (default) | 14-17 | 20 |
| Semi-rigorous | 16-19 | 24 |
| Challenging | 17-21 | 28 |

`weight = credits + 0.25 x weekly contact hours above credits + 1 lab +
0.5 x (level - 1) + 1 technical` (major subject, 01:640, or SAS Core NS), from
the term's own SOC meetings when published, else the course's latest loaded
term (`basis_term`). Every component is shown; no "easy/hard" wording
(app.services.planning.workload).

Each term, re-validated by the Degree Engine at every step:
anchors (pins, then needed retakes; may exceed the budget, and the term says
so) -> multi-requirement courses (2+ open requirements, within the audit's
sharing policy) -> ONE major-subject course that counts toward the major ->
fillers one at a time against the term as it stands: preferred delivery mode
(SOC meeting mode: asynchronous = mode 90 with no day/time), then one that
reaches the credit floor within budget, then the lowest weight. A lab joins
its lecture with the lecture's role. Fillers stop at the floor; nothing goes
past the level's maximum or (except anchors) the budget.

Pins are hard constraints: placed in their term or nowhere, never pruned.
Impossible pins are explained (not listed for the term, prerequisites unmet,
already completed, every section restricted). A pinned course whose
prerequisites CoursePilot cannot verify is placed and flagged
(`PIN_PREREQUISITES_UNVERIFIED`); a pinned course the program excludes is
placed with `PIN_WILL_NOT_COUNT` (e.g. 01:198:142 Data 101 for CS).

If the level cannot fit the graduation target, the plan says so
(`WORKLOAD_VS_GRADUATION_TARGET`) with fixes; it never extends the horizon.
Profiles confirmed before 7.3 have no level and keep the 7.2.1 behaviour until
the student chooses one (onboarding and the preferences form preselect
Balanced).

Changes this needed elsewhere, each decided or recorded:

* "Any Course EQUAL or GREATER Than: (X)" (553 rows, 72 courses incl. Gen
  Physics 203/205, Linear Algebra 250, CS 111) is now read LITERALLY - met by
  a passed course of the same unit and subject numbered >= X (decision of
  2026-10-23; prerequisite parser v2). Before, these were never planned.
* CS B.S. encoded from the catalog prose (`sas-198-bs.json`, pending human
  review; review packet generated): seven electives and a physics or
  chemistry sequence.
* An ANY_OF with no progress no longer opens only its alphabetically first
  alternative ("CHEM" sorted before "PHYS").

Scenario test (`ingestion/tests/test_workload_levels.py`, real public SOC
records): a CS B.S. student who failed Calc II, Semi-rigorous, prefer
flexible -> Spring 2027 = Calc II (anchor, retake) + Gen Physics 203 with
lab 205 (Core NS and the B.S. science) + 01:198:211 (major) + 01:810:135
(asynchronous, Core AHq) = 16 credits, workload 22.0 of 24.

## 9. Graduation target from the entry term (2026-10-24)

The graduation target counts Fall/Spring semesters from the student's ENTRY term - the first
Rutgers semester on record, else the first planned semester: On time 8, Early 7, No rush 10.
An optional expected graduation term (onboarding, preferences) overrides it. The plan ends at
that term (summer/winter included only if the student allows them) and states the basis; when
the remaining requirements do not fit, `GRADUATION_TARGET_NOT_MET` says so with fixes and the
plan is never extended. Example: entered Fall 2024 (class of 2028), planning from Spring 2027
-> Spring 2027, Fall 2027, Spring 2028. Stateless requests (no pace) keep the plain horizon.

## 10. Phase 7.4: Degree Navigator PDF import - decisions (2026-10-24)

Answers to the Step 4 open questions, recorded as given by the product owner
(they override Prompt 03 where the two conflict). Implementation:
`app.services.degree_audit`, DATA_MODEL section 51.

1. **Mapping DN requirements to CoursePilot nodes.** A reviewed mapping table
   per track and catalog year:
   `data/programs/rutgers/nb-undergrad/<year>/dn-mapping/<program-key>.json`.
   It maps DN `Requirement R<n> : Title [CODE]` (exact printed heading) to a
   CoursePilot requirement node id, each with a `review_status`. The CS BA
   and SAS Core tables are to be built from the developer's real audit. Names
   are never matched. A DN requirement with no entry is `unmapped` - kept and
   shown, never guessed.
2. **"In Progress or Planned"** is provisional and split deterministically.
   If every course behind it is in the student's confirmed current schedule
   for the current term, it is **in progress** (satisfied-if-passed for
   next-term prerequisites). Otherwise it is **planned**, which counts as
   not satisfied. The status shown says which rule decided.
3. **Transfer/AP and exceptions** are not supported yet. A row the parser
   cannot classify is `needs_review` with its raw text kept. No format is
   invented; one is added when a real audit containing them is available.
4. **Several degrees in one audit.** The student picks which degree block to
   import. Its heading/program code is checked against their CoursePilot
   track. A mismatch blocks confirmation ("this audit is for X, your
   CoursePilot track is Y"). Each block can be imported separately (what-if).
5. **A confirmed DN status drives planning** for mapped requirements; this
   reverses the "comparison record only" stance (§1). CoursePilot's own audit
   is the cross-check. Disagreements are shown on the Degree page (per
   requirement), in the plan's reasons, and in an anonymous developer report
   for encoding review. Unmapped requirements use CoursePilot's audit,
   labelled "estimated".
6. **Freshness.** The audit's "as of" is the Chrome print timestamp in the
   PDF header, else the upload time. It is stale if it predates the last
   term's final grades or is older than 30 days. A newly confirmed import
   marks saved plans out of date.
7. **Core goal codes** map through the same reviewed table. Every DN goal
   code is checked against `sas-core.json`; one that does not match a node
   one-to-one stays `unmapped` until reviewed.
8. **Retention** follows the transcript import's staging, expiry and cleanup
   rules exactly. The original PDF is deleted after confirmation or expiry,
   leaving only extracted rows and the file hash. Grades are never logged.

Parsing notes from discovery that the importer handles:

- **Status icons:** status is an icon image, classified only by comparison
  with DN's legend images *in the same PDF*. An icon that matches nothing is
  `unknown` and goes to review.
- **Two courses per line:** numbered lists put two courses on one line;
  they are split on the `n.` items as `course ( credits, Season YYYY, grade )`.
- **No text layer:** image-only PDFs are refused with "This PDF has no text.
  In Chrome, use Print → Destination: Save as PDF (not Microsoft Print to
  PDF)."

### 10a. What the implementation found or did differently (for review)

| Point | What happened | Why |
|---|---|---|
| CS mapping (decision 1) | **Not built.** `sas-198-ba.json` / `sas-198-bs.json` exist with no entries, so every CS DN requirement is `unmapped`. | The developer's real audit has ONE degree block - "The SAS Core Curriculum (NB) (Summer 2019)" - and no CS block. A CS mapping without a CS print would be name-matching. |
| SAS Core mapping | 6 requirements (R1-R6 → CORE_CC, CORE_NS, CORE_HSA, CORE_AH, CORE_WC, CORE_QFR) and 9 goal codes mapped, `review_status: pending_review` (used, and labelled "mapping not yet reviewed"). | Built from the real audit's exact headings; needs a named reviewer to mark `reviewed`. |
| Goal codes (decision 7) | The real audit has **14** goal codes (CCO, CCD, NS, HST, SCL, AHo, AHp, AHq, AHr, WC, WCr, WCd, QQ, QR) plus the requirement code CC - not 13. AHo/AHp/AHq/AHr are `unmapped` (CoursePilot has one CORE_AH node for all four). NS is `unmapped` as a goal (it is R2's own multi-course pool; R2 carries CORE_NS). | One-to-one rule. |
| DN "not satisfied" vs CoursePilot "met" | Shown as NEEDS REVIEW everywhere, but planning does **not** lower CoursePilot's status. | DN's "Needs" line does not say which course is missing, so planning would have to guess. DN "satisfied" and "in progress" do lift a node. |
| Final-grade dates (decision 6) | A cited table (`app/core/grade_calendar.py`) from the Registrar's Spring and Fall 2026 calendars. A term without a published date is not used. | Never estimated. |
| Print timestamp time zone | Read as America/New_York. | Chrome prints the student's local time; Rutgers NB students are assumed Eastern. |
| Document retention | The PDF is deleted as soon as extraction ends (success or failure) - the transcript rule. | Stricter than "after confirmation or expiry". |
| Course history | Staged as a linked `transcript_import` (`source_document = degree_navigator_audit`) and confirmed by the existing `confirm_import`; the transcript API never lists it. | "Do not build a second path." |
