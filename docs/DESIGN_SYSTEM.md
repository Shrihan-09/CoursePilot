# CoursePilot design system (Phase 7.1)

CoursePilot should feel **modern, precise, intelligent, calm, trustworthy,
student-focused, and premium without being flashy**. It is a student
academic-planning product, not an analytics dashboard.

The interface presents what CoursePilot's deterministic backend computed. It
never decides academic facts itself: whether a requirement is complete, whether
a course counts, prerequisites, GPA, eligibility, schedule conflicts or section
validity. Visual simplicity must never hide uncertainty.

Implementation:

- `frontend/src/app/globals.css` (tokens);
- `frontend/tailwind.config.ts` (semantic names);
- `frontend/src/components/ui/*` (primitives);
- `frontend/src/components/brand/*` (mark and motion).

The `/design` page, which is available in development only, renders all of it.

## Brand

The supplied logo is the source of truth: a blue graduation cap with a white
upward navigation arrow, dimensional, rounded and strongly silhouetted. It is
**never redrawn, recoloured or regenerated**. See `frontend/brand/README.md` for
every derived asset and how it was made.

## Colour: neutral first, CoursePilot blue for emphasis

All colour is semantic tokens, defined as CSS variables. Components never use
literal colours.

| Token | Light | Dark | Use |
|---|---|---|---|
| `background` | #FFFFFF | #0A0B0D | page |
| `surface` | #F7F8FA | #111317 | quiet sections, sidebars |
| `surface-elevated` | #FFFFFF | #171A1F | cards, dialogs, menus |
| `surface-hover` | #F0F2F5 | #1E2228 | hover, skeletons |
| `fg` (text-primary) | #0B0D12 | #F3F5F8 | primary text |
| `fg-secondary` | #4A5162 | #B4BAC5 | supporting text |
| `fg-muted` | #666E7C | #8A92A0 | hints, metadata (≥ 4.5:1) |
| `border` / `border-strong` | #E3E6EB / #C9CED6 | #262A31 / #353A43 | dividers / controls |
| `primary` | #016DF4 | #016DF4 | the logo's core blue: primary actions, active nav, selection, progress |
| `primary-hover` | #0154E0 | #0E93FC | the logo's deep (light) / bright (dark) blue |
| `primary-subtle` | #EAF3FF | #0D2447 | selected rows, active nav background |
| `primary-text` | #0154E0 | #5AA9FF | blue as TEXT (links), readable on each background |
| `focus` | #016DF4 | #4D9FFF | focus rings |
| `success` / `warning` / `danger` (+ `-subtle`) | #0B6B4C / #9A5800 / #C7292F | #3FB68B / #F0B04A / #F26D6D | states, always with icon and words |

**Blue comes from the mark.** Sampled from the source artwork:

| Name | Value |
|---|---|
| highlight | #3CC8FD |
| bright | #0E93FC |
| core | #016DF4 |
| deep | #0154E0 |
| shadow | #0049D3 |

The core blue carries white text at 4.67:1, which passes WCAG AA, so primary
actions use the logo's own colour in both themes.

**Blue is reserved.** It marks:

- the one primary action on a view;
- active navigation and selected controls;
- progress, links and focus;
- CoursePilot's generation motion.

Cards, backgrounds and most buttons stay neutral. A mostly neutral interface is
what makes the blue identity strong. Gradients appear only in the logo itself.

**Dark mode is designed, not inverted:**

- a near-black canvas with charcoal elevation steps, where each surface is
  lighter and depth never depends on shadow;
- softened text, so white does not glare;
- the same logo blue for fills;
- a lighter blue only where blue is text.

## Appearance

- The options are System (the default), Light and Dark, set in Settings.
- A script in `<head>` applies the theme **before first paint**, so the wrong
  theme never flashes.
- The preference is stored under a single `localStorage` key holding only
  `light` or `dark`; System removes the key. It is never stored next to student
  data.

## Typography

**Geist Sans and Geist Mono** are self-hosted through the `geist` package, so
there is no external font request.

- Sans handles text. Its numbers are clear and it holds up in compact, dense
  layouts.
- `.code` (Mono, tabular) is used for course codes (`01:198:111`), sections and
  term codes, so codes align and their characters are unambiguous.
- `.tabular` is used for credits, GPA, counts and times.
- Hierarchy:
  - one `text-2xl`/`3xl` page title;
  - `text-xl` section titles;
  - `text-sm` body;
  - `text-xs` metadata.
- Density: compact rows (12–16px padding) rather than giant cards.
  Information density is not traded away for minimalism.

## Shape and depth

- Radii: 6px (controls), 8px (default), 12px (panels).
- Shadows only where something floats (dialogs, sticky action bars).
- No glassmorphism. The translucent top and bottom bars use a single backdrop
  blur so content scrolls under them legibly.

