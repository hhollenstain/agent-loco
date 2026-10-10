---
name: debug
description: >-
  Structured debugging. Use when the goal is a bug, test failure, exception,
  wrong output, or a regression. Reproduce first; do not guess a fix.
---

# Debug

Do not start by editing. A guessed patch that does not reproduce the failure
is not a fix.

## Reproduce

1. Restate the symptom in one sentence (the exact error, wrong value, or test).
2. Find a red signal you can re-run: `run_tests`, the project's test command,
   or a single shell invocation that shows the failure.
3. Run that signal once and keep the output. If you cannot reproduce, stop
   and say what you tried. Do not "fix" a bug you have not seen fail.

## Isolate

- Name two or three falsifiable causes. Example: "If X is the cause, changing
  Y will turn this signal green."
- Use `search_text` and `read_file` on the failing path only. Do not rewrite
  unrelated files.
- Change one cause at a time. If the signal stays red, revert that guess.

## Fix and lock

- Apply the smallest change that turns the same signal green.
- Leave a regression test at the public seam (CLI, HTTP, or rendered UI)
  unless the workspace already has one that now fails for this bug.
- Call `run_tests` after the fix. If there is no test command, look up the
  official runner, add the regression test, and call `configure_project`
  first. If `run_tests` fails, fix
  that failure. Do not replace it with a command that exits 0 without
  running the check. Then `run_lint` if the project has a linter. Remove
  any temporary prints or harness files you added.

Do not add logging, flags, or "debug mode" that the goal did not ask for.
