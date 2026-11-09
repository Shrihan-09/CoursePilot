# Phase A addendum: paired sources and curriculum conflicts

Research date: 2026-11-09. This updates the [Phase A report](phase-08a-program-expansion.md). Research and documentation only: no curriculum was encoded, no engine or database changed, and no source conflict was resolved by selecting a preferred source.

## Required evidence for every future encoding

For every program and applicable curriculum version, locate both the official catalog requirements and the official department/school requirements page. Archive both, including delegated elective lists and linked requirement documents. Record requested URL, verified final URL when available, UTC retrieval time, exact saved-byte SHA-256, representation, and local archive path. Link each encoded clause to the relevant snapshots and applicability scope. A missing department source remains a visible evidence gap; catalog coverage alone is insufficient.

There is no blanket rule that the catalog overrides the department, or vice versa. Compare applicability before comparing requirements. Explicitly different cohorts can legitimately have different rules. Disagreements within the same or uncertain scope must remain visible, with both claims, sources, affected contexts, and an unresolved status. Do not manufacture course equivalences, correct apparent source typos silently, or treat a newer retrieval timestamp as an effective date. Future publication/review must address unresolved material conflicts explicitly.

This pass covers the five selected pilot programs, with Industrial Engineering split into two class-year contexts and Biology retaining two home-school compositions. It does **not** claim that department pages have been discovered for every one of the 70 inventory rows. That paired-source discovery is required before any additional program is encoded.

## Pilot source pairs

The [paired-source manifest](evidence/phase-08a-paired-sources.json) contains exact URLs, UTC retrieval times, full SHA-256 hashes and archive paths for 17 captures. The [conflict register](evidence/phase-08a-source-conflicts.json) references those snapshots by full hash.

