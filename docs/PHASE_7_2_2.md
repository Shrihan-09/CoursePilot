# Phase 7.2.2: investigation and design record

Starting main: `15143e5071ca3a542f7a699bbaaa1dc2986199f6`.
Starting migration: `b491ce3a2784`; starting Planning Engine: `6.6.0`.
Investigation date: 2026-10-19. All behavioral examples and tests use fabricated
students. No uploaded personal transcript or schedule defines an academic rule.

## Investigation before implementation

| Priority | Confirmed issue | Root cause | In-scope response |
|---|---|---|---|
| P1 | College Writing absent, writing distributions permissive | Three courses from combined WCr/WCd pool | Explicit WC, WCr, WCd; source-backed exemption evidence |
| P1 | QQ-only or QR-only could fill quantitative pool | Combined two-course selection | Separate QQ and QR obligations with distinctness scope |
| P1 | Valid cross-area Core reuse rejected | Whole Core treated as one exclusive system | Allocation scope independent from display system |
| P1 | Core pass/no-credit eligibility overstated | Earned degree credit conflated with Core eligibility | Per-requirement grade/origin policy and unknown evidence |
| P1 | WCr attendance provenance absent | Rutgers origin does not prove New Brunswick attendance | Required-campus evidence; unknown remains unknown |
| P1 | Count-based nodes ignore quoted credit condition | Count-only verdict | Joint count, credit and category checks |
| - | CS query permits graduate unit (claimed) | **Not a bug on re-check:** since Phase 7.0 a query admits only level-U courses unless it opts in (`test_a_query_never_admits_graduate_courses_unless_it_opts_in`) | The attempted CS definition edit was REVERTED: a published definition changes only through human review |
| P1 | Empty meeting list appears time verified | Vacuous all([]) | Nonempty evidence required |
| P1 | Legacy publication described as reviewed | Supported status conflated with human approval | Publication-basis-aware text |
| P1 | Imported registration requires academic-history course | Current confirmation FK and latest-in-progress restriction | Separate preserved registration identity; no academic history writes |
| P2 | Core copied into selected major definitions | ProgramVersion treated as whole context | Independent school-set composition by explicit school/catalog applicability |
| P2 | Progress has no cross-client measurement contract | Existing credits and leaf tallies have different meanings | Versioned backend rational obligation metric |
| P2 | Modality has raw fields but no section/bundle evidence contract | Course and section semantics not separated | Conservative meeting/section/bundle classification |
| P2 | Ranking buried in term filling | Canonical code final tie precedes useful preference evidence | Pure bounded ranking boundary and structured evidence |
| P2 | Coverage stops after encoding/review | Discovery pages are not evaluable definitions | Explicit lifecycle counts and independent review workstream |

No P0 was established. Cosmetic P3 work and polished rings are deferred.
New schemas require additive migrations, disposable roundtrip verification, and
engine/fingerprint invalidation. Existing academic records are preserved.

## Source authority and applicability

Public Rutgers sources checked during the read-only audit:

- https://sasoue.rutgers.edu/core/core-learning-goals
- https://sasundergrad.rutgers.edu/resources/faq/faq-detail/how-can-i-check-and-see-what-courses-are-being-offered-2
- https://sasundergrad.rutgers.edu/majors-and-core-curriculum/core/cognitive-skills-and-processes-writing-and-communication

The writing page identifies College Writing 01:355:101/103/104, distinct WCr/WCd,
and an AP English composition/literature score of at least four exemption.
Transfer credit is not proof of WCr New Brunswick attendance. Core requires
appropriate graded, credit-bearing work. Exact eligibility remains source- and
catalog-specific; absence of evidence never creates an exemption.
The writing page's nine-credit heading and certified lower-credit course list
must not be flattened into invented three-credit minimums per writing child.
School rules remain pending review when no named human has approved the corrected
encoding. The legacy CS publication is not a recorded human review.

## Academic context and coverage boundary

A primary program version and independently versioned school requirement sets
are composed deterministically. School code and applicable catalog year select
school context; engine logic contains no SAS/CS branches. Requirement display
system and allocation scope are different concepts. A course can satisfy an
allowed major and Core obligation without earning its credits twice.
Additional-major/minor/certificate goals remain exploratory. No API or assistant
may imply combined-program planning exists.
Complete graduation eligibility is not established. School/Core coverage,
residency, graduation credits, exceptions, admission versus completion rules,
and source-review status must be disclosed independently. A major's credit
minimum is not a whole-degree credit minimum.

## Modeled progress policy: modeled-obligations/1

One terminal obligation contributes one presentation unit. Course-count progress
is valid allocated count divided by required count, capped at one; credit
progress is valid allocated credits divided by required credits, capped at one.
Simultaneous count/credit/category constraints use the least-satisfied fraction.
An engine rejection for another constraint never becomes a full completed unit.
These weights are an explicit CoursePilot display convention, not Rutgers
credits, workload, time remaining, or likelihood of graduation.

