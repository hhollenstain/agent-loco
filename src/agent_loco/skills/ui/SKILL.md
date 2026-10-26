---
name: ui
description: >-
  Lay out web UI so new controls stay clickable and do not cover existing
  ones. Use when editing HTML, CSS, templates, or client layout, and after
  any change that adds a button, modal, tab, or overlay.
---

# UI layout

A UI change is done when the rendered page shows the new control, a click
reaches it, and it does not cover or get covered by something already there.
A template diff is not done. A screenshot of the closed main screen is not
done.

## Place the control

- Read the CSS for the region before adding anything. Find what is already
  `position: absolute` or `fixed` and which corner it occupies.
- One pinned control per corner. If that corner is taken, put the new action
  in normal document flow and reserve padding so the text underneath still
  clears it.
- Do not add a second button at the same `top`/`right`/`bottom`/`left` as an
  existing one. Nudging it by a few pixels still overlaps.
- On a task or history card, `.pr-link` and `.rerun-btn` already own the
  bottom-right, and `.history-line-stats` owns the bottom-left. A new control
  in either corner overlaps them. Put a new card action in a footer row, or
  reuse the existing rerun button, and keep the card's bottom padding.
- Sidebar panels (`#skills-panel`, `#guidelines-panel`) cover the sidebar
  only. The main task pane stays visible. The list fills the overlay and
  scrolls. Do not push the form down to make room for the list.
- A new dialog uses the existing `.modal-backdrop` and `.modal`, starts
  `hidden`, and closes from its button or a click on the backdrop. A closed
  dialog must not intercept clicks.

## Wire the control

- Give the control an `id` or a `data-*` attribute. The click listener added
  in the same change must mention that exact token.
- A class that is only used for color or position is not a handler. If the
  review says `button.<class> has no click handler`, the listener does not
  reference that class.
- A new route is wired when the added page contains that path. `fetch`,
  `EventSource`, and a template URL all count. Do not add a second route
  that the page never calls.
- Do not leave a button, route, or form field that nothing calls.

## Prove the layout

- After the edit, call `review_ui`. Open the pane that contains the control
  (Current, Past runs, Skills, Rules) and click the new control.
- The report is a failure when it says overlaps, unreadable, 0×0, dead
  control, JavaScript error, or 404. Move the control or reserve space, then
  call `review_ui` again.
- Check the resting state and the opened state. A panel that looks fine
  closed can cover the rerun button, the PR link, or the diff counts once
  it is open.
- Call `run_tests` after the layout is clean. If a test asserts the old
  markup this change replaces, update that test to the new public behavior.

## Show the screen

A visual change is not done until something renders it. If `review_ui` says
there is no preview server, add one before claiming the control works.

- For a web app, call `configure_project` with `preview_command`. The command
  runs with `{port}` replaced and must listen on that port. Example:
  `python3 -m http.server {port}`. Look up the framework's official dev
  server when this repo does not already name one.
- The page has to show the control this goal changes and perform the action
  (open the menu, press Start, change the setting). A paragraph that says the
  screen works is not a preview.
- A project with `project.godot` is previewed by loco. It runs Godot, captures
  the main scene, and serves that frame on `{port}`. Do not set
  `preview_command` to `echo` or `sleep`. If the review says Godot was not
  found, install Godot 4 or set `GODOT_PATH`, then call `review_ui` again.
- If the capture is missing, blank, or does not show the action, the goal is
  not met. Fix the app or the preview and call `review_ui` again.

## UI Component Patterns

### Sidebar Panels
Sidebar panels such as `#skills-panel` and `#guidelines-panel` are overlays that:
- Cover only the sidebar area, keeping the main task pane visible
- The panel list fills the overlay and scrolls
- Clicking outside the panel or using the close button dismisses it
- These panels must not interfere with existing UI elements

### Modal Dialogs
Modal dialogs should follow this structure:
- Use existing `.modal-backdrop` and `.modal` classes for consistent styling
- Start with `hidden` attribute to ensure they're not visible initially
- Include a close mechanism (button or backdrop click)
- Do not intercept clicks when closed
- Maintain consistent z-index stacking with other UI elements

### Task Cards
Task cards follow specific positioning rules:
- Bottom-right corner owned by `.pr-link` and `.rerun-btn`
- Bottom-left corner owned by `.history-line-stats` 
- New controls in these corners will cause overlap issues
- Use footer rows for additional actions on task cards
- Maintain proper padding to prevent clipping

### Form Elements and Buttons
When adding form elements:
- All buttons must have `id` or `data-*` attributes for click handlers
- Click handlers must reference the exact token used for the element
- No unused form fields, routes, or buttons should be left in the code
- Maintain consistent styling with existing UI components

### Responsive Layout Guidelines
The UI must maintain proper behavior across different screen sizes:
- Use CSS Grid and Flexbox for responsive layouts
- Avoid fixed positioning that may conflict with other elements
- Test both desktop and mobile views during review process