| Pilot/context | Official department requirements | Catalog comparison |
| --- | --- | --- |
| SAS CS BS | [BS degree](https://www.cs.rutgers.edu/academics/undergraduate/cs-degrees/b-s-degree), plus its [electives list](https://www.cs.rutgers.edu/academics/undergraduate/electives) | [Computer Science 198](https://newbrunswick-26-27-undergrad.catalogs.rutgers.edu/schools/sas/program-listing/computer-science-198) |
| RBS–NB BAIT BS | [Public program requirements](https://www.business.rutgers.edu/undergraduate-new-brunswick/business-analytics-information-technology) and [cohort-specific myRBS requirements](https://myrbs.business.rutgers.edu/undergraduate-new-brunswick/business-analytics-and-information-technology-major) | [BAIT](https://newbrunswick-26-27-undergrad.catalogs.rutgers.edu/schools/rbsnb/degree-requirements/programs-majors-minors/bait) |
| Industrial Engineering, classes 2026–2029 | [Classes 2026–2029 curriculum](https://ise.rutgers.edu/bsie-curriculum-2026-29) | [Industrial](https://newbrunswick-26-27-undergrad.catalogs.rutgers.edu/schools/eng/programs-study/industrial), [first year](https://newbrunswick-26-27-undergrad.catalogs.rutgers.edu/schools/eng/programs-study/first-year) |
| Industrial Engineering, class 2030 | [Class 2030 curriculum](https://www.ise.rutgers.edu/bsie-curriculum-class-2030) | Same catalog pages; generic Industrial page has not been demonstrated applicable to this class |
| SEBS Microbiology BS | [Department requirements](https://dbm.rutgers.edu/undergraduate/microbiology/requirements), linked [Spring 2020 worksheet](https://dbm.rutgers.edu/sites/default/files/2025-01/microbiology-curriculum-spring-2020-ac.pdf) | [Microbiology](https://newbrunswick-26-27-undergrad.catalogs.rutgers.edu/schools/sebs/programs/microbiology) |
| Biological Sciences BA, SAS and SEBS | [Department requirements](https://biology.rutgers.edu/academics/undergraduate/major-in-biological-sciences), [March 2026 life-science electives PDF](https://biology.rutgers.edu/images/2026%20uploads/REVISED%20ELECTIVE%20SHEET%20MARCH%202026.pdf) | [SAS Biology](https://newbrunswick-26-27-undergrad.catalogs.rutgers.edu/schools/sas/program-listing/biological-sciences-119), [SEBS Biology](https://newbrunswick-26-27-undergrad.catalogs.rutgers.edu/schools/sebs/programs/biological-sciences) |

### Archive quality and location

All 17 saved artifacts have verified SHA-256 hashes: 11 raw HTML/PDF captures and 6 explicitly labeled public web-reader text captures. Direct requests to several department endpoints returned HTTP 403; their accessible reader representations were preserved instead. A text hash authenticates the saved extraction, **not** the original HTML/PDF. Reader retrieval time records acquisition of that representation, not proof of a live origin fetch at that instant. These six captures need raw replacements or explicit capture-quality acceptance before production curation.

The content-addressed artifacts are local, under the gitignored `data/raw/catalog/phase-08a-dual-source/` directory in the running app checkout. The small manifests and conflict register are versioned on the documentation branch. GitHub alone therefore does not contain the complete archive; preserve this directory with the research artifacts when transferring the work. The catalog inventory's earlier 135-page manifest remains separate from this pilot refresh. No private data is present in either manifest.

## Visible disagreements and applicability differences

| ID | Evidence and affected requirement | Classification / next action |
| --- | --- | --- |
| CS-INTRO-2027 | Department BS lists 121/122 and explicitly dates the new introductory sequence to Spring 2027; the 2026–2027 catalog still lists 111/112. Department says preparatory 120 earns SAS credit but no CS major credit. | Open effective-term transition. Obtain the official old/new transition/equivalence policy before changing the existing validated BS. Do not replace old students' requirements globally. |
| CS-BS-COPY | The BS page labels its residency clause as a BA requirement. | Open cross-degree wording ambiguity. Verify scope before copying it into BS policy. |
| BAIT-CORE-COHORT | Public program page shows 39 business-core credits without an explicit cohort qualifier. myRBS distinguishes Fall 2025-or-earlier (39) from Fall 2026 (36); catalog identifies the Fall 2026 curriculum (36). | Cohort variation is documented; the generic page's unqualified scope remains visible. Keep both versions, including their different course selections, rather than merging a 39-credit heading with a 36-credit list. |
| BAIT-ETHICS | Both department/school pages identify Business Ethics as 33:522:334; the catalog core lists 33:522:335. | Open course-identity conflict, including the Fall 2026 comparison. No inferred equivalence or silent correction. |
| IE-CLASS-VERSIONS | Classes 2026–2029 department page totals 129 credits / 67 major credits; class 2030 totals 123 / 69. Chemistry II appears in the former and is absent from the latter. | Explicit class-year variation, not an error to reconcile away. Preserve both complete curricula. Do not extrapolate the 2030 page to 2031+. |
| IE-CATALOG-SCOPE | Generic catalog Industrial curriculum totals 129; department's class-2030 curriculum totals 123. Catalog first-year page separately identifies the class-2030 IE Chemistry II exception. | Open catalog scope/completeness issue. The first-year exception supports a change but does not authorize constructing a hybrid curriculum or prove all other catalog requirements apply to 2030. |
| MICRO-INTRO | Department introductory-science heading says 46 credits; catalog heading says 44. The catalog also prints organic chemistry as 01:160:370–308 while department lists 307/308 (or 315/316). | Open total and course-number conflicts. Compare clauses and verify intended chemistry identifiers; retain the literal catalog claim. Both sources call the microbiology core 44 including electives. |
| MICRO-WORKSHEET | Department links a worksheet named Spring 2020 under an upload path containing 2025. | Open dependency applicability, not proof of a 2026 curriculum. A filename/upload date cannot establish the student's governing cohort. |
| BIO-SCHOOL-TOTAL | SAS catalog lists 47 core credits versus SEBS 49 (71 versus 73 including 24 elective credits), although both describe the shared Biology major. | Open cross-school composition/total discrepancy. Do not normalize to one total until reviewed. Department requirements and the current elective PDF are additional evidence, not an automatic tie-breaker. |
| BIO-MATH-ID | Department's second-math substitution note prints 01:160:152; catalog math text also has malformed alternatives. | Open identifier/typographical ambiguity. Do not silently substitute 01:640:152 or parse concatenated numbers as new courses. |

The machine-readable register retains both source claims and their locators. No row is recorded as resolved. Explicit cohort differences are distinguished from unresolved source conflicts, so a user can see why two valid versions differ.

## Industrial Engineering class-year design

Future curriculum applicability needs an explicit `curriculum_class_year`, supported by the school's curriculum/classification evidence. Keep it separate from catalog year, entry/transfer cohort, effective term, and the student's editable target graduation term. Delaying a planned graduation must not silently switch the student's governing curriculum. Unknown class year requires clarification/review, not guessing from a target term.

Start with two distinct sourced versions: classes 2026–2029 and class 2030. Do not assume 2031 inherits 2030. The differences extend beyond Chemistry II: the older page includes Accounting for Engineers and Scientific and Technical Writing, while the new page adds Applied Algorithms and Industrial Informatics and changes fixed department courses into elective choices. Encode the whole applicable version only after review; no universal IE tree with one Chemistry II toggle.

Both department pages warn that major courses are offered annually in the displayed semesters. This is explicit availability evidence to verify against term offerings. It qualifies the earlier report's general warning about sample plans: sample placement alone is advisory, but an explicit annual-offering statement must not be discarded. It does not prove every displayed semester is a mandatory completion deadline.

## Knowledge-index and review contract

Extend the proposed contextual membership key with curriculum version and applicable class year, alongside home school, catalog/entry scope, effective term and variant. Store requirement-level source snapshot IDs and conflict IDs. Preserve claims from both sources separately; do not flatten conflicting memberships into one authoritative union or intersection.

Future plan/audit explanations, course search and assistant answers should show an unresolved-source warning with both claims and links, and request the missing context where applicability is unknown. An unresolved affected membership remains conditional/unreviewed; it cannot silently become a definitive completion claim or a free-elective-only classification. This is a design requirement, not an implemented UI in Phase A.

Before encoding any pilot: verify paired-source completeness and capture quality; establish its governing cohorts/class years; reconcile material conflicts with documented reviewer evidence; then follow the existing parsed → validated → reviewed → published lifecycle. Human decisions need scope, rationale, reviewer, time and source references while preserving the original disagreement. Phase B still requires approval.

## Validation and stopping point

Verified all 17 artifact hashes and byte counts, coverage of both source families for all six pilot contexts, and conflict-register links to manifest snapshots. Archive files remain gitignored. Only instructions, research documentation and public evidence metadata changed. No tests, model calls, migrations, database writes or server lifecycle operations were needed. Stop at this Phase A report.
