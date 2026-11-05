---
name: rules
description: >-
  Write and follow organization, team, and project rules stored in KiwiFS.
  Use when the person asks for a rule, a skill, or a standard the team should
  keep, or when the task lists rules in force.
---

# Rules

A rule is a short standard. It lives in a KiwiFS space that belongs to one
organization, one team, one project, or one person. Those spaces do not
contain each other. The hierarchy is organization, then teams, then projects.
Organization and team rules are edited in organization settings. A project
shows an organization rule only when it is required there, and a team rule
only when that team can use the project. Personal rules stay with the person.

## Follow the rules already in force

The task lists the rules that apply to this project: organization rules marked
required, team rules for teams granted on the project, and the project's own
rules. Follow them. Do not search another space for more.

Repository instruction files, such as AGENTS.md, CLAUDE.md, and CURSOR.md,
are included when this project has them turned on. They are read from the
primary branch. A file that is not included was turned off. Do not read it
back in, and do not copy it into a KiwiFS rule.

## Write a rule when asked

1. `list_rules` for the scope the person named, so you do not duplicate one.
2. `write_rule` once with `scope_type`, `scope_id`, a short `name`, and the
   markdown `body`. Pass `rule_id` only to revise a rule that is already there.
3. Stop. Do not copy the rule into the repository, and do not write it with
   `kiwi_write`. That tool is for this project's notes, not for an
   organization or team rule.

The scope id is in the request. Do not invent one, and do not use an id from
another organization, team, or person. A member edits organization rules only
when they hold the rules duty. Team rules follow the team rank or that duty.
Project rules follow manage access.
