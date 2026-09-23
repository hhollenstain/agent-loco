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