## Primitives

These are built only as far as Phase 7.1 needs, in patterns later phases can
extend:

| Primitive | Notes |
|---|---|
| Button | primary, secondary, ghost, danger, link; sizes give a ≥ 40px hit area |
| IconButton | requires a label |
| Input, Select, SearchInput | `Field` wires label, hint and error ids |
| Checkbox, Radio | native controls inside 40px labels |
| Tabs | arrow-key navigation |
| Badge, StatusBadge | **icon + text, never colour alone** |
| Alert | calm: an icon and a plain sentence |
| Toast | announced through `aria-live` |
| Dialog, Sheet | native `<dialog>`: focus trap, Escape and inert background for free; Sheet is a bottom sheet on phones, a right drawer on wider screens |
| Tooltip | shown on hover and focus |
| Progress, Skeleton | |
| EmptyState, ErrorState | ErrorState carries a request reference |
| PageHeader | |

## Responsive architecture

| Width | Navigation |
|---|---|
| < 1024px (phones, small tablets) | top bar + **bottom tab bar** (Home, Degree, Plan, Schedule, More) within thumb reach; secondary areas in a More sheet; safe-area insets respected |
| ≥ 1024px (laptop, desktop) | persistent sidebar |

- Areas not yet built (Degree, Plan, Schedule, Courses, Notifications,
  Assistant) are present in the architecture and marked **"Later"**, never
  shown as dead links.
- Layouts are designed per width, not shrunk:
  - the onboarding step indicator collapses to numbers on phones;
  - the review summary grid goes from 4 columns to 2;
  - action bars become sticky bottom panels.

## Motion language

Motion communicates state or progress, never decoration. There are three
different kinds of "loading":

| Kind | Treatment |
|---|---|
| **App initialization** | No splash and no artificial delay. If the app is ready, it shows. |
| **Ordinary data loading** | Skeletons, inline progress, a spinner inside a button. **Never** the branded animation. |
| **CoursePilot generating something substantial** (a semester or multi-semester plan, a schedule, a significant replan) | `GenerationAnimation` |

**GenerationAnimation** (`components/brand/generation.tsx`):

- **The cap is the anchor.** A simplified plan sheet starts *behind* it and
  emerges over the cap's top edge.
- **It travels along the logo's own arrow vector.** The vector is measured from
  the artwork: from the tail at lower-left (~22%, 86%) to the tip at
  upper-right (~84%, 24%).
- **It continues onward,** as if CoursePilot made the plan and is sending it
  into the interface.
- **On completion the sheet comes forward and resolves into the result**
  (`onResolved`), never into a spinner.
- **Stages are passed in by the caller** and should mirror real backend stages.
  The default caption credits CoursePilot's **deterministic** engine and never
  implies an LLM decides anything.
- **Implementation:** pure CSS keyframes and SVG, with no animation library.
  `z-index` is never animated. Visual QA caught that interpolating it put the
  sheet in front of the cap.
- **Reduced motion** (`prefers-reduced-motion`, or the `reducedMotion` prop):
  there is no flight. The static mark plus the same stage list carry the same
  information, and every animation in the app is reduced globally.
- **Responsive:** the size is `clamp(9rem, 42vw, 15rem)` and can be overridden
  with `--cp-gen-size`.
- **7.1 builds the component only.** It connects to the Planning Engine UI in
  the core-product phase.

## Transcript review

The student must see at once what was recognized, what is uncertain, what needs
them, and what confirming will do.

**Row states.** Each has an icon, a label and a one-line explanation:

| State | Icon | Label |
|---|---|---|
| matched | check | "Recognized" |
| needs_review | triangle | "Needs your review" |
| unrecognized | question | "Not recognized" |
| conflict | compare | "Conflicts with your record" |
| invalid | ban | "Can't be saved" |

**On the screen:**

- **Document-level warnings** (out-of-order reading, term totals that don't add
  up, a possibly missing page) appear as calm amber alerts above the rows.
- **A "Needs you" filter** is the default view.
- **"What the transcript said"** shows the original line, page and line number
  for every row.
- **Courses with no grade yet** are never assumed to be in progress. The student
  is *offered* "Mark as in progress".
- **A sticky action bar** states the outcome in plain words, for example
  "Confirming adds 9 courses…", or what still blocks confirmation.

## Accessibility (verified)

Visual QA rendered every screen in light and dark at 320, 390, 820, 1280 and
1600px, and audited each one with **axe-core (WCAG 2 A/AA)**, a
horizontal-overflow check and a touch-target check.

- **Final result:** 56 audited screens, 0 findings.
- **Fixed along the way:**
  - step-indicator overflow on phones;
  - light-mode success contrast;
  - an invalid list inside a radiogroup;
  - an unlabeled file input.
- Focus rings are visible on every control. Skip-to-content is provided.
