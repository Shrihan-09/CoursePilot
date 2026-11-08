# Prompt 11: Course Options and Swaps, then the AI Assistant v1

> **Owner priority: HIGH.** Run **after Batch 0 and Batch 1** of `FIX-QUEUE.md` (correctness first; an assistant on top of wrong plans is worse than none).
> **Part A** ⚙️ High, then STOP, owner merges. **Part B** ⚙️ High, in a new chat.
> Follow `AGENTS.md` / `CLAUDE.md`. Start each part with a **done / partial / not done checklist** against existing code (planning engine, saved plans, `services/explanations/`, `backend/app/llm/`, `docs/AGENT_ARCHITECTURE.md`, `prompts/07-agent-integration.md`) and reuse what exists.

**Core rule: the LLM proposes; deterministic code decides.** Every option, swap and plan change is computed and validated by the planning engine. The assistant only interprets, calls tools, explains and proposes. A change is applied **only after the student confirms**.

---

## Part A: Course options, swaps and locks (the deterministic foundation)

### A1. "Options" on every planned course

Each course card in the plan gets an **Options** button. Its behavior depends on the course's role:

| Role | Options shown |
|---|---|
| **Anchor** (retake, pin, e.g. Calc II) | **Locked**, with the reason ("required retake"). It can be moved only to another valid term; no swap. |
| **Major / counts twice** | Other courses that satisfy the **same requirement(s)** this term. |
| **Filler / elective** | Two groups: **(1) Same requirement:** other courses filling the same open requirement this term. **(2) A different requirement instead:** other open requirements that can be satisfied this term, each with its best options. Example: swap "Historical Analysis (082:105)" for "Arts & Humanities (082:101 …)". |

**Each option shows:**
- course, title and credits;
- the requirement(s) it fills;
- offering label (confirmed / historically offered);
- workload weight and contact hours;
- delivery (async/online available? in person?);
- whether it fills 2+ requirements;
- **plan impact** ("keeps graduation Spring 2029", or "delays X to Fall 2027", or "you'd still need Historical Analysis later — moved to Fall 2027").

**Rules:**
- **Validity:** only courses that pass **every validity gate** for that term: offered, prerequisites (with planned/in-progress), not taken, not excluded, restrictions, honors gate, credit/course caps.
- **Order** by the student's preferences: format preference (async first if "flexible"), then fewer workload points when the term is over budget, then multi-requirement, then **"similar to courses you did well in"** (A3).
- **Never** the words "easy" or "hard".

### A2. Applying a swap: incremental replanning

