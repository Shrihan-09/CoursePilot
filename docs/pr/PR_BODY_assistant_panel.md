# Assistant side chat and conversation history

Students can open CoursePilot from the top-right header on signed-in pages,
including setup, without leaving their work. The right drawer is 400px on desktop
and a full-screen phone sheet. Ctrl+/ toggles it; Escape closes it. Its blue glow
uses the wizard tokens, pulses only before that account's first open in this
browser, and stays steady afterward. Reduced motion disables pulse and slide.

The drawer shares its selected conversation and live cards with the full Assistant
page through Expand. Plan supplies the current planning term; course Options
supplies the course, term and an editable alternatives prompt. The context chip
is removable. New chat and History support separate histories, deterministic
six-word titles, rename and confirmed deletion. History shows the latest activity
first. The daily cap remains visible and shared across chats.

Existing tool-backed proposal cards, the horizontal options carousel, Choose this,
second confirmation for a big effect, section selection and WebReg are reused.
Undo toasts render inside the native dialog so their buttons remain interactive.
Live turns and confirmation tokens stay in memory; browser storage holds only
per-account discovery/session flags and the selected conversation id.

## Storage and migration

`p11b02` follows `p11b01`. It adds conversation title/archive columns and a
conversation foreign key for action tokens. Existing messages are consolidated
into one conversation per student, titled from the first user message; no model
call or backfill command is needed. CRUD and conversation-selected status/message
routes enforce the signed-in owner; another owner's id returns 404. Deletion
cascades to that conversation's messages and tokens. The downgrade removes the
new metadata/token link while retaining messages.

The dev database was backed up with pg_dump before upgrading to p11b02. Archive:
`C:\coursepilot-private\backups\coursepilot-pre-p11b02-20261109-132256.dump`,
26,250,578 bytes; archive listing verified. Before/after hashes matched across all
19 existing student-related tables. There were no existing dev assistant messages.
No private backup or test artifact is included in this branch.

## Validation

- Backend: 48 tests passed against `coursepilot_test` only, including conversation
  CRUD/ownership, separate history, titles, cascade deletion and a transactional
  legacy-message migration/downgrade exercise.
- Frontend: TypeScript check and 174 unit tests passed, with one worker.
- Six synthetic Chrome scenarios cover Plan context, Options prefill, 400px
  carousel/proposal layout, Choose this/Confirm/Undo/WebReg, Expand with live
  tokens, history rename/delete, cap display, Ctrl+/, Escape, session restoration,
  account-specific discovery, full-screen phone layout and reduced motion.
- Browser scenarios use fabricated HTTP fixtures, not a real database or model.
  Existing backend assistant tests validate deterministic behavior separately.
- Test logs, temporary Next build, browser results and reviewed screenshots are
  outside the repository/OneDrive. Backend and frontend tests run sequentially.
  All servers started for these checks are stopped afterward.

## Review notes

Built from `origin/main` (`0c835c8`) as requested. The subsequent Responses API
commits on `feature/11b-assistant` are not part of main and were not carried into
this branch. Model/provider behavior and academic validation are unchanged here.
The first-open preference is per account in this browser, not synced across devices.
Historical cards retain the existing rule: after a full browser reload their
confirmation tokens are unavailable in the UI; ask for fresh options. Expand within
the current app session preserves the live cards and their tokens.

Manual review: open Plan, use the header sparkles button, check its term chip,
open a course's Options and ask for alternatives, then walk through a guided swap
inside the panel. Review the diff before Confirm; use Undo and the WebReg handoff.
