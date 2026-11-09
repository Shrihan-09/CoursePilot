# Prompt 11 Part B checklist: AI Assistant v1

Status key: `todo` · `wip` · `done` · `blocked`. Updated as each sub-part lands.

## Reused, not rebuilt

| Existing piece | Used for |
|---|---|
| `app/llm/base.py`, `registry.py`, `providers/openai.py`, `providers/anthropic.py` | Provider boundary. Assistant roles added; no vendor type crosses it. |
| `services/planning/options.py` (`course_options`, `next_term_choices`) | `list_course_options` tool, guided-swap carousel |
| `services/planning/course_search.py` (`search_courses`, `interpret`) | `search_courses` tool (BM25 + aliases, resolved requirement IDs) |
| `services/planning/edits.py` (`preview_change`, `apply_preview`, `undo`) | `preview_plan_change`; apply only through a confirm token |
| `services/scheduling` (`load_term`, `schedule`) | Section pick that respects blocked times |
| `services/explanations/validation.py` | Grounding rules reused (academic-claim patterns, requirement-code check); extended for multi-tool turns |
| `docs/AGENT_ARCHITECTURE.md` | Router -> tools -> entity gate -> validator -> bounded retry -> fallback |

## B1 What it does

| Item | Status |
|---|---|
| Chat panel: "Assistant" nav entry and floating button on Plan | done |
| Questions answered from tools (placement, Core left, online options, failing a retake) | done |
| Change requests -> proposal card (Part A preview diff) with Confirm / Not now; applied only on Confirm | done |

## B1b Guided swap

| Item | Status |
|---|---|
| Ask 1-3 questions with chips (instead / format / anything else); skip ones already answered by the message or saved preferences | done |
| Carousel of 5-10 option cards: number, title, credits, catalog description (shortened, never generated), why it works, delivery, workload, hours, open status, offering label, Choose this / More like this | done |
| "Choose this" = confirmation: preview -> apply with the card's token, USER_SELECTED, 10 s Undo, side-effect line | done |
| Big side effect (moves another course, delays graduation, over the credit max, loses a requirement) -> diff + one more tap | done |
| Offer a section: schedule engine with blocked times, best 1-3 sections, tap saves to the term's schedule | done |
| Never auto-apply, never invent descriptions, never show an invalid course | done |

## B1c WebReg handoff (no bot)

| Item | Status |
|---|---|
| "Ready for WebReg" panel: index numbers, course, section, Copy all | done |
| Steps + link to WebReg's public login page (new tab) | done |
| Warnings: closed (consider a watch), special permission, time conflicts | done |
| No login, no credentials, no registration; "register me" is refused | done |

## B2 Architecture

| Item | Status |
|---|---|
| Typed tool layer over existing services (no new academic logic) | done |
| `apply_confirmed_plan_change` not callable by the model; needs a UI confirm token | done |
| Router: question / change request / preference change / out of scope (+ change count) | done |
| Grounding: every course, requirement, credit, offering or date claim from this turn's tool results; 2 regenerations, then template fallback | done |
| Labels pass through unchanged (estimated / DN audit, confirmed / historically offered, needs review, Preview programs) | done |
| Never "easy", no grade/GPA promises, never claims to register, footer disclaimer | done |
| OpenAI provider alongside Anthropic behind the registry; models from config; tool calling on the Responses API (native function tools, store=false) | done |
| Two tiers: default `gpt-6-luna`, escalation `gpt-6-sol` (config); escalate on 2+ changes or a failed grounding/validation turn | done |
| Vague topics expanded into keywords before `search_courses` (BM25/aliases, no embeddings) | done |
| Caps: daily messages per user, tool calls per turn, token budget per reply; usage logged as counts only | done |
| Privacy: only this student's tool output, no GPA unless asked, no conversation content in logs, deletable history | done |
| Key from env only; `.env` gitignored (verified: root `.gitignore` line 2) | done |

## B3 UI

| Item | Status |
|---|---|
| Streaming (activity events stream; the reply is shown after validation) | done (NDJSON activity, validated reply revealed progressively) |
| Suggested prompts | done |
| Proposal card: per-term before -> after, graduation, credits, workload | done |
| Collapsible tool activity line | done |
| Phone width | built (carousel scrolls, sheet is full-width on phones); visual check pending |

## B4 Evaluation

| Item | Status |
|---|---|
| 30+ golden conversations on synthetic students (CS B.S. undeclared + Calc II retake + rushing; CS B.A. senior; WebReg-only freshman; DN disagreement) | done |
| Coverage: explanations, same/different-requirement swaps, lighten, impossible requests, out of scope, invent-a-course attempts, multi-change requests | done |
| Scores: grounded, correct tools, valid proposal, no auto-apply, labels preserved | done |
| Fake provider in CI; real provider once locally | fake: done (42/42 both modes); real run blocked: no OPENAI_API_KEY in .env yet |
| Pass rate, average tokens, estimated cost: default-only vs with escalation | fake done; real-model numbers pending the key |

## Tests (Part B)

Router classification · ungrounded claim rejected · apply impossible without a token · caps enforced · labels pass through · no "easy" · GPA not sent unless asked · chat deletion · another user's plan unreachable · clarify skipped when known · carousel valid only · Choose this applies with undo · big side effect needs a second tap · section pick respects blocked times. Status: done (backend 42 assistant tests + 3 real-SOC tests; frontend tests written, run pending memory).

## Also in this task

| Item | Status |
|---|---|
| Availability rows pointing at missing archives (`openSections_2027_0_NB_20261104T023041Z.json`, `..._20261107T193340Z.json`): repoint to a same-sha256 file, else mark missing | done: both repointed to byte-identical files in `data/raw/live` (`archive_repair.py`); all 317 rows now resolve |
| Gitignore `ingestion/.tmp_*` and test-output folders; future test output outside OneDrive | done: `.gitignore` patterns + `test_output_guard.py` refuses an in-repo or OneDrive `--basetemp` |