- **Swap, move or remove:** picking an option → **preview** (a before/after diff of the affected terms, graduation term, credit totals and workload) → **Confirm** → saved. Nothing changes without Confirm.
- **Incremental:** recompute only what's affected. Keep every other course where it is unless a prerequisite/requirement chain forces a move, and list every forced move in the preview.
- **Course states:** `LOCKED` (student locked it, or it's an anchor), `USER_SELECTED` (the student swapped it in), `PLANNER_SELECTED`, `PROPOSED_CHANGE` (preview only, never saved).
- **Replanning respects** `LOCKED` and `USER_SELECTED` courses: the planner never silently replaces something the student chose. If a later change makes a student choice invalid, flag it "needs reselection".
- **Lock / unlock** a course or a whole semester. Unlocking returns it to `PLANNER_SELECTED`.
- **Undo:** keep the last few versions of the saved plan; "Undo last change".
- **Plan-level actions,** each with a preview first: "Lighten this semester", "Add a course", "Move this course to another term".

### A3. "More like what worked for me" (lightweight, owner request)

- **Signals:** courses on the confirmed record with grade A/A-/B+/B, plus current in-progress courses the student marks 👍 "doing well" (a new small toggle on current courses).
- **Features:** delivery (async/online), subject/department, course level, workload weight, Core area, plus catalog-description similarity (existing pgvector embeddings).
- **Use:** an ordering signal for options and fillers only ("Similar to 11:370:202, where you're doing well: async, 200-level"). It never overrides requirements or validity.
- **Privacy:**
  - grades are used **only** for the student's own ordering;
  - never shown to anyone else, never in social features, logs, analytics or issue reports;
  - a profile toggle "Use my grades to personalize suggestions" (default ON, can be turned off).

### A3b. The app asks: "Your choices for <next term>" (no AI needed)

After a plan is generated, and in the setup wizard right after "Build my plan", show a **"Your choices for Spring 2027"** card at the top of the Plan page. The app asks about every flexible slot in the next term; the student doesn't have to find the Options button.
- **One question per flexible slot** (filler/elective, or any requirement with several valid options this term). Example: *"You have room for one Core course. Which do you want to knock out first?"*
  - **Historical Analysis:** 3 top options shown as tappable cards.
  - **Arts & Humanities:** 3 top options.
  - **Social Analysis:** 3 top options.
  - **Let CoursePilot choose** (the default).
- **Choose-one requirements** (like the B.S. science sequence) are asked here too if not decided yet: "Physics or Chemistry?", with the recommendation marked.
- **Anchors** (Calc II retake) are shown as "Already set: Calc II (required retake)", with no question.
- **Picking an option** runs the same preview → confirm flow from A2; the result becomes `USER_SELECTED`. "Skip for now" keeps the planner's choice.
- **Re-asking:** the card shows again when a new term becomes "next term", and when a replan invalidates a choice.
- **The specific-course path:** a line under the card, "Want a specific course? **Ask the assistant**" (opens the Part B chat with the term prefilled), plus a plain **search box** fallback ("Add a specific course") that runs `search_courses` → options → preview → confirm. The fallback keeps it working when the assistant is unavailable or the daily cap is hit.

### A3c. Search by what students mean, not just course codes

Students won't say "01:082:105". They'll say "a different AHo course", "another CS elective", "something about AI", or "an online writing class". Build one **requirement-aware course search** (`search_courses`) used by the search box, the Options sheet and the assistant.

- **Inputs** (any combination):
  - a **requirement / goal**: a Core goal code (AHo, AHp, HST, SCL, WCd…), a major requirement ("CS electives", "science sequence"), or "anything that counts toward my degree";
  - **free-text topic** ("AI", "music", "psychology of…"), matched with **pgvector similarity over catalog descriptions** plus title keywords;
  - **filters**: term, online/async only, in person, campus, level (100–400), credits, days/times (respecting blocked times), open sections only, "fills 2+ of my open requirements".
- **Resolving words to requirements** happens **deterministically**: build a lookup of requirement names, goal codes and common aliases ("humanities" → AH goals, "history" → HST, "writing" → WC/WCr/WCd, "CS elective" → the CS electives list) from the program and Core encodings. Ambiguous phrases return a short "Did you mean…" list instead of a guess. Unknown phrases fall back to the topic search, labelled.
- **Output:** only courses that pass **every validity gate** for the chosen term (offered / historically offered, labelled; prereqs; not taken or planned; not excluded; restrictions; honors gate). For each: the requirement(s) it fills, its offering label, delivery, workload, open status, and a one-line "why it matched" ("fills AHo · similar to 'Minds, Machines and Persons'"). Courses that fail a gate can be shown under "Not available this term" with the reason, never mixed in.
- **Examples that must work** (and become tests):
  - "a different AHo course in Spring 2027, online";
  - "another CS elective that's not 14:332";
  - "something about AI that counts for my major";
  - "a writing course that also fills diversity";
  - "Fridays off: what Core courses fit?".

### A4. API (these become the assistant's tools in Part B)

Typed endpoints, each scoped to the signed-in student's own plan:
- `GET  …/plan/courses/{course}/{term}/options`: the options from A1;
- `POST …/plan/changes/preview`: body {change: swap / move / remove / add / lock / unlock / lighten_term, …} → the diff, or validation errors;
- `POST …/plan/changes/apply`: body {preview_id}, applied only if the plan hasn't changed since the preview (the version check);
- `POST …/plan/undo`.

### Tests (Part A)

- Options contain only valid courses, and an anchor has no swap.
- "A different requirement instead" defers the old requirement correctly.
- Preview → apply works, and applying a stale preview is refused.
- `USER_SELECTED` and `LOCKED` courses survive replanning, and an invalidated choice becomes "needs reselection".
- Undo restores the previous version.
- The similarity ordering uses only the owner's own grades.
- No "easy/hard" wording.
- Failure injection: an option that fails a gate, and a replan that drops a locked course.
- `plan_quality.py` timings: previews under 3 s typical.

**STOP after Part A:** push, write `docs/pr/PR_BODY_11a_options.md`, give the compare URL, and a short report with screenshots of the Options sheet and a swap preview.

---

## Part B: AI Assistant v1

### B1. What it does

There's a chat panel ("Assistant" in the nav, plus a floating button on the Plan page). Students can:
- **Ask questions:**
  - "Why is CS 206 in Fall 2027?"
  - "What do I still need for Core?"
  - "Which of my options are online?"
  - "What happens if I fail Calc II again?"
- **Request changes:**
  - "I'd rather do a humanities course than history this spring."
  - "Make spring lighter, I'm rushing."
  - "Swap Art History for something online."
  - "Move physics to fall."
- The assistant answers using tools, then shows a **proposal card**: the preview diff from Part A, plus **Confirm** / **Not now**. It applies the change only on Confirm.

### B1b. Guided swap flow (owner's main scenario, must work end to end)

Example: *"I don't want to take Art History, but I don't know what's better."*

1. **Ask first.** The assistant asks 1–3 short questions with **tappable quick-reply chips** (free text also works). It skips any question already answered by the message or the saved preferences.
   - **What to finish instead?** The same requirement (Arts & Humanities / Historical Analysis), a different open requirement (listing the student's open ones by name), or "whatever counts the most".
   - **Format:** async/online, hybrid, in person, or no preference.
   - **Anything else?** e.g. no Fridays, no mornings, a topic interest ("something about tech"), a lighter workload.
2. **Show options.** It calls `search_courses` / `list_course_options` with the answers, then shows a **horizontally scrollable carousel of option cards** (5–10). Each card has:
   - the course number, title and credits;
   - a **1–2 sentence description** from the catalog (shortened, not invented);
   - **why it works for you:** the requirement(s) it fills, plus matches to the stated preferences ("async · fills HST + WCd · similar to a course you did well in");
   - delivery, workload weight and weekly hours, open/closed status, and the offering label (confirmed / historically offered);
   - a **"Choose this"** button and a **"More like this"** button.
3. **Choose → apply.** Tapping **"Choose this"** counts as the student's confirmation for that one swap (the tap is an explicit choice, so no second confirm dialog is needed):
   - run `preview_plan_change` → if valid, `apply_confirmed_plan_change` with the confirm token from the tap;
   - mark the course `USER_SELECTED`;
   - show an **"Undo"** toast for 10 seconds, plus a one-line summary of side effects ("Art History moved out; Historical Analysis now fills via 01:510:101; graduation unchanged").
   - If the preview shows a **big** side effect (moves another course, delays graduation, exceeds the credit max), show the diff and ask for one more tap instead of applying straight away.
4. **Offer the section.** "Want me to pick a section that fits your week?" It calls the schedule engine with blocked times and preferences, shows the best 1–3 sections (days, times, campus, instructor, open seats), and the student taps one. The result is saved to the term's schedule.
5. **Hand off to WebReg** (no automation; see below): offer "Copy index numbers for WebReg".

**Never:** auto-apply without the "Choose this" tap; invent course descriptions; show courses that fail a validity gate.

**Tests:** the clarify step is skipped when preferences are already known; the carousel shows only valid options; "Choose this" applies with undo; a big side effect requires a second tap; the section pick respects blocked times.

### B1c. WebReg handoff (no bot)

CoursePilot **does not** log into WebReg or register for students. That would require the student's NetID, password and Duo, which breaks CoursePilot's no-credentials rule and likely Rutgers' acceptable-use policy. Instead:
- **"Ready for WebReg" panel** for a term: each chosen section's **index number**, course and section, plus a **"Copy all index numbers"** button.
- Short steps: "Open WebReg → choose term → paste index numbers → Add to schedule → Submit." Link to WebReg's public login page in a new tab.
- **Warnings:** closed sections ("closed — consider a watch"), special-permission sections, and time conflicts.
- Later (backlog): a "Notify me when this section opens" watch, using the existing availability feed.

### B2. Architecture

- **Tool layer:** typed functions wrapping existing services (no new academic logic):
  - `get_profile_summary`, `get_progress`, `get_plan`, `explain_course_placement`;
  - `list_course_options` (A1), `preview_plan_change` (A4), `apply_confirmed_plan_change` (A4, callable **only** after the UI's explicit confirm, never directly by the model);
  - `search_courses` (A3c: by requirement/goal, topic and filters; the assistant must call it for any "a different X / another Y / something about Z" request instead of naming courses from memory, and must pass the resolved requirement IDs, not guess them);
  - `get_course_details` (public data from Batch 4, if present);
  - `update_planning_preference` (via preview + confirm).
- **Router:** classify each message: question / change request / preference change / out of scope (e.g. "write my essay", "register me in WebReg"). Out of scope gets a polite refusal plus what it *can* do.
- **Grounding:**
  - every factual claim in a reply must come from a tool result in that turn;
  - reuse `services/explanations/validation.py` (or build an equivalent);
  - reject or regenerate replies that state ungrounded course facts, prerequisites, offerings or dates; after 2 failures, fall back to a template answer.
- **Labels pass through unchanged:**
  - "estimated" vs "from your Degree Navigator audit";
  - "confirmed for Spring 2027" vs "historically offered";
  - "needs review";
  - "Preview — not yet reviewed" programs.
- **It never:**
  - claims a course is "easy";
  - promises grades or GPA outcomes;
  - claims to register anyone;
  - gives official advising. Footer: "CoursePilot is a planning aid; your advisor and Degree Navigator are authoritative."
- **Provider:**
  - complete `AnthropicProvider` behind the existing provider registry;
  - API key from an env var (never committed); model name configurable;
  - the `echo`/fake provider stays the default in tests.
- **Cost and safety caps:**
  - a per-user daily message limit (configurable, e.g. 50);
  - max tool calls per turn (e.g. 8);
  - a token budget per reply;
  - log token usage (counts only, no message content with grades or GPA).
- **Privacy:**
  - send the model only what the tools return for this student;
  - never send GPA unless the student asks about it;
  - never log full conversations with grades; store chat history per user, deletable.

### B3. UI

- **Chat panel:** streaming replies, suggested prompts ("Make next semester lighter", "What Core do I have left?", "Show online options").
- **Proposal cards:** the plan diff (before → after per term, graduation, credits, workload), with Confirm / Not now.
- **Tool activity line** ("Checked your plan · Found 6 options"), collapsible.
- **Works at phone width.**

### B4. Evaluations (required before merge)

Write **at least 30 golden conversations** on synthetic students (CS B.S. undeclared with a Calc II retake and rushing; CS B.A. senior; a freshman with only WebReg; a student with a DN disagreement). Cover:
- explanations;
- swaps (same requirement / different requirement);
- lighten a term;
- impossible requests (prereq unmet, course not offered, would delay graduation);
- out of scope;
- attempts to get it to invent a course or prerequisite.

Score each one: grounded (no unsupported claims), correct tool use, proposal valid, no auto-apply, labels preserved. Report the pass rate. Target ≥ 90%, with 100% on "no auto-apply" and "no invented courses". Run with the fake provider in CI, and once with the real provider locally if a key is set.

### Tests (Part B)

- Router classification.
- An ungrounded claim is rejected.
- `apply` is impossible without a confirm token.
- Caps are enforced.
- Labels pass through.
- No "easy" wording.
- GPA isn't sent unless asked.
- Chat deletion works.
- Another user's plan can't be reached.

**STOP after Part B:** push, write `docs/pr/PR_BODY_11b_assistant.md`, give the compare URL, and a short report with the eval pass rate, 3 example transcripts, and setup steps for the API key and caps.

## Keep it cheap
- Run only the affected tests while working, then the full suites once per part.
- Delete temp folders and worktrees when done.
