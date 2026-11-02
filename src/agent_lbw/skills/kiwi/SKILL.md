---
name: kiwi
description: >-
  Keep project memory in KiwiFS and pull a short brief into the prompt.
  Use on every task when a KiwiFS server is configured. Local models do not
  have room for the repo and a knowledge base at once.
---

# Kiwi

KiwiFS is the memory outside this prompt. Search it. Do not load it.

## Read a little

1. `kiwi_brief` the question. Name the file, error, or library. Leave the
   token budget at the default.
2. `kiwi_peek` the one path that matches. `kiwi_read` a single heading only
   when the peek is not enough.
3. Stop. Do not read the tree, the index, or several full pages. Do not copy
   a KiwiFS page into the repo.

## Write the conclusion back

When this run learns a fact the next run would otherwise rediscover, save it:

- `kiwi_append` `episodes/<date>.md` with a few lines (what failed, what the
  test command is, which file owns the behavior).
- `kiwi_write` `decisions.md` or `conventions.md` when the fact should outlive
  the episode.

A note is a few sentences. No diffs, logs, or source dumps. Writes stay under
`projects/<repo-name>/`. A workspace that is not a git repo uses its folder
name there instead. Do not rewrite the shared guides.

## Why the budget stays small

A 7B–14B local model is already spending its context on the system prompt,
the tools, and the files it edits. A bigger brief crowds out the edit.
Keyword search is enough. Do not ask for a second embedding model while the
coder model is loaded; that embedder competes for the same memory.
