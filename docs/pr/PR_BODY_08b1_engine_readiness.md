# Prompt 08 B1: engine and data model ready to encode every NB undergrad major

This branch is based on main after #43 (the Phase A addendum). It changes the engine, the data model and the tooling so that each later wave of majors is mechanical. It also encodes the school gen-ed systems and shared school cores, and re-sources the CS B.S.

**Nothing here is human-reviewed.** Every new component is `pending_review`. The CS B.S. is back at `validated` after its definition changed. Every audit and index label says so.

## Checklist: what existed, what's new

| # | Item | Reused | Added |
|---|---|---|---|
| 1 | Engine | requirement tree, inherited `min_grade`, school requirement sets, verified evidence, `ProgramRule` | components (`kind`, `composition`, `program_version_component`); admission stages; class-year curricula; school-level rules; data-driven degree totals; per-course credit minimum; group caps; open-conflict labels; certification-code eligibility |
| 2 | Gen-ed | SAS Core; SOC term codes (`SOEHS`, `ECN`, `GVT`, `XPL` were already stored) | SEBS Core, RBS Rutgers Core, SoE H/SS + school rules, with provenance |
| 3 | Course knowledge index | `requirement_course_option`, `offering_core_code` | versioned build, membership, context and coverage tables; builder; freshness; readers |
| 4 | CS B.S. | the department list encoded 2026-10-23 (no archive) | sourced from archived department captures; 493/494 cap; open conflicts |
| 5 | Report a wrong requirement | admin auth | model, API, a button on every requirement row, admin page |
| 6 | Encoding pipeline | `review_packets.py`, registry | `scripts/encode_program.py`, multi-source validation and packets, `docs/ENCODING_MAJORS.md` |
| 7 | Tests | SQLite worlds on real SOC records | 30 engine tests, 3 report API tests, 3 UI tests, 15 failure injections, synthetic student per school |

## 1. Engine changes (all done)

Each is the smallest change Phase A proposed. Migration `p08b101` is additive.

- **Components.**
  - `school_requirement_set_version` gains `kind` (`gen_ed`, `school_core`, `entry_stage`, `shared_major`) and `composition`.
  - `"school"` components compose with every program of their school and year (unchanged behavior). `"linked"` components compose only through the new `program_version_component` table.
  - A linked major component never displaces the program's own major nodes.
  - **Same major, two schools:** one `shared_major` component is linked from each school's program version, and each composes its own school's gen-ed.
- **Admission stages.**
  - New fields: `requirement.available_after_stage` (inherited down the tree) and `program_version.admission_stage`.
  - `entry_stage` components name the stage and its label. A plan includes the entry requirements until a **verified** `admission` record exists; course completion never grants admission.
  - Planned courses carry `after_stage_label` ("after admission to RBS" / "after major declaration"), shown on plan cards.
- **Grade floors.** "C or better in all core and major courses" uses the existing inherited `min_grade` on a component root. It's tested through a linked component.
- **Class-year curricula.**
  - New fields: `program_version.curriculum_key` (now part of the unique key) and `curriculum_class_years`.
  - Which curriculum governs comes only from a verified `curriculum_class_year` record, never from a target graduation term.
  - `resolve_version` refuses to pick between curricula.
- **School-level rules.** `program_rule` can belong to a component. New rule types `residency_window`, `pass_fail_limit` and `double_count_limit` must be stored as not evaluable, with a reason; the audit reports them as NOT EVALUABLE.
- **Degree totals.** The order is program total, then school component total, then the existing SAS registry. Without any of them the total is none; 120 is never assumed.
- **Other.**
  - `min_course_credits` ("at least 3 credits").
  - `group_caps` ("at most one of 493/494").
  - `unresolved_conflicts` (nodes stay conditional).
  - `certification_codes`: eligibility reconciled from SOC per-term certifications.
  - An exempted all-of node now reports whole-number counts (it used to crash planning).

## 2. Gen-ed systems and shared cores (encoded, pending review)

All quotes are verbatim from archived captures, with sha256 in `data/programs/rutgers/sources.json`. Packets are in `components/review/`.

