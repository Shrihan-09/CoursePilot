# Prompt 12 implementation checklist

Baseline: origin/main f83faf2. Prompt saved under prompts/12-semester-builder-and-plan-redesign.md. This checklist was written before implementation.

| Area | Baseline | Evidence / work |
| --- | --- | --- |
| A0 compact choices | Not done | YourChoicesCard renders expanded, horizontally scrolling groups; replace with compact rows and a grouped modal, three options per group, Review collapse. |
| A semester/category layout | Partial | SemesterCard and CourseCard exist, with titles, workload, traces and options; board scrolls horizontally and codes lead. Reuse evidence, group courses and collapse semesters. |
| Full-term browser | Partial | course_search.py supports smart interpretation, BM25, requirements, section filters and gates, but defaults to open pools and truncates candidates to 240. Add a separate exhaustive term-browser adapter using the existing services. |
| Student-specific fills | Partial | OptionsContext.fills and term-aware candidate checks exist; no Prompt 08 index. Preserve unknown coverage; add a replaceable membership adapter. |
| Preview / confirm / undo | Done for single changes | Existing edits service has revision-bound previews and USER_SELECTED pins. Extend transactionally for a semester selection without bypassing gates. |
| Calendar / scheduling | Done as standalone service | Reuse schedule generation, blocked-time preferences, section evidence and weekly calendar; integrate selection and comparison in builder. |
| Assistant | Partial | Full Assistant page is merged. Side-panel commit remains unmerged; reuse awaits user's branch preference. |
| Phone / theme / tests | Partial | Existing tokens, overlays, unit and visual harnesses. Verify 360px, title-first layout, choices, browsing, gates, save and calendar. |

Execution order: A0 first, then A, then B. Frontend and backend test suites run sequentially; output outside repo/OneDrive. No development DB tests, no volume/database deletion, no gh. Stop servers started for verification. Finish with updated results, PR body, push and compare URL.

## Completed implementation

Rebased onto main `d618b21` after the user merged assistant-panel. The initial checklist above records the pre-implementation state.

| Area | Final status | Result |
| --- | --- | --- |
| A0 | Done | Compact Keep/Change rows, anchors on one line, grouped modal, three initial options, Show more / Browse builder, All set / Review, card inside next semester only. |
| A | Done | Collapsible semester/category sections, title-first cards, secondary codes, one fills chip, delivery icon, lock badges, workload summary, Details. |
| B browser | Done with current services | Exhaustive SOC primary-course universe; structured/smart/instructor search; requirement/Core-goal/degree-credit, delivery/campus/time/day/open/level/credit filters; counts, pagination, sorting, explicit gates and potential/unknown coverage. No Prompt 08 index was built. |
| Draft / save | Done | Live credits/workload, local add/remove, explicit supported pins with explanations, atomic set_term preview, revision guard, Confirm, USER_SELECTED preservation, Undo. |
| Calendar | Done as preview | Existing schedule engine and weekly calendar, section combinations, saved blocked times and campus preferences, conflict feedback; draft-aware comparison in a new tab. Existing eight-course limit applies. WebReg indexes are not saved by this preview. |
| Assistant | Done | Merged side panel receives term and current unsaved selection, including Ctrl+/. Suggestions validate and add to draft; no action-token application in builder context. |
| Responsive/theme | Done | Desktop columns, phone/tablet Browse/Selection/Calendar controls, persistent Preview footer, existing light/dark tokens. |

## Validation results

All suites ran **one at a time**. No real model calls or development database tests. Backend fixtures used isolated in-memory SQLite and archived public SOC with fabricated students; the existing DB guard remained installed. PostgreSQL-specific API fixtures were not run.

- TypeScript: `tsc --noEmit --incremental false` passed.
- Frontend: **177 passed**, 17 files, one worker.
- Backend regression: **136 passed** across semester builder, plan options, workload levels, search filters, builder contracts and assistant behavior.
- Backend follow-up: **15 passed**, covering the final browser adapter and engine version 7.8.1 on the existing CS planner fixture.
- Focused Core-goal filter check: **1 passed**, including selected/already-taken rows with short-circuited candidate evidence.
- Browser: **5 scenarios passed**, zero page errors, including rendered calendar, Ctrl+/ builder context, 360px/tablet tabs and persistent Preview footer. Synthetic local mock API; final results and captures are under `%TEMP%\coursepilot-12-ui-final`. No student database or model is used by the browser harness. Screenshots were visually inspected. Test servers and helper processes were confirmed stopped.

Logs: `%TEMP%\coursepilot-12-typecheck.log`, `coursepilot-12-frontend-final.log`, `coursepilot-12-backend-complete.log`, `coursepilot-12-backend-post-review.log`, `coursepilot-12-goal.log`, and `coursepilot-12-ui-final.log`. Next build output and temporary fixtures also remain outside the repository/OneDrive.

## Review descriptions / local captures

- `plan-desktop.png`: the next semester opens with compact Your choices above title-first Major/Core course cards; later semesters collapse.
- `choice-modal.png`: requirement controls switch groups, keeping alternatives in a compact modal rather than horizontal page content.
- `builder-desktop.png`: browser, selection totals/cards and weekly preview in three columns.
- `builder-calendar.png`: auto-picked section combination on the existing weekly calendar.
- `builder-assistant.png`: side chat shows the builder draft context and Add to semester suggestion.
- `semester-preview.png`: the standard diff and Confirm dialog; no write occurred before confirmation.
- `plan-360.png`, `builder-360.png`, `builder-selection-360.png`: phone layout with no page overflow and reachable Preview footer.
- `builder-768.png`, `builder-selection-768.png`, `plan-dark.png`, `builder-dark.png`: tablet controls and dark theme.

Captures are deliberately local, outside Git/OneDrive. See `docs/SEMESTER_BUILDER.md` for contracts and limitations and `docs/pr/PR_BODY_semester_builder.md` for the review body. Engine version 7.8.1 marks old plans out of date; the user triggers any rebuild. No migrations or automatic plan rewrites.
