---
name: research
description: >-
  Look up official documentation and current best practices on the web
  before inventing an API, architecture, or recommendation. Use when the
  repo does not already contain the answer.
---

# Research

Local models guess stale APIs and weak architecture. Search and read
official docs before you invent a library call, framework pattern, or
"best practice" that is not already in this repo.

## When to look it up

Call `web_search`, then `fetch_url` on an official page, when you need:

- A library or framework API you have not seen in this workspace
- Current recommended architecture for a well-known problem
- Version-specific behavior, deprecations, or security guidance
- How an official tool, protocol, or language feature is supposed to work
- How this language or framework should be tested, linted, or previewed
  when `.loco/config.yaml` has no command for it

Do not search for code that is already in the workspace. Read the repo first.

## How to look it up

1. Call `web_search` with the language, library, and version when you know them.
2. Prefer official sources in the results: project docs, MDN, language
   references, RFCs, and the library author's site. Skip random SEO blogs
   when an official page is present.
3. Call `fetch_url` on that official URL and use what it says.
4. If you already know the docs URL, skip search and `fetch_url` it directly.

## After you read

- Implement from the docs you fetched, not from memory that disagrees with them.
- When the docs say how to test, lint, or capture this stack, call
  `configure_project` with those commands. `preview_command` must listen on
  `{port}`. Do not leave loco validating a Godot, native, or other non-web
  app with a command you invented.
- Do not paste long documentation into the repo. Cite the URL in a comment
  only when the call is non-obvious.
- If search and fetch fail, say so and stop guessing at an invented API.