| Component | Kind | Nodes | Quotes | Unknown or not modeled |
|---|---|---|---|---|
| `sebs-core` | gen-ed (fall 2019+) | 19 | 25/25 | NS two-discipline rule; experiential approval outside courses; current P/NC (403) |
| `rbs-core` | gen-ed | 4 | 10/10 | **course lists unknown**: the catalog defers to Degree Navigator |
| `soe-school` | gen-ed + school rules | 2 | 8/8 | H/SS distribution rules (403); no school total (120–132 by major) |
| `rbs-business-core` | school core, fall 2026 entrants, after RBS admission, C floor | 19 | 27/27 | open **BAIT-ETHICS**; Accounting overrides go with the Accounting major |
| `rbs-entry` | entry stage `rbs-nb-admission` | 5 | 9/9 | foundation **course numbers unknown**; admission criteria (403) |
| `soe-first-year` | school core (004) | 11 | 13/13 | — |
| `soe-first-year-chem-ii` | school core, linked only where required | 1 | 3/3 | link guide in its packet |
| `soe-major-declaration` | entry stage, no entry requirements | 0 | 2/2 | declaration timing and requirements (403) |

**Sources.** Every catalog page was fetched with a plain GET and archived. Every department page (cs, soe, business, myRBS, sebs) returned **HTTP 403**. Those are recorded as failed captures, never bypassed. For the CS department pages I cite Phase A's raw HTML captures from 2026-11-09.

## 3. Course knowledge index

- **Coverage:** every course offered in every loaded term (24 terms). The dev build took 8 s: 32,461 memberships, 18 owners, 216 coverage rows.
- **Freshness:** an input-manifest hash, plus `ensure_fresh`. A stale build is labelled, and absence is never claimed from it. A failed build never replaces the ready one.
- **Readers:**
  - semester builder: its `MembershipProvider` seam, restricted to the planner's audit-consistent pools;
  - Options and Search: a verdict on each result;
  - assistant: `get_course_details.requirement_fit`, with the student's own status per requirement, computed at query time.

**Behavior change.** The builder used to label any unmapped course "free elective" whenever a degree total existed. A course is now "free elective only" only when its coverage is complete, fresh and reviewed. Otherwise it's "unknown", with reasons.

**Index coverage (dev DB, Spring 2027):** 4,667 courses offered; 1,055 map to at least one requirement node; 0 are "free elective only" (no context is complete).

| Owner | Memberships, all terms | Courses, Spring 2027 | Basis |
|---|---|---|---|
| `sas-core` | 11,234 | 479 | term certification |
| `sebs-core` | 10,497 | 469 | term certification |
| `soe-school` (SOEHS) | 3,818 | 703 | term certification |
| `rbs-business-core` | 272 | 15 (33:522:335 is offered in no term) | curated list |
| `soe-first-year` / `-chem-ii` | 144 / 7 | 8 / 1 | curated list |
| `rbs-core`, `rbs-entry` | 0 | 0 | course lists unknown |
| `sas-198-bs` | 969 | 61 | curated list |
| other SAS majors (7) | 235–1,362 | 20–52 | curated list / query |

In each SAS context, between 493 and 529 of the Spring 2027 offerings map. Every context is "incomplete", with reasons (unpublished components, and open conflict CS-INTRO-2027 for the CS B.S.).

## 4. CS B.S.

- **Electives:** the list now cites archived department captures. It's identical to the 2026-10-23 list.
- **300-level question:** answered by the department B.S. page: "At least five of the electives must be taken in the New Brunswick Department of Computer Science (i.e., they must be 01:198:xxx), of which at least two must be at the 300 level or above."
- **493/494:** at most one counts; modeled as a group cap.
- **Two open conflicts:**
  - **CS-INTRO-2027:** the department requires 01:198:121/122 from Spring 2027, while the catalog says 111/112. 111 isn't offered in Spring 2027, while 120 and 121 are.
  - **CS-BS-COPY:** the residency and ten-year clauses on the B.S. page are labelled "B.A.".

## 5. Report a wrong requirement

- `POST /requirements/reports` stores the program, the node, the message and the account id, and nothing else. Messages are 1–2000 characters, and an account may have at most 50 open reports.
- `GET` and `PATCH /admin/requirement-reports` are for administrators; the reporter appears only as a redacted reference.
- The button appears on every requirement row of the degree page and of what-if. The admin page is at `/admin/requirement-reports`.

## 6. Encoding pipeline

`scripts/encode_program.py`: `archive`, `new`, `validate`, `packet`, `load`, `index rebuild|status|coverage`. The whole procedure is in `docs/ENCODING_MAJORS.md`.

## 7. Tests