ALL_OF sums child budgets and does not add a parent unit. ANY_OF requiring N
alternatives has N units; use the N greatest normalized alternative fractions.
Unchosen alternatives do not inflate the denominator. Unknown obligations remain
in the denominator when defined, earn no confirmed completion, and are counted
separately. An undefined/empty scope returns unavailable/null, not a fake zero.
Non-count eligibility rules remain separate gates and are never averaged away.
A completed attempt with provisional grade/campus eligibility does not count as
confirmed completed progress. An affected obligation contributes a conservative
zero lower bound until that eligibility is established, rather than presenting
its raw allocated count as confirmed.

Compute completed-only, completed-plus-current, and fresh saved-plan projections
through the same Degree Engine. Planned attempts remain transient and provisional.
Stale saved plans are not regenerated or projected by GET. If reallocation makes
increments incomparable, withhold additive segments rather than clamp away losses.
Expose exact rational percentages plus two-place display rounding. A displayed
100 percent refers only to modeled obligations and does not certify graduation.

GET `/api/v1/student/progress` returns the authenticated student's own progress,
coverage, fingerprints, engine/policy versions, projection state, rule gates and
warnings. The request cannot choose another student. No writes or audit-cache
population are needed. Requirement systems are explicit, not parsed from codes.

## Profile and Home contract

The full Profile will have one concentric visualization: outer modeled overall,
middle primary program, inner school/Core. It is one related visualization,
never three unrelated donuts. Home should reuse its compact variant and link to
full inspection. The center identifies the selected scope and completed percent.
Completed is solid, current patterned, planned outlined/optional, remaining
neutral; unknown uses text and a marker. Never rely on color alone.

Native keyboard controls, numerical text/bar equivalents, visible focus,
phone-size hit areas, semantic light/dark tokens and reduced motion are required.
No Apple assets, palette, proportions, animations, icons or branding are copied.
This phase supplies only a minimal text/bar contract proof. Current semester,
confirmed current schedule, planning preferences, timeline freshness, remaining
requirements and warnings belong alongside progress in the later full Profile.
Earlier completion is an optional evidenced scenario, not an assumed preference.

## Course intelligence boundary

Before: dependency, unlocked targets, scarcity, offering evidence, eligibility,
then canonical course code. Credits bound packing and target distance; they were
not difficulty. Ranking is deterministic greedy selection, not global optimality.
After: preserve academic filters and expose a pure bounded rank/evidence boundary
with requirement-coverage evidence before the final canonical tie-break.
Eligibility UNKNOWN never becomes a valid preference-ranked alternative.

Meeting evidence is term- and section-specific. Explicit online/no-time supports
asynchronous; explicit online/timed supports synchronous online. Known in-person
and online components support hybrid; malformed, empty, TBA and ambiguous evidence
stay unknown. A course may have several modalities in one term. Availability
summaries are not permanent course attributes and do not certify future terms.
Modality preference storage is a validated foundation; any not-yet-applied
preference must explicitly say so. Hard constraints are never silently simulated
by a soft rank and soft preferences never remove every valid schedule.

## Future feedback: design only

No trustworthy course difficulty/workload dataset exists in this repository.
Do not infer it from title, department, instructor reputation, or an LLM.
Future optional first-party feedback follows confirmed course completion and
separates course, term, section and instructor context. Aggregate workload and
perceived difficulty distributions with sample size, collection window, source,
updated_at, confidence methodology and minimum sample thresholds. Small cohorts
must not reveal identities. Require anti-abuse checks, bounded submissions,
moderation and transparent selection bias. UI wording: lower reported workload.
No third-party rating scrape or invented numeric signal is introduced.

## Future assistant tools: design only

Tools read academic context/progress, enumerate validated requirement alternatives,
preview preference patches, preview transient plan/schedule changes, compare
program scenarios, explain deterministic ranking, and confirm a versioned proposal.
Every proposal includes fingerprints, changed settings, assumptions, warnings and
consequences. Confirmation rejects stale proposals. Natural language does not
satisfy requirements, override prerequisites, allocate credits or validate schedules.
Only explicit confirmation persists important preference/plan/registration changes.
A second-major request must disclose exploratory-only support until generic
multi-program validation exists.

## Future registration boundary: design only

Schedule Engine -> RegistrationPlan -> Student review -> Explicit confirmation
-> RegistrationAdapter. ManualWebRegHandoff carries course/index information for
the student to register themselves. RutgersOfficialRegistrationAdapter remains
an unimplemented interface contingent on an officially authorized integration.
No credentials, CAS automation, session cookies, authenticated scraping, private
endpoints, or registration clicks are collected or automated.

