# Coursenav Assistant (Prompt 11 Part B)

A chat panel ("Assistant" in the nav, plus an **Ask** button on the Plan page)
that answers questions about a student's plan and progress, suggests course
options, and proposes plan changes. **It never changes a plan by itself:** a
change applies only when the student taps Confirm on a proposal card, or
"Choose this" on an option card.

## Setup

1. **Key.** Put the OpenAI key in the repository-root `.env` (gitignored, never
   committed; `backend/.env` also works when the API runs from `backend/`):

   ```
   OPENAI_API_KEY=sk-...
   ```

   Anthropic is optional. `ANTHROPIC_API_KEY` may stay empty; it is never
   read unless `ASSISTANT_PROVIDER=anthropic`.

2. **Provider and models** (defaults shown; all server-side, never request fields):

   ```
   ASSISTANT_PROVIDER=openai            # openai | anthropic | fake | none
   ASSISTANT_MODEL=gpt-5.6-luna         # default tier
   ASSISTANT_ESCALATION_MODEL=gpt-5.6-terra
   ASSISTANT_ESCALATION_ENABLED=true
   ```

   With no key the assistant reports "isn't set up on this server yet" and
   makes no call; nothing errors. `fake` is a deterministic offline policy for
   tests and UI work (tests always force it, whatever `.env` says).

3. **Caps:**

   ```
   ASSISTANT_DAILY_MESSAGE_LIMIT=50     # per student per UTC day
   ASSISTANT_MAX_TOOL_CALLS=8           # per turn
   ASSISTANT_MAX_OUTPUT_TOKENS=2000     # per model call
   ASSISTANT_TURN_TOKEN_BUDGET=60000    # input + output across one turn
   ASSISTANT_TIMEOUT_SECONDS=45
   ASSISTANT_PRICES=                    # optional price overrides for cost estimates
   ```

4. **Migration** `p11b01` (after `p11a01`): `cd backend && alembic upgrade head`.
   New tables only; no backfill.

5. **Eval:** `cd backend && python -m app.cli.assistant_eval` (fake) or
   `python -m app.cli.assistant_eval --provider openai` (real models). Reports
   go to a new folder under the system temp directory.

## How a turn works

```
message ─► router (deterministic)
             ├─ out of scope ("write my essay", "register me") ─► refusal + what it can do   no model call
             ├─ guided swap ("I don't want Art History…") ─► guided.py                        deterministic
             └─ question / change / preference
                  model ⇄ typed tools (≤ 8 calls, ≤ token budget)
                  reply ─► grounding gate ─► cards built from tool data
                     rejected ─► regenerate with the problems, on the escalation model
                     rejected again ─► template answer from tool data
```

* **Tools** (`services/assistant/tools.py`) wrap existing services only:
  `get_profile_summary`, `get_progress`, `get_plan`, `explain_course_placement`,
  `list_course_options`, `search_courses`, `get_course_details`,
  `preview_plan_change`, `find_sections`, `update_planning_preference`
  (preview only). **There is no apply tool.** Applying happens only in
  `actions.execute`, reached only through a one-time token minted when the card
  was shown and spent by the student's tap (`POST /student/assistant/actions`).
* **Search is BM25 + aliases, not embeddings.** The model expands a vague topic
  ("something about tech") into keywords before calling `search_courses`.
* **Two tiers.** The escalation model takes a turn that asks for 2+ plan
  changes, and the retry after a default-model reply fails grounding or
  validation (bad JSON, bad tool arguments, a refused proposal).
* **Grounding** (`grounding.py`, reusing Phase 5.1's claim patterns): every
  course, requirement code, credit number, term, offering label, prerequisite
  and graduation claim must come from this turn's tool results. Never: easy /
  hard ratings, grade or GPA promises, "I registered you", "I updated your
  plan", official-advising claims, or GPA the student did not ask about.
* **Cards are code, not model text.** Descriptions are the Rutgers catalog's
  first 1-2 sentences (or "No catalog description available"); labels
  (confirmed / historically offered, estimated / from your Degree Navigator
  audit, needs review, Preview — not yet reviewed) are copied verbatim.

## Guided swap (B1b)

1. Up to three questions with chips (what to finish instead, format, anything
   else). A question already answered by the message or saved preferences is
   skipped.
2. A carousel of up to 10 valid options from `list_course_options` /
   `search_courses`.
3. **Choose this** previews the swap and applies it when the effect is small
   (Undo toast for 10 s, one-line summary). A big effect (moves another course,
   delays graduation, over the credit max, loses a requirement) returns the
   diff for one more tap.
4. "Pick a section that fits my week": the Schedule Engine with the student's
   blocked times (hard) returns the best 1-3 sections; a tap saves it
   (`student_planned_section`).
5. **Ready for WebReg**: index numbers, Copy all, steps, a link to WebReg's own
   login page, and warnings (closed, special permission, time conflicts).
   Coursenav never signs in to WebReg and never registers anyone.

## Privacy

* The model sees only what tools return for the signed-in student; tools are
  bound to that student (no student id in any request).
* GPA reaches the model only when the student asks about GPA (enforced in the
  tool layer, whatever the model requests). Coursenav does not compute a GPA
  for display, so the real tool says so rather than guessing.
* Chat history is stored per student (`assistant_message`), deletable with one
  call (`DELETE /student/assistant/history`), and CASCADE-deleted with the
  student. Logs carry counts only (intent, tool calls, model calls, tokens,
  escalation, latency), never message text or tool output.
* Action tokens are stored only as SHA-256, expire in 30 minutes, are
  single-use, and are honoured only for the student they were minted for.

## Evaluation (B4)

42 golden conversations on four fabricated students
(`services/assistant/eval/`): explanations, same- and different-requirement
swaps, lighten, moves, multi-change requests, impossible requests (prerequisite
unmet, not offered, locked retake, delays graduation), out of scope, and
attempts to invent a course or prerequisite. Each is scored on grounded /
correct tools / valid proposal / no auto-apply / labels preserved, with no
auto-apply and no invented courses reported separately. CI runs it with the
fake provider (`tests/test_assistant.py`).
