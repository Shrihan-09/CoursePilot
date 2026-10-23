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
