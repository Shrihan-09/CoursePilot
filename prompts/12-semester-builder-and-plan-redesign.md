# Prompt 12: Semester Builder (CSP-style) and Plan Page Redesign

> ⚙️ **Effort: Medium.** Follow `AGENTS.md` / `CLAUDE.md`. Start with a **done / partial / not done checklist** against existing code (Plan page, Options sheet, search, schedule generator, assistant panel), and reuse what exists. Uses the **course knowledge index** from Prompt 08 if it's merged; otherwise use the existing search/options services.

## Why

The Plan page is a wall of course codes and long text. Students know classes by **name**, not "01:198:210". When they want to change a semester, they shouldn't be confined to the planner's shortlist. They should be able to browse **every** course offered that term, like Course Schedule Planner, with CoursePilot **recommending** courses, never restricting them.

## Part A0: Compact "Your choices" card (fix first)

The current card shows every question with every option as a full card and paragraphs of text, scrolling sideways off the screen. Replace it with:
- **One compact row per question**, e.g. "Core slot · pick 1 · CoursePilot picked *Intro to Philosophy*" with buttons **Keep** · **Change**. Anchors show as one line: "Already set: Calc II (retake)".
- **Change** opens a **bottom sheet / modal** (not inline) with the options grouped by requirement in tabs or a segmented control (e.g. Historical Analysis | Natural Sciences | Writing). Each option is **one compact row**: title, credits, one "fills" chip, delivery icon, open/closed, and a **Choose** button. The long explanation ("similar to…", "fills… the planner moves…") goes behind a small ⓘ / "Details".
- **At most 3 options per group** visible, plus "Show more" / "Browse all in the semester builder" (Part B).
- The whole card is **collapsible**, collapses itself once all questions are answered ("All set for Spring 2027 · Review"), and lives at the top of the next semester's section only.
- No horizontal scrolling of the page at any width; it works at 360px.

## Part A: Plan page redesign

- **Organize by semester, then category.** Each semester is a collapsible section (the current/next semester open by default, others collapsed). Inside it, courses are grouped under: **Major** · **School Core / gen-ed** · **Electives** · **Free elective**. Anchors (retakes, pins) show a small lock badge in their group.
- **Course cards: title first.** A big title ("Data Management for Data Science"), with a small muted code (01:198:210), credits, one chip for what it fills ("CS elective"), a delivery icon (online/async/in person), and an **Options** button. Everything else (workload math, "why this term", prerequisites, offering label) goes behind a **"Details"** tap.
- **Semester header:** credits, a workload bar against the budget, the number of major courses, and one-line "why this fits you". Long notes (commitments, budget explanations) go behind an ⓘ.
- Use **human names** everywhere: course titles, requirement names. Codes are secondary.
- Works at phone width; light and dark mode.

## Part B: Semester builder (CSP-style)

From each semester's header, a **"Build / change this semester"** button opens a builder for that term:

- **Left: course browser** over **every course offered that term** (the full SOC term data, not a shortlist). It has:
  - **search** by title, code, subject, instructor or topic (existing smart search);
  - **filters:** what it fills for me (my major / a specific requirement / a Core goal / any degree credit), still-needed only (default on), delivery (async/online/hybrid/in person), campus, days and time window (respecting blocked times), open sections only, level, credits;
  - **each result** shows the title (big), code, credits, what it fills **for this student** (from the course knowledge index plus the student's status), prerequisite status (met / met if earlier planned courses pass / not met: which), open/closed, and delivery;
  - **"Recommended" badges** (from the planner and the "more like what worked for me" signals), sorted first by default. The student can sort by name, credits or what it fills instead.
- **Middle: this semester's selection.** Courses currently in the plan for the term, plus added ones, with credit and workload totals live against the range and budget. Add or remove freely.
- **Right: weekly calendar preview.** Pick sections per course (or "auto-pick best sections" with the existing schedule engine, respecting blocked times). It shows conflicts and campus changes, and links to the existing schedule options comparison.
- **The student isn't confined.** Any course that passes validity gates can be added, even if it fills nothing, in which case it's labelled "counts only as free elective" / "doesn't count toward your degree". Courses that fail a gate (prerequisite unmet, restricted, not offered) are still visible with the reason, and can't be added, except as a pin with an explanation where the engine allows it.
- **Save:** builds a plan change through the existing preview → confirm flow, shows the diff (graduation, later terms affected, requirements newly unmet), then applies. Added courses are `USER_SELECTED`; the planner never silently removes them.
- **The assistant inside the builder:** the side panel knows the builder's term and selection. "Recommend something for my last slot" returns **suggestions** the student can add with one tap. It never auto-replaces.

## Tests

- Plan grouping by semester and category.
- Title-first cards.
- Builder search covers every course in the term (count matches the SOC term data minus gate failures shown separately).
- "What it fills" matches the index.
- Adding a non-counting course is allowed and labelled.
- A gated course can't be added.
- Save goes through preview → confirm, and the diff is correct.
- Calendar conflicts are detected.
- Phone layout.

Run frontend and backend tests one at a time (16 GB RAM). Push, write `docs/pr/PR_BODY_semester_builder.md`, give the compare URL, and STOP with screenshots or descriptions.
