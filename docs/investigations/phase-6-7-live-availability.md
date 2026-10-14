# Phase 6.7 investigation: what "available" means in Rutgers data, and how fresh it can be

Probed live on **2026-10-14 02:29-02:31 UTC** (2026-10-13 evening, US
Eastern), with the project User-Agent, 2 s between requests, 13 requests in
total. Payloads archived locally under `data/raw/live/` (gitignored). Compared
against the archived Fall 2026 download of 2026-09-08.

## 1. Sources that exist

| endpoint | bytes (body / on the wire) | latency | `Cache-Control` | content |
|---|---|---|---|---|
| `GET https://classes.rutgers.edu/soc/api/openSections.json?year=2026&term=9&campus=NB` | 96,009 / 28,308 (gzip) | 50-300 ms | `max-age=30` | JSON array of 12,001 index strings |
| `GET https://classes.rutgers.edu/soc/api/courses.json?year=2026&term=9&campus=NB` | 21,228,604 / 931,895 (gzip) | 433 ms | `max-age=900` | 4,395 courses, 12,012 sections, everything structural + `openStatus` |

Both are the endpoints the public SOC app itself calls (Phase 6.1 read its
JavaScript). No authentication, no Rutgers login, nothing scraped. No other
availability endpoint is referenced by the public app; WebReg/CSP require
NetID login and are out of bounds.

## 2. What Rutgers exposes per section (re-verified on the live payload)

| fact | exposed? | where |
|---|---|---|
| open / closed | **yes** | `openStatus` (bool) + `openStatusText` (`OPEN` 8,555 / `CLOSED` 3,457 - no third value); `openSections.json` membership |
| current enrollment, capacity, remaining seats | **no** | no such key on any section (35 keys listed in the investigation script output) |
| waitlist state / capacity | **no** | none |
| special permission to add/drop | yes (structural, not volatile) | `specialPermissionAddCode/Description`, already stored |
| registration restrictions | yes (structural) | `majors`/`minors`/`unitMajors`/`honorPrograms`, stored since 6.6 |
| cancellation | **no explicit field** | a cancelled section disappears from `courses.json` |
| section existence / removal | only by diff of two `courses.json` downloads | 44 added, 24 removed between 2026-09-08 and 2026-10-14 |
| last-updated timestamp | **no** | no `Last-Modified`; `ETag` changes on every response even when the body is byte-identical (same SHA-256 twice, ETags 1828319362 vs 966343522) - not a version |
| course-level open count | yes | `course.openSections` = number of open sections (matches on 4,395 of 4,395) |

**Availability therefore has exactly two observable states, OPEN and
CLOSED, plus CoursePilot's own UNKNOWN.** WAITLIST and seat counts would be
invented.

## 3. `openSections.json` semantics

* **Still exists**, same shape as Phase 6.1: an array of 5-digit index strings,
  no duplicates (12,001 unique).
* **Term-specific, NOT campus-specific.** `campus=NK` returned a body
  byte-identical to `campus=NB`. The 3,446 indexes absent from NB
  `courses.json` are other campuses' open sections: all 1,942 open Newark
  sections and all 1,419 open Camden sections; 85 come from campus codes not
  probed. Indexes are unique across campuses within a term (NB/NK/CM overlap:
  0). `ONLINE_NB` sections are a subset of NB.
* **Membership == `openStatus`.** On all 12,012 NB sections at the same moment:
  every open section is listed, no closed section is listed - 0 disagreements,
  including 2,905 special-permission sections, 5,758 TBA/asynchronous
  sections and 826 cross-listed sections. So, within a term's known sections,
  **absent means CLOSED**.
* **Empty is ambiguous.** An unpublished term (Spring 2027), a nonsense year
  (1999) and a nonsense campus (`XX`) all return `[]` with HTTP 200 - the same
  as "nothing is open". An empty or non-overlapping list must never be read
  as "everything closed".
* **No linked-component information**: a 0-credit LB lab index appears like
  any other index; bundles come from CoursePilot's structure (Phase 6.6.1).
* **Refresh behaviour**: two fetches 60 s apart were byte-identical (Fall 2026
  is mid-semester). Phase 6.1 measured 1,094 flips in 18 days. Rutgers'
  `max-age=30` is the freshest meaningful interval; nothing documents how
  often Rutgers itself recomputes the list.

