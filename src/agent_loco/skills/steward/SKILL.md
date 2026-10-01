---
name: steward
description: >-
  Continuously improve a repo: pick one best-practice or UX gap, implement
  it fully, then let the cycle open and merge the PR. Use when keep
  improving is on or the goal is the next small quality change.
---

# Steward

Keep the product closer to current best practices and a clearer user
experience. One change per cycle. Do not boil the ocean.

## Pick one gap

Review the tree, README, UI, tests, and docs. Choose **one** concrete
improvement that is missing today:

- A best practice this stack already uses elsewhere but this file skips
- A UX gap: unclear copy, a dead control, missing empty state, crushed
  layout, a 404, or a flow that needs an extra click
- Docs that teach a command that does not exist

Do not invent a new architecture. Do not drive-by refactor. If nothing is
wrong, say so and stop.

## Finish that change

- Restate the one-line goal, then `str_replace` the files that implement it.
- Look up current docs when the practice is not already in this repo.
- Run tests after behavior changes. After UI edits, call `review_ui` and
  click the new or changed control.
- Do not ship stubs, TODOs, unused fields, or "this enables X later".

The cycle commits, opens a PR, and (when keep improving is on) merges after
review. Leave main alone; do not `git push` or `gh pr merge` yourself.
