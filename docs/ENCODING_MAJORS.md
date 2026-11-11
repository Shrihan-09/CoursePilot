# Encoding a major (or a school component)

The steps below turn one Rutgers program into a reviewable CoursePilot encoding. They're the same every time, so each wave of majors is mechanical. Each step is repeatable, and no step decides that an encoding is correct. Only a named human review does that.

```
official sources ──archive──▶ capture (sha256) ──encode──▶ JSON ──validate──▶ machine checks
                                                                 └──packet──▶ .review.json / .review.md ──▶ human review
```

## Rules that never bend

1. **Never invent a requirement.** Encode only what an archived source says, and quote it verbatim. If the catalog defers to a department page or Degree Navigator, archive that page or record the item as **unknown**. Never fill it from memory, another school, or a similar program.
2. **Use both source families.** Archive the official catalog page and the official department or school requirements page. If they disagree, record a conflict with both claims; neither source wins by default (CLAUDE.md, *Program requirement sources*).
3. **Admission is never assumed.** Entry requirements go in an `entry_stage` component. Courses taken after the stage carry its label ("after admission to RBS"). Completing courses never counts as admission; only a verified record does.
4. **Sample four-year plans are guidance.** Semester placement is not a requirement unless a source says it is binding.
5. **Synthetic data stays synthetic.** Tests use fabricated students and programs labelled TEST. A real student's audit never enters the repository.
6. **Encoding is not review.** Every new encoding stays `pending_review` (components) or `parsed`/`validated` (programs) until a person reviews it against the sources.

## Step 1: archive the sources

```
python scripts/encode_program.py archive \
    <id> <url> [<id> <url> ...]
```

- Archiving is a plain HTTP GET with a generic client name and a 1.5 s pause between requests. No browser engine is used, and no bot protection is bypassed.
- The bytes are saved as `data/source-archive/<sha256>.<ext>`. That folder is gitignored.
- The command appends a record to the **committed** manifest `data/programs/rutgers/sources.json`: requested and final URL, HTTP status, UTC time, sha256 and byte count of the saved bytes, representation, and archive path.
- A refused request (HTTP 403, a timeout) is recorded as `representation: "failed"`. A failed capture cannot be quoted.
  - If a human saves the page from a browser, add it by hand with `representation: "browser_saved"` and a note.
  - A reader or text view must be labelled `extracted_text`.

Use stable, descriptive capture ids, for example `sebs-catalog-core` or `rbs-catalog-bait`.

## Step 2: write the encoding

Start from a template:

```
python scripts/encode_program.py new program   --code 136 --school RBSNB --out data/programs/rutgers/nb-undergrad/2026-2027/rbsnb-136-bs.json
python scripts/encode_program.py new component --code rbs-business-core --school RBSNB --component-kind school_core \
       --out data/programs/rutgers/nb-undergrad/2026-2027/components/rbs-business-core.json
```

### Which file?

| What it is | File | Composition |
|---|---|---|
| A major, degree or option | program file (`<school>-<code>-<degree>.json`) | its own nodes, plus `components: [...]` it links |
| A school's gen-ed (SAS Core, SEBS Core, SoE H/SS, RBS Rutgers Core) | `components/*.json`, `kind: gen_ed` | `"school"`: composed with every program of that school and year |
| Requirements shared by every major of a school (RBS business core, SoE first-year 004) | `kind: school_core` | `"linked"`: only programs that list it |
| What comes before a stage (pre-business, pre-declaration) | `kind: entry_stage` plus `stage: {code, label}` | `"linked"`; exempted by a verified `admission` record |
| One major offered by two schools (Biological Sciences under SAS and SEBS) | `kind: shared_major` | `"linked"` from **each** school's program file; each program keeps its own school's gen-ed |

Encode a shared component **once** and link it. Never copy its nodes into each major. When a requirement applies only to some majors or classes (SoE Chemistry II, for example), make it its own component and link it only where it applies.

### Node fields

| Field | Meaning | Source example |
|---|---|---|
| `requirement_type` | `all_of`, `any_of` (with `min_count`), `choose_n` (with `min_count`), `credits` (with `min_credits`), `course` | |
| `courses` / `course_categories` / `eligible_course_query` | curated eligibility; never invent a course | "01:198:111, 112, 205…" |
| `certification_codes` | eligibility from SOC's **per-term** `coreCodes` (`SOEHS`, `CCD`, `ECN`…) | "[SCL-GVT]", "SOE: Approved Humanities/Social Science" |
| `course_list_unknown: true` | the source names the slot but not its courses (with a `notes` reason) | "Calculus (3-4)", "see Degree Navigator" |
| `min_grade` | the minimum grade for this node **and its subtree** | "with a grade of C or better" |
| `min_course_credits` | every counted course must carry at least this many credits | "must be at least 3 credits" |
| `group_caps` | at most N courses from a group | "at most one … 493/494" |
| `available_after_stage` | courses here come after the stage (nearest ancestor wins) | business core: `rbs-nb-admission` |
| `unresolved_conflicts` | open conflict ids from the file's `conflicts`; the node stays conditional | `BAIT-ETHICS` |
| `quotes` | `[{source: <capture id>, quote: <verbatim>}]`; " ... " separates elided parts | |

