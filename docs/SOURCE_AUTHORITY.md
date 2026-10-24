# Source authority map (Phase 7.2.4)

One answer to "who decides this field?". Evidence investigated 2026-10-19;
detail lives in the code named in each row.

## Authority

| Field | Authority | Provenance / version kept |
|---|---|---|
| Available Rutgers terms | SOC `courses.json`, probed over a rolling window (`term_discovery`) | `rutgers_term`: first seen, last checked/fetched/validated, content hash, counts |
| Target-term offering | That term's SOC data, once **READY** (published + ingested + validated) | `data_source` content hash; `rutgers_term.ingested_hash` |
| Offering in an unpublished term | Same-season history, labelled **HISTORICALLY_LIKELY** | `OfferingEvidence.observed_terms`, `target_term_authority`, `checked_at` |
| Section, index, meetings, instructor, campus, credits | Target-term SOC data | as above |
| Prerequisite / co-requisite rules | Target-term SOC text; else latest same-season term (labelled) | `rules_term` |
| Restrictions (school/major/level) | SOC section restriction data (6.x) | per section |
| Open / closed | SOC `openSections.json` live poll (6.7–6.9) - separate from validity | `section_availability` observed_at |
| Current registration | Student-confirmed WebReg PDF import (7.2.2) | `student_registration`, import hash, parser version |
| Academic history | Transcript import / manual entry (confirmed) | `student_course.source_kind` |
| Program requirements | Catalog + human-reviewed definitions | `rules_fingerprint`, program lifecycle |
| Degree audit | CoursePilot Degree Engine | `AUDIT_ENGINE_VERSION` |
| Degree Navigator comparison | Student-provided DN audit (future; never overwrites rules) | - |
| Professor sentiment | Permitted external summaries / first-party feedback (verified identity only) | `Provenance` (source, sample, window, confidence) |
| Difficulty / workload | First-party feedback aggregates only; otherwise **NOT_ENOUGH_DATA** | `Provenance`; min sample 5 |

**The target-term rule.** Once a term is READY, history decides nothing about
it: a course missing from it is `not_offered_in_term`. A term Rutgers
publishes but CoursePilot has not validly loaded is `target_term_data_pending`
(UNKNOWN, not placeable). Only unpublished or unchecked terms use history, and
the UI says so ("CoursePilot has no Spring 2028 schedule from Rutgers";
"Rutgers hasn't published …" only after an actual check). Saved plans that
relied on history become stale (`offering_assumption_contradicted`,
`target_term_published`); nothing is swapped silently.

## Refresh cadence (one pipeline per kind, not competing systems)

| Kind | Mechanism | Cadence |
|---|---|---|
| Term discovery | `term_cli discover` - sequential, paced, archived | low (daily) |
| Term structure | `term_cli ingest` - existing SOC pipelines + validation | on new payload hash |
| Open/closed | existing live worker (`openSections.json`, leased) | existing 6.9 cadence |
| Catalog / programs | `registry_cli` | separate, low |

## Investigation matrix

| Source | Access | Term-scoped | Live | What it adds | Verdict |
|---|---|---|---|---|---|
| SOC `courses.json` | public, unauthenticated | yes | snapshot | courses, sections, meetings, instructors, notes, prerequisites, credits | **Authority** for term structure |
| SOC `openSections.json` | public | yes | yes | open index list | **Authority** for open/closed only |
| SOC term list (`init.json`) | 404 | - | - | - | does not exist → rolling-window discovery |
| Course Schedule Planner (CSP) | **NetID login** (`sims.rutgers.edu/csp/`) | - | - | not verifiable without auth; page links to SOC/SIS | blocked by authentication |
| WebReg | **CAS / RUID+PAC login** | - | - | the student's own registrations | student-provided PDF only (7.2.2) |
| Degree Navigator | **CAS redirect** | - | - | official audit view | future student-provided comparison |
| Catalog (`catalogs.rutgers.edu`) | public | by year | no | program prose | via `registry_cli` |
| Rutgers SIRS | NetID-permissioned; terms forbid aggregation without consent | - | - | course evaluations | blocked by permission |
| Rate My Professors | terms prohibit automated access/reuse without written permission | - | - | ratings | blocked by permission; interface only |
| Reddit | Data API requires an approved application | - | - | discussion themes | pending approval; interface only |

Observed source disagreement (Spring 2027, 2026-10-19): `openSections.json`
listed 15,205 indexes against 11,233 NB sections in `courses.json` (4,189
unknown to the NB payload - the feed appears campus-unfiltered) and omitted 23
sections `courses.json` marks open. Recorded per term as counts in
`rutgers_term.disagreements`; neither source overwrites the other.

No Rutgers credentials, cookies or CAS sessions are ever collected, stored or
replayed; authenticated systems are classified, not automated.
