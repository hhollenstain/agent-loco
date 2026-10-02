---
name: sdd
description: >-
  Spec-driven development. Use when there is a spec, requirements document, or
  detailed goal description. Prefer the spec throughout the cycle and verify
  work against it.
---

# Spec-Driven Development

Spec-driven development (SDD) means using specs as the primary source of truth
for what to build. This skill is especially useful when there is a requirements
document, design spec, or detailed specification that you should follow.

## Read the spec first

- Locate the spec in the issue body, comments, linked documents, or existing
  files in the workspace.
- Read the entire spec before making any changes.
- Note explicit requirements, constraints, acceptance criteria, and edge cases.
- If multiple specs exist, prefer the most recent or the one at the root of
  the workspace.

## Use the spec throughout the cycle

- Every change should map to an item in the spec. Do not implement behaviors
  that are not in the spec unless explicitly required.
- After each edit, verify against the spec. Ask: "Does this satisfy the
  current requirement?"
- When the agent runs are done, use the spec to form acceptance criteria.
  Each requirement should have a corresponding verification.

## Handle missing specs

- If the spec is unclear or missing, ask for clarification or propose one.
- Do not guess requirements. Document what you found and ask for confirmation.
- When a spec is ambiguous, prefer conservative behavior that can be adjusted.

## In this workspace

- Read issue comments and linked documents as part of the spec.
- Verify changes against both the stated goal and any additional spec that
  appears during the cycle.
- Use the `review` skill to check your work against the spec before finishing.
