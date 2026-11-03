# AGENTS.md

Practices for changing this repository. The cycle checklist for working on LBW
itself is also in `skills/agent-lbw/SKILL.md`.

## Layout

- Package code lives in `src/agent_lbw/`.
- Tests live in `tests/test_<area>.py`. Put a test next to the behavior it
  covers. Do not append a new feature's tests onto an unrelated file.
- Web UI is `src/agent_lbw/templates/index.html`, `static/`, and `web_ui.py`.
- The cycle is `runtime/improve.py`, `runtime/roles.py`, `runtime/review.py`, and `agent/loop.py`.
- Bundled skills ship in `src/agent_lbw/skills/<name>/SKILL.md`.
- Repo skills live at `skills/<name>/SKILL.md`.
- Prove a new behavior at a public seam: the `lbw` CLI, an `/api/...` route,
  or the rendered page. Do not add a helper, route, or control that nothing calls.

## Python

- Python 3.12+. Start modules that use annotations with
  `from __future__ import annotations`.
- Line length is 100. Before finishing, run `uv run ruff format src tests`
  then `uv run ruff check src tests`. Ruff selects `E`, `F`, `I`, `UP`, and `B`.
- Use `pathlib.Path` for filesystem paths.
- Name caught exceptions `exc`. Tool and HTTP handlers keep failures in-band:
  tools return `ToolResult(False, message)`, and HTTP errors are
  `{"error": "..."}` with a 4xx status. Do not let a tool exception escape the
  tool runner.
- Prefer a public function over importing another module's `_private` helper.
- Keep imports at the top of the file, in the order ruff applies. A function
  may import a heavy or circular dependency locally when the top-level import
  would cycle.

## Web UI

- JavaScript in `index.html` uses double quotes and `function` declarations for
  named behavior. Iterate with `for...of`.
- Rules, Skills, and Schedules are rail sections beside Proposals, Changes,
 and Releases. Schedules save documents. A project run is not a schedule.
  The main pane shows the selected section. Roles stays a sidebar overlay.
  Close the roles overlay from its Close button or a click outside it.
- UI layout is the bundled `ui` skill. New workspaces enable it with `tdd`.
  After a UI edit, exercise the control in a browser: open it, use it, and
  close it. A template diff is not done. `review_ui` must show the control
  without overlap, unreadable controls, dead buttons, or 404s.
## CSS

- Extract inline styles from templates into `static/layout.css`.
- Do not add inline `style=` attributes to HTML elements in templates.
- Define CSS classes in `layout.css` and apply them to HTML elements.
- After CSS changes, run `review_ui` to verify the page renders correctly.

## Skills and the cycle

- Agent process belongs in a skill, not in `SYSTEM_PROMPT` or the agent loop.
- If you add a bundled skill, keep `package-data` including `skills/*/SKILL.md`.
- A cycle is finished only when the stated goal is wired through. A later cycle
  is not a substitute for missing data or UI.
- After behavior changes, run `uv run pytest -q`. After Python edits, format
  with ruff, then lint.

## Directory database

Shared rows live in `runtime/store/`. Each file owns one concern: `schema` creates
the tables, `crypto` seals secrets, `install` is the local account, `teams`,
`duties`, and `projects` are access, `tasks` stores a request, `schedules`
indexes when one should run, and `recording` is what the task queue and the
file scheduler call.

- Every shared row carries a tenant id. A tenant is `personal` or `organization`.
  A personal tenant has its owner and no teams. Promote it before adding a team.
  Promotion keeps the tenant id, so projects and tasks stay put.
- A person is an identity, separate from a membership. Rank is `owner`, `admin`,
  or `member`, once on the tenant and once on a team. Duties are additive
  grants, `billing`, `security`, or `rules`, on a tenant or a team. An owner
  already holds every duty, so do not write a duty row for an owner. A new duty
  is a new value, not a new kind of person.
- An organization owner or admin can use every project in the tenant. Everyone
  else needs a `use` or `manage` grant on their team or on themselves. `manage`
  includes `use`.
- Seal task goals and the GitHub App private key with `runtime/store/crypto.py`
  and the tenant key. The master key stays in `.lbw/master.key`. It does not go
  in the database, a prompt, or a commit. Do not add a second cipher. The
  `github_apps` row stores the app id, the sealed key, and the chosen
  installation. Do not store an installation token. A connected app can add a
  repository it can see to the organization, a person, or a team. The checkout
  is the workspace. `identities.github_login` links a person to a GitHub
  account. Sync copies that account's organization role, team membership, and
  repository access onto the existing grants. It does not create people.
- Do not store model context, prompts, tool traces, diffs, or file contents in
  the database. Those stay in the run directory. A task row is the request:
  tenant, project, requester, status, branch, pull request, `run_ref`, and
  `goal_ciphertext`.
- A schedule row is an index. The instruction stays in the schedule file.
  `change` indexes a file schedule. `scan` indexes a scan. Do not copy the
  instruction into the database.
- A rule's text lives in its own KiwiFS space: one for the tenant, one for a
  team, one for a project, and one for a person. The `rules` table indexes it.
  `rule_requirements` says which organization rules a project must follow. Do
  not store the rule body in the database. Derive the space from the scope id.
  Do not take a space name from the client. The `rules` duty lets a member edit
  rules for that tenant or team. Project rules follow `manage`.
- A cloned skill repository is indexed in `skill_repos`. The files and the git
  commit stay in KiwiFS. The space is `sk-org-<id>`, `sk-org-<id>-team-<id>`,
  or `sk-org-<id>-team-<id>-proj-<id>`. Derive it from those ids. Do not put an
  organization, team, or project name in the space. A project reads the
  organization space, a team space only when that team can use the project,
  and its own project space.
- The hierarchy is organization, then teams, then projects. An organization
  rule or skill reaches a project only when it is assigned there: an
  organization rule through `rule_requirements`, a team rule through a project
  grant, a skill through `skill_grants` on the organization, a team, or that
  project. Organization rules and skills are edited in organization settings.
  A project page shows the ones assigned to it. Roles are chosen on the
  project. `role_requirements` is the set an organization owner or admin
  enforces on every project.
- Extend these checks when adding access. Do not add a second permission table.

## Git

- Do not commit, stage, or push `.lbw/` or `history.json`. Config, cloned
  skills, run logs, and screenshots stay local.
- Do not force-push, skip hooks, or rewrite published history.
- Do not bind-mount this app's `.lbw` over `/workspaces/.lbw`.
- `lbw init` needs a directory that already exists. Do not document
  `docker clone`.
- Do not ship stubs, TODOs, unused form fields, or "this enables X later".
