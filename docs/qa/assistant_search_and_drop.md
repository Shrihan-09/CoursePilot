# Assistant async search and explicit drop QA

Branch: `feature/fix-assistant-search-course-drop`, based on main `75d0b47` after Prompt 12 merged. Scope: the two reported bugs; no new course knowledge index or program encoding.

## Diagnosis

The existing assistant-to-search adapter uses the same search function as direct search. Historical messages retain reply/flow but not tool arguments; the reported messages had no guided flow. The exact old arguments are therefore unavailable.

Read-only comparison on the existing record established:

| Query shape | Result |
| --- | --- |
| `term_code=20271`, `requirements=[[CORE_WCD]]`, `async_only=true`, no open-only/campus filter | 12 valid async results, including all four reported courses. |
| Four separate groups `[[CORE_HST],[CORE_WCR],[CORE_NS],[CS_ELECTIVES]]` | Zero: those groups are ANDed. |
| One OR group of alternative remaining requirements | Valid results. Before the cutoff fix, the broad pool omitted 37:575:202 although its WCd-only query and gate check passed. |
| Fixed local assistant route for the reported generic asynch request | One OR group, Spring 2027, async-only, no invented campus/open-only filter, valid cards, zero external model calls, plan revision unchanged. |

All four reported courses passed the relevant academic gates in that read-only comparison. The service has no 01:-only school-code restriction. Completed/in-progress courses and courses already planned in this/earlier terms remain gated; later planned courses can be moved through a preview. Async must fit one usable section, including mixed-delivery courses with an async section. The 240-candidate pre-filter cutoff and unrecognized `asynch` spelling were actual defects. Guided "different requirement" also lacked recognition and limited alternatives to three open requirements; it now searches the full remaining pool, including requirements planned later. An AND intersection reproduces the reported zero, but cannot be attributed to the unavailable original trace.

The attempted external replay was rejected by automatic approval review before execution because it would send private history to the model provider. No workaround or external replay followed. All subsequent checks were local.

## Tests and evidence

- `ingestion/tests/test_assistant_search_golden.py` consumes four cases from `REAL_DATA_SEARCH_CASES` against real archived SOC records and the actual database/search tools, using fabricated students and fake providers. Each requires `37:575:202`, `01:840:305`, `01:355:201` and `04:189:152`, correct format/term/grouping, and no automatic apply. The fourth exercises guided replacement rather than only the generic question route.
- The public fixture and its provenance are `soc_async_requirements_20271.json` and `soc_async_requirements_20271.provenance.json`. The original retrieval timestamp was unavailable; extraction time, source URL, full original-archive SHA and derived fixture SHA are recorded honestly. Only sections were subsetted; course facts were not invented.
- Drop integration covers Options-style remove, builder set_term, a 16-credit floor, no additions, exact credit decrease, requirement consequences, confirmation, persistence through a rebuild, empty-term visibility, explicit swap/Undo, and assistant replacement search only after the opt-in chip.
- Frontend tests render the 18 → 15 diff, warning, later requirement contribution and graduation change; the term offers Suggest a replacement without dispatching a change on render.

Intermediate results: 88 focused backend tests passed, then 75 workload/planner regression tests passed. Final checks/results are appended below. These runs overlap and are not additive counts.

| Completed check | Result | Output under `%TEMP%` |
| --- | --- | --- |
| Focused planner, drop, builder and assistant checks | 75 passed | `coursepilot-search-drop-final.log` |
| Empty-term, cap and builder-context follow-ups | 9 passed | `coursepilot-search-drop-final-extra.log` |
| Search filters including a 301-candidate late-unit regression | 11 passed | `coursepilot-search-drop-cap-test.log` |
| Final archived-SOC goldens, search behavior and existing assistant suite | 45 passed | `coursepilot-search-drop-guided-final3.log` |
| Fake-provider golden CLI, default and escalation | Each 42/42; each 100% no-auto-apply and no-invented courses | `coursepilot-search-drop-eval-final.log` |
| Frontend, one worker | 182 passed across 19 files | `coursepilot-search-drop-frontend.log` |
| TypeScript | Passed | `coursepilot-search-drop-typecheck.log` |

Final backend command (using `backend/.venv/Scripts/python.exe`):

```powershell
python -m pytest ingestion/tests/test_assistant_search_golden.py backend/tests/test_assistant_search_filters.py backend/tests/test_assistant.py -q -p no:cacheprovider --tb=short --basetemp "$env:TEMP\coursepilot-search-drop-guided-final3"
python -m app.cli.assistant_eval --provider fake --out <timestamped-directory-under-TEMP>
```

The commands ran sequentially with `PYTHONPATH=backend;ingestion`, `COURSEPILOT_ENV=ci`, `DEBUG=false`, `ASSISTANT_PROVIDER=fake` and bytecode disabled. Frontend used `tsc --noEmit --incremental false` and Vitest with a single worker. Fake-provider token/cost figures are synthetic and are not real-key performance or billing measurements.

Output is under `%TEMP%`, never the repository or OneDrive. In-memory SQLite fixtures use fabricated records and the test DB guard. PostgreSQL-specific integration fixtures were not run. No test called a real model. No migration or development-record write was performed.

## Flow descriptions

Search before: a generic async request could be converted to a catalog topic, accidentally intersect alternatives, or lose late-sorting courses before modality checks. After: grounded async cards use current requirement pools; the new real-SOC cases all include the four reported courses. If no courses pass, the response names the selected term, requirement logic, format, seat status and other active filters, then offers one-filter relaxation. It does not silently relax a filter.

Drop before: a remove re-ran the normal credit-floor filler and could replace the dropped course. After: the preview shows the lower credit count and explicit original-minimum warning, with requirement/later-term/graduation consequences. The same roster and warning survive Confirm and a rebuild. Empty semesters remain visible. Suggest a replacement is optional and never applies a suggestion. Choosing a specific replacement still uses the existing swap flow.
