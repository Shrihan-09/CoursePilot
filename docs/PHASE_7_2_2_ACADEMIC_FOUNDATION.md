# Phase 7.2.2 academic foundation

## Rule composition and identity

`SchoolRequirementSetVersion` stores an independently sourced school/catalog-year
requirement set. Its explicit `(school_id, catalog_year, code)` identity permits
one active definition per named set in a catalog year. `definition_version`,
`definition_sha256`, source URL/prose and lifecycle describe the definition;
changing any is fingerprinted. It is not a separate student major.

`app.services.academic_context.composed_requirements(session, version)` combines
program-owned nodes with matching school-owned nodes. Independent nodes replace
legacy embedded nodes only for the same requirement systems; legacy rows remain
stored. Duplicate composed codes raise rather than silently overwriting rules.
A missing set is coverage unknown. No graduation-credit or residency coverage is
inferred. `context_coverage` exposes missing scopes and independent provenance.

Audit, planner requirement pools, profile selection warnings and reflective rule
fingerprints use the same composition. The PostgreSQL global rules trigger also
covers the school-set table. Audit semantics version is `7.2.2`; the planning
version is bumped separately. Saved plans and audits are never silently rewritten.

## Allocation and eligibility

`allocation_scope` controls reuse independently from display `requirement_system`.
Absent scope preserves the legacy system-level exclusive behavior. SAS uses scopes
for Contemporary Challenges, HST/SCL, Arts/Humanities, writing, quantitative
reasoning and natural sciences. A course can satisfy different areas, while
prohibited pairs must use distinct courses. Earned credits remain deduplicated.

`choose_n` evaluates simultaneous course counts, credit minima and category
coverage. A bounded local search (10,000 combinations) seeks a feasible count and
credit selection without taking another obligation's allocation. It does not
claim global optimality; unmet constraints remain unmet if the bound is exhausted.

Requirement-specific `graded_only`, `eligible_origins`, and `required_campus`
checks run before allocation. Degree credit is not proof of Core eligibility.
Pass/no-credit cannot satisfy Core. WCr transfer/exam origins are rejected;
ordinary `rutgers` origin does not prove New Brunswick attendance. Missing
attendance or graded-course evidence yields provisional satisfaction plus an
explicit warning, never confirmed completion. Actual in-progress work remains
provisional. Course availability on a campus is not attendance evidence.

## Explicit evidence and exemptions

`StudentAcademicEvidence` is a foundation for typed exam-score, attendance-campus
and requirement-waiver claims. Engines only read claims; they never create or
verify them. Claims require a source reference and default to unverified.
Attendance evidence is keyed by course and exact term. The generic exemption
rule currently supports a verified exam score meeting a published minimum.
SAS College Writing accepts verified AP English language/literature score >=4;
that meets the obligation without manufacturing earned credits or waiving WCr
and WCd. Unverified, malformed or absent claims do not grant exemptions.
All evidence fields participate in the student's academic fingerprint.
There is no public evidence-verification write endpoint in this foundation.

## Sourced corrections and authority limits

The Core definition now includes College Writing (WC), WCr and WCd as separate
obligations and QQ/QR as separate obligations. SOC supplies certification
eligibility; structural definitions and reuse restrictions are curated from:

- https://sasoue.rutgers.edu/core/core-learning-goals
- https://sasundergrad.rutgers.edu/majors-and-core-curriculum/core/cognitive-skills-and-processes-writing-and-communication
- https://sasundergrad.rutgers.edu/resources/faq/faq-detail/how-can-i-check-and-see-what-courses-are-being-offered-2

The official writing page lists certified low-credit writing courses alongside
a nine-credit heading. Therefore no invented three-credit writing-child minimum
is enforced. Individual certified credit-bearing writing courses remain eligible;
this ambiguity is disclosed rather than resolved by guessing. HST/SCL and QQ/QR
retain their explicitly modeled credit minima; AH requires count, credits and
distinct goals together. A human advisor still certifies actual degree completion.

The CS elective query is unchanged. Graduate courses were already excluded
generically in Phase 7.0 (a query admits only level-U courses unless it opts
in), so an attempted edit adding offering unit `01` was reverted: changing a
published definition requires human review. The query still does not
establish the department's complete designated elective list. Non-CS
elective coverage remains explicitly incomplete.
Legacy publication is labeled `legacy_curated` and never presented as recorded
human review. New/changed school definitions stay pending review/needs rereview.

## Migration and loading

Revision `722academic01`, based on `b491ce3a2784`, is additive schema only: school
sets, optional requirement ownership, scoped eligibility fields and typed evidence.
It does not touch student data, env files or the development database. After
applying migrations to an authorized database, run the existing Core ingestion
pipeline against the updated definition and archived SOC certification data.
It creates/updates one school set rather than cloning per major; reruns are
idempotent. Prior embedded definitions remain available until independent data
is loaded. School coverage stays explicitly missing until then.

Downgrade refuses populated school-owned requirements or evidence instead of
silently deleting academic facts. Export/reconcile those records explicitly first.
The known lower environments and data-loading procedure must be verified before
beta; a schema migration alone does not apply curated data corrections.
