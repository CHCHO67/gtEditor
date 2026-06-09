# Design

## Source of truth
- Status: Active
- Last refreshed: 2026-06-09
- Primary product surfaces: PySide6 `gtEditor` desktop GUI for reviewing cropped table image/json GT pairs.
- Evidence reviewed: `src/gt_editor/app.py`, `src/gt_editor/cli.py`, `README.md`, `tests/test_input_output_workflow.py`, user feedback that four visible save actions were confusing.

## Brand
- Personality: focused annotation workstation, calm, explicit, low-distraction.
- Trust signals: visible current input dataset/file, clear review state, explicit output write action, validation failure messages.
- Avoid: multiple ambiguous save buttons, hidden finalization behavior, crowded toolbars.

## Product goals
- Goals: let reviewers process one table at a time, classify it as completed or discarded, and persist every decision to Output_data saved/discarded buckets.
- Non-goals: full visual design system, batch approval UI, cloud sync.
- Success signals: reviewer always knows current file state; only one primary save action exists; review/completed/discard counts are visible; a new image defaults to direct grid-line dragging; vertical grid lines are red; selected cells are visibly blue; merged cells are visibly purple; source text boxes are clear green translucent overlays without duplicate text rendering.

## Personas and jobs
- Primary personas: dataset/GT reviewers correcting table structure for TTE datasets.
- User jobs: inspect one table crop, edit lines/cells by shortcuts, save accepted output, discard rejected output, continue through review queue.
- Key contexts of use: local desktop, keyboard-heavy annotation, repeated review over many table crops.

## Information architecture
- Primary navigation: top-level tabs for each Input_data folder.
- Core routes/screens: active dataset tab, sub-tabs `검토`, `검토 완료`, `버리기`, table canvas, status/warnings pane.
- Content hierarchy: top header shows active Input_data and file; right side header actions show `Discard` then `Save`.

## Design principles
- Principle 1: One current-file decision at a time.
- Principle 2: Destructive/reject decision is visually distinct but adjacent to the positive save decision.
- Tradeoffs: no visible batch save controls in the GUI; CLI keeps `--save-all` for automation/testing.

## Visual language
- Color: green/teal primary Save, orange Discard, neutral slate header/background.
- Typography: native Qt fonts with bold header labels.
- Spacing/layout rhythm: compact control header, generous canvas area, narrow review sidebar.
- Shape/radius/elevation: rounded high-contrast action buttons, subtle bordered header panels.
- Motion: none.
- Imagery/iconography: text labels over icons to avoid ambiguity.

## Components
- Existing components to reuse: `QTabWidget`, `QListWidget`, `QGraphicsView`, `QPlainTextEdit`, `QPushButton`, existing command stack.
- New/changed components: current-file header, styled Save/Discard buttons, edit button bar, direct drag cell-selection view, Select Cells mode, red vertical grid lines, purple merged-cell state, green text-box overlays, per-dataset review state tabs.
- Variants and states: review/completed/discard list states; blue selected cells; purple merged cells; green source text boxes; disabled action state when no active file.
- Token/component ownership: inline Qt stylesheet in `src/gt_editor/app.py` until a broader design system is needed.

## Accessibility
- Target standard: keyboard-first desktop usability.
- Keyboard/focus behavior: editing tools are available as both visible buttons and application shortcuts; Save is `Ctrl+S`; Discard is `Ctrl+D`.
- Contrast/readability: high contrast text/buttons; orange discard distinct from green save.
- Screen-reader semantics: explicit button text and visible labels.
- Reduced motion and sensory considerations: no animation required.

## Responsive behavior
- Supported breakpoints/devices: desktop windows around 1400x900; native resizing.
- Layout adaptations: sidebar remains left, canvas expands.
- Touch/hover differences: mouse/keyboard primary, no touch-specific behavior.

## Interaction states
- Loading: first review sample loads automatically when present.
- Empty: no document status text appears if no active sample.
- Error: validation/save failures use warning dialog and status pane text.
- Success: status bar and warnings pane show saved/discarded output path/counts, with completed files under `saved/image,json` and discarded files under `discarded/image,json`.
- Disabled: action buttons are disabled when no active document is loaded.
- Offline/slow network, if applicable: not applicable; all local files.

## Content voice
- Tone: concise, operational, reviewer-focused.
- Terminology: use `Input_data`, `Output_data`, `검토`, `검토 완료`, `버리기`, `Save`, `Discard` consistently.
- Microcopy rules: label actions by the current-file decision, not by implementation details.

## Implementation constraints
- Framework/styling system: PySide6 widgets with inline Qt stylesheets.
- Design-token constraints: no new dependency or external asset.
- Performance constraints: load documents on demand and cache edited docs per stem.
- Compatibility constraints: keep CLI `--save-all` and existing IO helper contracts for tests/automation.
- Test/screenshot expectations: pytest/offscreen GUI smoke should cover tab counts, Save/Discard state moves, and output image/json counts.

## Open questions
- [ ] Whether discarded samples need an additional machine-readable manifest beyond the GUI discard tab / owner: product / impact: downstream filtering.