A leaf `course` node may rely on its parent's quote ("six required courses … 205, 206, 211"); the packet shows it as "via" the parent.

### Program-level fields (`program_version`)

| Field | Use |
|---|---|
| `curriculum_key`, `curriculum_class_years` | Two complete curricula for different classes in the same catalog year (Industrial Engineering classes 2026–2029 and class 2030). The governing curriculum comes from a **verified** class-year record, never from a target graduation term. |
| `admission_stage` | The program's own nodes come after this stage. |
| `degree_credits` | `{credits, capture, quote}`: the program's own total (SoE majors, 120–132). Without it, the school component's total applies. Without either, the plan says there is no total; 120 is never assumed. |

### School rules (`school_rules` in a component, `program_rules` in a program)

The evaluated types are `min_gpa` (scope `cumulative` or `subject:<code>`), `max_grade_count`, `course_exclusion` and `residency`.

Recorded but **not yet evaluated** are `residency_window` ("12 of the last 42"), `pass_fail_limit` and `double_count_limit`. Each needs `is_evaluable: false` and a `not_evaluable_reason`. The audit then reports them as NOT EVALUABLE and never calls the degree complete while one is unchecked.

### Always fill these, even if the list is empty

- `rules_not_yet_modeled`: `{rule, why_not, source?, quote?}`
- `unknowns`: what the sources don't say, or what wasn't archivable
- `conflicts`: `{id, status: "open", summary, claims: [{source, quote}, ...]}`
- `review_questions`: what a human must decide

## Step 3: validate

```
python scripts/encode_program.py validate <files...> [--with-db]
```

It checks:

- **Shape:** the shape the loaders accept, one root per file, known kinds and applicability keys, and that unevaluated rule types carry a reason.
- **Sources:** every cited source is a manifest capture, its bytes match the recorded sha256, and it isn't a failed capture.
- **Quotes:** every quote appears **verbatim** in its capture's text, ignoring whitespace and typographic quotes or dashes.
- **Coverage:** every node that names courses or codes quotes something (itself or an ancestor); every `course` or `choose_n` node names courses or says `course_list_unknown`; every conflict id is declared.
- **With `--with-db`:** named courses exist (missing ones are reported, never created) and linked components are loaded.

A validation error is a stop. Fix the encoding; never weaken the check.

## Step 4: review packet

```
python scripts/encode_program.py packet <files...> [--with-db]
```

This writes `review/<stem>.review.json` and `.review.md` next to the file. A packet contains:

- the sources table, with failed captures shown as evidence gaps;
- machine checks;
- open conflicts with both claims;
- every node's encoding beside its verbatim quote(s);
- rules, rules not yet modeled, unknowns, review questions;
- the command that records a decision.

## Step 5: load (test database first)

```
python scripts/encode_program.py --database-url <..._test URL> load <components first> <then programs>
```

- Load components before the programs that link them; a program naming an unloaded component is refused.
- Loading is idempotent. A changed definition resets review (`validated → parsed`, `published → needs_rereview`).
- The command prints the target database name. Load into the development database only after the test suite passes, and back it up first if a migration is involved.

## Step 6: rebuild the course knowledge index

```
python scripts/encode_program.py index rebuild     # rebuilds only if inputs changed
python scripts/encode_program.py index coverage --term 20271
python scripts/encode_program.py index status
```

The index is the table course × term × requirement node, with provenance, for every course offered in every loaded term. Search, Options, the semester builder and the assistant read it.

- **Freshness:** an input-manifest hash covers definitions, lifecycle states, eligibility rows, offerings and certifications. Any change makes the build stale; a stale build is never presented as current.
- **"Free elective only"** appears only when a program's coverage is complete. That means:
  - every composed owner is published;
  - no course lists are unknown;
  - no conflicts are open;
  - SOC certifications are loaded for the term;
  - a degree total is sourced.

  Otherwise an unmapped course is "unknown", with the reasons listed.

## Step 7: tests

For each wave, write a synthetic student per affected school with the clock frozen (`ACADEMIC_TODAY`). The student plans the next term on real public SOC records, extracted with a fixture script like `scripts/make_program_expansion_fixture.py`. Assert:

- the plan includes the entry stage's requirements and never assumes admission (stage labels present);
- grade floors, credit minimums, caps and class-year rules hold;
- unknown course lists stay open and are never filled from elsewhere.

Every engine change also needs a fault in `scripts/inject_program_expansion_failures.py` that the tests detect.

## Wave checklist (one row per program)

| program | sources archived (catalog + department) | encoded | validated | packet | loaded (test) | reviewed by a human | rules not modeled |
|---|---|---|---|---|---|---|---|
