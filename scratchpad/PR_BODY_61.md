# Phase 6.1 — Rutgers Data Source & Coverage Investigation

**This PR is investigation only. It changes no production code, schema or
data.** It adds a report (`docs/investigations/phase-6-1-rutgers-data-sources.md`),
two reusable anonymous probe scripts, and the JSON evidence they produced.

It's stacked on `feature/phase-6-0-multi-program-foundation`, which isn't
merged yet. Merge 6.0 first; after that, this diff contains only the 6.1
commits.

## Verdicts, all with recorded evidence

| system | finding | verdict |
|---|---|---|
| **SOC** | Public. `courses.json` is 21 MB with `max-age=900`. **`openSections.json`** is 96 KB with `max-age=30` and is used by SOC's own app. 75.3% of prerequisite strings are pure boolean expressions. | **Authoritative** for courses, sections, meetings, instructors, prerequisites and live availability. |
| **Catalog** | Public, served by Coursedog. `sitemap.xml` returns 404; the navigation tree embedded in every page lists 516 NB undergraduate pages (172 subject-coded). 430 of 433 page IDs are stable across years. Requirements are **prose**: 0 of 14 sampled pages hold structured requirement data. | **Authoritative** for programs and requirements, via reviewed curation. |
| **Degree Navigator** | `dn.rutgers.edu` redirects straight to CAS NetID login (`renew=true`), and its terms are accepted at login. It holds program definitions, co-requisites and equivalencies. | **Not a data source.** At most a human validation reference; flagged for a possible institutional data agreement. |
| **CSP** | NetID login page. Its value is scheduling behaviour, and it exposes no public data interface. | **Don't integrate.** Build scheduling from SOC. |
| **WebReg** | Authenticated. Its page states: "**The use of automated software for registration is prohibited.**" | **Never automate.** Hand off index numbers only. |

## Existing bugs documented, not fixed

1. **A retaken course fills two slots.** 01:198:314 taken twice filled two
   `CS_ELECTIVES` slots and counted 8 credits.
2. **Search ranks code-shaped queries badly.** Carried over from Phase 6.0.

## Readiness

- **Planning Engine: PARTIAL.** There's no parsed prerequisite graph, and only
  one full term of offerings is loaded.
- **Schedule Engine: PARTIAL.** The data is essentially ready; the engine code
  doesn't exist yet.

## Recommended next phase

**6.2: prerequisite graph from SOC, plus the retake fix.** After that:

1. 6.3 — discovery registry and SAS curation wave
2. 6.4 — requirement primitives
3. 6.5 — Planning Engine
4. 6.6 — Schedule Engine and live availability

## Probes run

All probes were anonymous GETs with the project User-Agent and 1.5 s between
requests. Every URL came from an official Rutgers page. They stopped at every
login page and submitted nothing.

- **Systems:** one GET each to the four entry points, plus the Degree
  Navigator app, About page and FAQ.
- **SOC:** the SOC app's JavaScript, `openSections.json` twice, and
  `courses.json` once.
- **Catalog:** 22 school URLs (all turned out to be 404), 2 sitemaps (404),
  and 14 sample program pages.

## Tests

No production code changed, so no test counts moved. One throwaway retake
test was run and deleted.

## Needs human review

- The terms of use for catalog and SOC access at scale.
- A polling rate for `openSections.json`.
- A possible Degree Navigator data agreement.
- The Mathematics definition from Phase 6.0.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
