---
name: security
description: >-
  Keep secrets and untrusted input out of dangerous sinks. Use on every
  change that handles paths, shell, HTML, URLs, auth, or user data.
---

# Security

Review the diff for the failures an unattended agent actually introduces.
Fix them before you stop.

## Secrets

- Never commit `.env`, keys, tokens, PEM files, or `credentials.json`.
- Do not hard-code secrets. Read them from the environment the project
  already uses.
- Do not print secrets into logs, test output, or the summary.

## Untrusted input

- Do not interpolate request, form, file, or issue text into a shell
  command, SQL string, file path, or HTML without the same checks this
  repo already uses.
- Do not fetch a URL the model or a user supplied if it can point at
  localhost or a private network. Prefer the existing public-URL checks.
- New HTTP mutations must authenticate the same way neighboring routes do.
  Do not add an unauthenticated write next to guarded ones.

## Do not weaken the tree

- Do not skip hooks, disable auth, or catch-and-ignore errors to make a
  test pass.
- Path tools stay inside the workspace. Do not add a helper that reads
  or writes outside it.
- If a change looks risky and you cannot make it safe, stop and say why.
