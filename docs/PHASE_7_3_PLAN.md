# Phase 7.3 plan: CSP, Degree Navigator and WebReg as sources

Every Phase 7.3 prompt reads this file first. Status: **plan and discovery
only** - no production code is written until the discovery report is reviewed.

---

## 1. Decision record

**Date:** 2026-10-22 · **Decided by:** the project owner · **Supersedes:** the
blanket "never automate / never scrape an authenticated Rutgers session" rule
in `AUTHENTICATION.md` and the "Degree Navigator is not scraped" stance in
`DATA_SOURCES.md` - **only** as narrowed below.

**Context.** Rutgers students plan in **Course Schedule Planner (CSP)**,
register in **WebReg**, and track progress in **Degree Navigator (DN)**.
CoursePilot is only useful if it agrees with what students see in those tools.

**Decision.**

| Data | Preferred source | How it reaches CoursePilot | Fallback |
|---|---|---|---|
| Section data (term-scoped, identical for every student) | **CSP**, layered over SOC | A developer-run collector using the **developer's own** saved session | SOC (existing pipeline) |
| A student's requirement status | **DN audit** | The student uploads a DN audit **PDF**; staged, reviewed, confirmed | CoursePilot's own Degree Engine |
| What the student is taking now | **WebReg schedule PDF** | The student uploads it (7.2.2 importer); staged, reviewed, confirmed | Manual section confirmation |

The combined data view keeps every field's provenance. Where CSP and SOC
disagree, the disagreement is recorded and surfaced (the 7.2.4 rule) - never
silently resolved. CoursePilot's Degree Engine stays CoursePilot's engine: a
confirmed DN status is a **student-provided comparison and status record**,
not a replacement for the deterministic audit.

**Consequences.** CSP collection is a deliberate, narrow exception to
"CoursePilot never scrapes an authenticated Rutgers session": developer-only,
manual login, no Duo automation, polite, cached. It does **not** extend to
students' sessions, to DN, or to WebReg, which enter only as student uploads.

---

## 2. Trust boundaries (non-negotiable for every later prompt)

1. **The CoursePilot server never receives, stores or proxies** any student's
   NetID password, Duo approval or Rutgers session cookie. Nothing in the
   product asks a student to sign in to Rutgers through CoursePilot.
2. **CSP section data is collected only with the developer's own session**,
   created by a manual, headed Playwright login and saved to
   `data/private/csp_state.json` (gitignored). Duo is never automated. When the
   session expires the collector **stops**, records `auth_expired`, raises an
   alert, and the app falls back to SOC. The session never reaches the
   CoursePilot server, the database, logs or Git.
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

## 5. Changes to earlier documents - PENDING owner confirmation

These amendments were drafted but **not applied**: the change narrows a
documented security rule, so it waits for the owner's explicit confirmation.

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
