# Part A synthetic walkthrough descriptions

Generated from the existing fabricated CS B.S. test student and public SOC fixtures. These are service-backed UI descriptions, not screenshots of a real student's account.

The Plan page puts the choices card above the semester board, with horizontally scrollable groups and explicit preview actions. Options opens a right-hand drawer on desktop and a bottom sheet on phones. A selected change opens a modal with the affected terms, credits, workload, graduation impact, and Confirm / Not now. Search shows interpreted filters above course cards or an explicit empty state. Component-render tests verify these labels and controls; these descriptions do not claim browser screenshot or visual accessibility verification.

## Your choices for Spring 2027

- Already set: CALCULUS II FOR MATHEMATICAL AND PHYSICAL SCIENCES (required retake)
- You have room for one Core course. Which do you want to knock out first? CoursePilot picked 01:810:135 (Arts and the Humanities).
  - Arts and the Humanities: 01:190:207 GREEK AND ROMAN MYTHOLOGY
  - Social Analysis: 01:920:101 INTRO TO SOCIOLOGY
- Science Requirement (B.S.): which one?

The card offers Let CoursePilot choose / Skip for now, recommendations for undecided alternatives, and Add a specific course. Picks request a preview.

## Options sheet

- same_requirement: Arts and the Humanities.
  - 01:190:207 GREEK AND ROMAN MYTHOLOGY: 3.0 credits; confirmed; async; workload 3.50, contact hours 0.00. Already in your plan for Spring 2028 - it moves here and 01:810:135 takes a later semester. -1.0 credits in Spring 2027 - the preview shows any other change.
- different_requirement: Social Analysis.
  - 01:920:101 INTRO TO SOCIOLOGY: 3.0 credits; confirmed; async, in_person; workload 3.00, contact hours 2.75. Fills Social Analysis now; you'd still need Arts and the Humanities later - the planner moves it to a later semester (the preview shows where), and 01:920:101 is no longer needed in Fall 2027.

## Swap preview

Valid: True. Synthetic-fixture preview: 1.00 seconds. Nothing is saved without Confirm.
01:810:135 out of Spring 2027; 01:920:101 into Spring 2027; 01:920:101 out of Fall 2027; 01:810:135 into Fall 2027; graduation unchanged
- Spring 2027: removes 01:810:135; adds 01:920:101; credits 19.0 → 19.0; workload 22 → 21.
- Fall 2027: removes 01:920:101; adds 01:810:135; credits 19.0 → 19.0; workload 20.54 → 21.54.

Confirm saves the revision; Undo is available in the toast for 10 seconds and persistently through Undo last change.

## Required search examples

### a different AHo course in Spring 2027, online

Term: 20271; requirements: ['aho']; topic: none; online: True; avoided days: []; excludes: [].

No course passes the gates and filters in this limited synthetic fixture corpus; the UI states that explicitly.

### another CS elective that's not 14:332

Term: 20271; requirements: ['cs elective']; topic: none; online: False; avoided days: []; excludes: ['14:332'].

No course passes the gates and filters in this limited synthetic fixture corpus; the UI states that explicitly.
'cs elective': nothing of yours is still open there.

### something about AI that counts for my major

Term: 20271; requirements: ['my major']; topic: ai; online: False; avoided days: []; excludes: [].

No course passes the gates and filters in this limited synthetic fixture corpus; the UI states that explicitly.

### a writing course that also fills diversity

Term: 20271; requirements: ['diversity', 'writing']; topic: none; online: False; avoided days: []; excludes: [].

No course passes the gates and filters in this limited synthetic fixture corpus; the UI states that explicitly.
'diversity': nothing of yours is still open there.
'writing': nothing of yours is still open there.

### Fridays off: what Core courses fit?

Term: 20271; requirements: ['core courses']; topic: none; online: False; avoided days: ['F']; excludes: [].

- 01:920:101 INTRO TO SOCIOLOGY — confirmed; async, in_person; fills Social Analysis · async · no F meetings.
- 01:190:207 GREEK AND ROMAN MYTHOLOGY — confirmed; async; fills Arts and the Humanities · async · no F meetings.