| Suite | Result |
|---|---|
| New engine tests (`test_program_expansion.py`) | 30/30 |
| Failure injection (`inject_program_expansion_failures.py`) | **15/15 detected** |
| Backend (`coursepilot_test`) | 1352 passed, **2 failed**, 4 skipped. Both fail on main too (`test_deployment_verification…fully_configured…`, `test_student_context_api…route_exists`) and are fixed by the unmerged `feature/test-fixes-and-cost-limits`. |
| Ingestion (`coursepilot_test`) | **1235 passed**, 1 skipped, 0 failed (59 min) |
| Frontend | **210/210** (21 files); `tsc` clean |

Synthetic students: one per school, clock frozen 2026-10-15, planning from Spring 2027.

| Synthetic student | Spring 2027 | "After stage" labels | Leaves met before | Requirements met after plan | Open: course list unknown | Degree total |
|---|---|---|---|---|---|---|
| SAS CS B.S. (real encoding, existing scenario) | 5 courses, 19 cr | 0 | 7/46 | 18/47 | 0 | 120 (SAS) |
| RBS pre-business (synthetic major + real RBS components) | 4 courses, 17 cr | **11 courses: "after admission to RBS"** | 1/24 | 13/25 | 7 (4 foundations, 3 Rutgers Core) | 120 (RBS) |
| SoE first-year (synthetic major + real SoE components) | 5 courses, 17 cr | 0 (first-year 004 courses come before declaration) | 4/13 | 9/14 | 0 | 128 (synthetic program total) |
| SEBS first-year (synthetic major + real SEBS Core) | 6 courses, 19 cr | 0 | 1/14 | 11/15 | 0 | 120 (SEBS) |

Every plan is `partial`, for honest reasons. The fixture has only Fall 2026 and Spring 2027, so later terms lack offering evidence. A few prerequisites can't be evaluated in the fixture, so those courses aren't planned. The RBS foundations and the Rutgers Core lists are unknown and stay open. No plan assumes admission.

## Migration and data

- **Backup first:** `pg_dump` of the dev DB to `C:\Users\shrih\coursepilot-backups\devdb-before-08b1-20261111T191413Z.dump`, outside OneDrive. It was verified readable (65 tables).
- **`p08b101`:** applied to the test and dev DBs. An upgrade/downgrade/upgrade round trip passed on a disposable database. `alembic check` reports only two drifts that predate this branch, both `assistant_conversation` defaults.
- **Dev DB data:**
  - loaded the 8 components;
  - reloaded the CS B.S. (machine re-validated to `validated`);
  - built the index.
- **No student record or saved plan was rewritten.** Saved plans aren't regenerated; the planning engine version isn't bumped.

## For you to decide or check

1. **CS-INTRO-2027.** Do 01:198:121/122 replace 111/112 for 2026-2027-catalog students? Spring 2027 doesn't offer 111. This affects plans now.
2. **BAIT-ETHICS.** The table says 33:522:335, but the notes and department pages say 334. 335 appears in no loaded term; 334 is offered.
3. **Department pages return 403 to plain HTTP.** Should a human save them from a browser (recorded as `browser_saved`), or should we keep them as evidence gaps? Blocked items include:
   - RBS foundation course numbers;
   - the RBS Rutgers Core lists;
   - SoE H/SS rules;
   - SoE declaration;
   - SEBS current Core and P/NC.
4. **Phase A register check.** IE-CATALOG-SCOPE says the catalog's Industrial Engineering curriculum totals 129. Both the Phase A capture and today's capture of that catalog page say **123**.
5. **CS B.A.** One quote has failed machine validation since before this branch: "five electives from a designated list…". The CS B.A. is unchanged and stays `needs_rereview`.
6. **Components have no review command yet.** Program versions use `registry_cli review`; components stay `pending_review` until one is added.
7. **SEBS crosswalk.** Confirm the bracket-to-SOC mapping: EL→XPL, SCL-SCA/ECN→SCL or ECN, AH→AHo–AHr.
8. **Pre-business home school.** Before admission a pre-business student is in SAS, and SAS Core applies. Composition follows the program's school (RBS), so this isn't modeled yet.

## Not done or not modeled (listed per component in its packet)

- Rolling residency windows, P/NC limits and cross-major double-count caps: recorded, not evaluated.
- The NS two-discipline rule, and experiential approvals outside courses.
- Class-year **selection in onboarding** for class-scoped programs; the engine refuses to pick.
- UI chips for index verdicts in Options and Search. The fields are in the API, and only plan cards render the stage label so far.
- Pilot major encodings (BAIT, IE, Microbiology, Biology) are the next wave.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