## Program coverage workstream

Discovery records pages; fetching archives sources; parsing extracts candidate
credentials and structured definitions; validation checks schema, references,
unsupported constructs, source quotes and executable semantics. Named-human
review binds source and definition hashes; explicit publication follows approval.
Source or definition changes require revalidation/rereview. A page or parsed
heading is not a supported program. Coverage reporting must distinguish pages,
credential candidates, versions and review records.

Keep this workstream independent: bounded public fetch batches, archived snapshots,
review packets, cross-year diffs, unresolved-construct queues, explicit approval.
No major-specific Python branches and no automatic AI publication. The selector
should distinguish published/reviewed, legacy published, being verified and not
supported; unsupported entries may request support but must not produce fake audits.

## Degree Navigator comparison: design only

A future student-controlled audit upload is a comparison artifact, not an authority
over CoursePilot's engine or a route to overwrite academic history. Parse/stage
under the same private upload limits; compare dated, scoped requirement verdicts
with explicit mappings. MATCH means agreement within coverage; DIFFERENCE means
review, not automatic reconciliation. No authenticated Degree Navigator scraping.

## Remaining operational limitations

Windows PDF workers have wall-clock timeout and process termination; POSIX resource
limits do not imply equivalent Windows memory isolation. Conservative fabricated
WebReg fixtures establish supported parser signatures, not comprehensive real-world
layout coverage. Unknown layouts must fail closed and allow manual confirmation.
Raw uploads must be removed after extraction and never enter logs or Git.

## Verification record (takeover completion, 2026-10-19)

**Failure injection** - `scripts/inject_academic_failures.py`, 18 faults, each
restored and verified after its run. The first run detected 12; the six misses
(unknown budget shown as a fraction, in-progress work changing the completed
segment, course text beside an exact WebReg identity, identities outside a
recognised region dropped beside a valid row, registration meetings shown after
SOC renumbered the index, changed school rules not sent for review) each gained
a test. One miss was an equivalent mutant (the leftover-text check still refused
the line) and the fault was redefined. Final run: 18/18 detected.

**Performance** - `scripts/benchmark_progress.py` (throwaway `*_disposable`
database, public fixtures, fabricated students, 15 runs): `build_progress` p50
58 ms with nothing taken and 96-105 ms with a partial or full CS record; 52 SQL
statements per call for both, so there is no per-course N+1; output is
byte-identical across runs. WebReg: in-process extraction p50 2 ms, parse
0.1 ms, SOC matching of 10 rows 29 ms; the isolated child process costs about
1 s on Windows (process spawn), once per upload.

**Browser QA** - `frontend/tests/visual/academic.mjs` against a disposable stack
(fabricated `qa722-*` students, fabricated PDF): Profile and the import review
at 320/390/820/1280/1600 px, light and dark; 32 audited screens with no overflow,
no axe violation and no target under the floor; 34/34 functional assertions.
QA found and fixed: raw internal row states shown to students, a confirmed import
still reported as "Section not chosen", a stale expiry line, a duplicated word,
and undersized inputs. Imported registrations are shown in My current schedule,
labelled as imported; they never write a section confirmation or academic history.

**Real WebReg compatibility** - two real View / Print Schedule PDFs were checked
locally, outside the repository; only aggregate structure was inspected or recorded.

1. Saved through "Microsoft Print To PDF": every glyph is a vector outline - no
   font, no text object, no image, ~19k path segments. There is no character
   data; reading it would be OCR, which this architecture deliberately does not
   do. `UNSUPPORTED_LAYOUT` is correct. The upload error now tells students to
   use the browser's own "Save as PDF". Fabricated fixture:
   `build_schedule_pdf(outlined=True)`.
2. Saved with the browser's own "Save as PDF": a normal text layer (4 fonts, 96
   text objects). Header, term, five weekday labels on one baseline and the
   by-arrangement heading were all recognised; the refusal came from the grid
   identity pattern. Real grid cells print `course:section:index` with a COLON
   before the index; the first fixtures had assumed a hyphen. `GRID_ID` now
   accepts exactly one `:` or `-` before exactly five digits; nothing else was
   relaxed (`webreg-layout-3`). Result: recognised, 5 registration candidates,
   all with an exact index, one term; matched against the archived public SOC
   data in a throwaway database: 5 EXACT_MATCH, 0 MULTIPLE_MATCHES, 0 NO_MATCH,
   0 NEEDS_CONFIRMATION. Fabricated colon-form tests failed at the
   same parser line before the fix; injection fault S guards it.

To check another real schedule, keep the PDF outside the repository and run
`python scripts/validate_webreg_local.py <path> [--match] [--diagnose]`; it prints
the outcome, closed codes, counts and the PDF representation, never content.
