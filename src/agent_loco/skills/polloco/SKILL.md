---
name: polloco
description: >-
  Code review of the working diff before stopping. Flags hard-coded secrets,
  shell injection, and new modules with no tests. No extra questions.
---

# Polloco

The crazy chicken on the LoCo line. Review the change before you summarize.
The stated goal is the spec.
Do not ask the user to fill a review prompt. Call `review_changes`; the
cycle already has the goal from the initial prompt and runs the same
checks again after you stop.

## Before you stop

Call `review_changes` with no arguments. It reads the working-tree diff and
returns findings:

**[error]** `file:line` — what is wrong.
Suggestion: how to fix it.

- An error means the change is not done. Fix it and call `review_changes` again.
- A warning that a new `src/` module has no test means add that test in this run.
- Do not filter or ignore a finding. The tool result is the evidence.

An error still in the diff rejects the goal.
