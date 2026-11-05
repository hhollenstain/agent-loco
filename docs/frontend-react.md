# Frontend on React

The organization site should become a React client of the API it already
has. The page is one Jinja template, `src/agent_lbw/templates/index.html`,
about 10,700 lines, with one script of roughly a hundred functions and a
shared `state` object that builds the DOM by hand. Rail sections, dialogs,
and tables already have boundaries. Those boundaries are components. A
single replacement of the file would stall the product and leave `/`
broken until every section is back. Move one section at a time.

The engineer dashboard, `services/crossing`, is a second, smaller template
that shares `themes.css` and `layout.css`. It follows after the organization
shell can render one section.

## What stays

FastAPI stays the server. Behavior stays on `/api/...`. A client error is
still `{"error": "..."}` with a 4xx status. The session cookie stays the
sign-in. React does not gain a second backend, a second auth scheme, or a
copy of a route that already exists.

`/` stays a Jinja shell. `UiState.template_vars()` already gives the first
paint its workspace list, git remote, repo label, guidelines, and model
defaults. The shell keeps that JSON and a mount node. The client reads it.
It does not wait on a second round trip before the project bar can render.

Color and type stay in `static/themes.css`. Component layout stays in
`static/layout.css`. The bundled `style` skill remains the visual source.
A styling change updates that skill in the same change, then the CSS.
Components use those class names. A new look does not arrive as a second
palette, a CSS-in-JS runtime, or a component library theme.

Stable `id` and `data-*` tokens stay on the controls tests and `review_ui`
already look for. A section that moves keeps the token the click path uses.

The running image stays Python. Node builds the client. It is not installed
in the agent or crossing container, and `lbw ui` does not start a Node
process.

## Where the client lives

Source lives in `web/`:

```text
web/
  package.json
  tsconfig.json
  vite.config.ts
  src/
    main.tsx          mounts one island
    api.ts            fetch wrapper for /api
    sections/         one folder per rail section, added as each moves
```

Vite builds a single bundle to `src/agent_lbw/static/app/`. The shell loads
`/static/app/`. `pyproject.toml` package-data already ships `static/`, so
the wheel, the tests, and the image pick the bundle up the same way they
pick up `layout.css`.

`make web` installs and builds. The same command runs in CI and fails when
`web/src` changed and `static/app/` was not rebuilt. The Dockerfile copies
the built files with the rest of `src`. It does not run Node.

TypeScript is strict. Components are functions. Props name the API fields
they render. A section folder owns its markup. Shared controls (button,
dialog, switch, table) wait until a second section needs the same one.
The first section may keep its markup local.

The fetch wrapper sends credentials, reads JSON, and throws the `error`
string from a failed response. Callers render that string. They do not
invent a second error shape.

## Order

Each step is finished when the rendered page shows the control, a click
reaches it, and the API the control calls still passes pytest. A diff of
`web/src` is not done. `review_ui` still has to open the control.

1. **Toolchain and an empty mount.** Add `web/`, `make web`, and a hidden
   mount in the shell that renders nothing over the current page. `/`
   looks the same. `review_ui` still passes. This step proves the bundle
   is served from `/static/app/` inside the Python app.

2. **One leaf section.** Move the organization Models tables: endpoints,
   models, and agents. They are tables, switches, a confirm dialog, and
   plus dialogs, and they already have routes (`/api/org/settings`, the
   model and agent enable and delete routes). Leave the rail, the proposal
   form, and Changes in the template. When the Models tab is selected, the
   template shows the mount and hides the old tables.

3. **Shared controls.** Extract dialog, switch, table, and icon button
   once a second section needs them. Match the existing classes
   (`.modal-backdrop`, `.modal`, `.org-table`, `.org-switch`).

4. **Project rail, one section at a time.** Skills, then Roles, then
   Rules, then Releases, then Changes, then the proposal form. Changes
   and the proposal form own live task state, so they go last. Schedules
   can move with the rail. Each move deletes that section's markup and
   functions from `index.html` in the same change.

5. **Shell.** Sidebar, project picker, theme, and account menu move after
   the sections they switch among are in the client. Until then the
   template owns navigation and calls into the mounted section.

6. **Crossing.** Point the dashboard at the same `web/` package and the
   same CSS. Its pages are organizations, engineers, unlock, and SSO
   settings. It does not gain a goal, a diff, or a pull request.

Do not start with Changes, the proposal form, or the auth gate. Those
screens are the product, and they are the worst place to discover a
bundling mistake.

## While both exist

A section lives in one place. Once it has a React file, new work on that
section goes in `web/src/sections/`. The template copy is deleted in that
same change. A section still in `index.html` keeps the current rules:
double quotes, `function` declarations, `for...of`, and no inline
`style=`.

The template decides which section is visible until the shell moves.
The client does not also listen for those rail clicks.

Client state for a section is the last JSON from its API. The first
section can hold that in component state. Add a query cache when the
second section needs the same response. Do not mirror the server into a
hand-written store that drifts from `/api`.

Polling and `EventSource` for a running task stay on the Changes path
until that section moves. The Models island does not subscribe to task
progress.

A styling request still updates `src/agent_lbw/skills/style/SKILL.md`
first, then `themes.css` and `layout.css`. Components consume the classes.

## Tests

API tests stay on the route. A moved control keeps a page test that the
shell includes the mount and the section's stable token. Browser coverage
is the same as today: open the control, use it, and close it. Assert the
rendered result, including the bundle, at `/`.

`review_ui` keeps rendering this app through `lbw ui`, not a static file
server. The built bundle has to be present for that, because a Jinja shell
with a missing `/static/app/` script is a broken page.

## Where this stands

The toolchain, organization Models, organization Skills, organization Roles,
project Skills, and project Roles are in the client. Organization tabs stay
in the template and call `showOrgTab`. Settings JSON is passed with
`lbw-org-settings`. Project skills and roles are passed with
`lbw-project-skills` and `lbw-project-roles`. The next move is project
Rules, then Releases. Changes and the proposal form stay last. Until a
section moves, edit it in `index.html`.