## 4. Real states observed (Fall 2026, 2026-10-14)

| course | record | open / total sections |
|---|---|---|
| 01:750:193 PHYSICS FOR SCIENCES | base | 0 / 9 |
| 01:750:193 PHYSICS FOR SCI LAB | `LB` | 0 / 9 |
| 01:750:194 PHYSICS FOR SCIENCES | base | **0 / 8** |
| 01:750:194 PHYSICS FOR SCI LAB | `LB` | **1 / 8** - a real "lab open, lecture closed" bundle |
| 01:750:203 GENERAL PHYSICS / 01:750:205 GENERAL PHYSICS LAB | | 2 / 41, 2 / 33 |
| 01:750:227 ANALYT PHYSICS II / 01:750:229 ANAL PHYS II LAB | | 1 / 33, 2 / 27 |
| 01:119:117 BIO RESEARCH LAB | | 3 / 27 |

## 5. Volatility, measured

2026-09-08 archive -> 2026-10-14 live: 825 sections opened, 289 closed, 44
added, 24 removed (12,012 sections). Summer 2026: 1,379 open now vs 1,378 in the
archive.

## 6. Storage options compared

| | A: column on `course_section` | B: latest snapshot table | C: append-only observations |
|---|---|---|---|
| freshness / provenance | overwritten by every catalog ingestion; no refresh identity | per-refresh log gives observed_at + source hash | complete |
| history for prediction | none | none | complete |
| writes per 2-minute poll (12,012 sections) | up to 12,012 | changed rows only | 12,012 (~8.6 M rows/day) |
| refresh boundary | **mixed with structural data** | separate | separate |

**Chosen: B + a transition log.** One `availability_refresh` row per poll
(observed_at, source URL, content hash, counts, accepted/rejected), one
`section_availability` row per section (current state and since when), and
one `section_availability_change` row ONLY when a state changes. Measured
change volume: ~1,100 transitions in 36 days mid-semester; even 50x that in a
registration window is ~2 MB/month. It keeps everything a notification
(CLOSED -> OPEN) or a later predictor (time-to-close, reopenings, volatility)
needs, without C's write volume. "Still closed as of T" is recoverable from
the refresh log, so unchanged polls need no per-section rows.

## 7. Found while implementing (measured, 2026-10-14)

* **Indexes are reused across terms, so a payload's content cannot always
  prove its term.** The real Spring 2026 `openSections` payload shares 116
  open indexes with the 498 Fall 2026 component-fixture sections. Applied to
  Fall it would pass both content guards (it overlaps, and the overlap is
  above 10% of the previous open count). The body carries no term, so the
  refresh also checks that the request URL's `year`/`term` match the term it
  is applied to (`rejected_term_mismatch`). A body mislabelled with a
  correct-looking URL remains undetectable from content; that is a
  documented limit.
* **Two real polls 80 s apart were identical:** 0 opened, 0 closed out of
  12,001 entries (02:29:51Z and 02:31:11Z). Unchanged polls are the norm, which is why
  storage writes only transitions.
* **An archive's file time is not an observation time.** The section fetcher
  takes `retrieved_at` from the cached archive's mtime, which a copy or
  backfill resets. That makes a months-old `courses.json` look "just
  observed". So `courses.json` `openStatus` is never CURRENT. Only an
  accepted `openSections` refresh is.
* **Refresh cost on the full term** (replayed real payloads, development
  PostgreSQL, `scripts/benchmark_availability.py`):

  | step | verdict | transitions | apply | SQL statements |
  |---|---|---|---|---|
  | first observation (11,992 sections) | accepted | 11,992 | 3.3 s | 31 |
  | same payload again | accepted | 0 | 0.29 s | 7 |
  | real next poll (+80 s) | accepted | 0 | 0.38 s | 7 |
  | `[]` (Spring 2027, unpublished) | rejected_empty | - | 0.71 s | 7 |

  Parse: 3.8 ms for 96 KB. Lookup of all 11,992 sections: 3 statements,
  ~0.5 s. Result: 8,513 open/current and 3,479 closed/current; 3,488 listed
  indexes are other campuses' and were ignored.
