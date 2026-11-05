# Auth flows

Plan for sign-in and for a GitHub organization that keeps LBW teams and
repository access in step with GitHub. Sign-in proves a person and opens a
session. GitHub access is a copy onto grants that already exist. Neither path
creates a person, and neither stores a token.

## What already works

Password sign-in is the session. The first password is the owner's, created
on the setup page. Later sign-in uses that email and password. The password
is hashed. Five failures lock the account. The session cookie is random and
only its hash is stored. A session is one person and one organization, and it
lasts fourteen days. The sign-in page can create another organization and its
owner. `/api/auth/login`, `/api/auth/setup`, and `/api/auth/signup` are the
seams. Once a password exists, an `/api/` call without a session is `401`.

SSO settings are stored and not used to sign in. A crossing guard saves
`password`, `entra`, `auth0`, or `saml` on `sign_in`, with the issuer,
metadata URL, client id, and a sealed client secret. Check fetches the
metadata URL and opens the secret. It does not redirect, and it does not
match an email.

GitHub is an app on the organization, not a second login. An owner or admin
saves the app id, the sealed private key, and the sealed client secret, then
chooses one installation. An installation token is minted when a call needs
it and is not stored. A person connects from the GitHub tab on Personal.
That visit reads their login once and stores it on `identities.github_login`.
The user token is dropped.

`POST /api/github/sync` copies access onto people who already have a login:

- On an organization installation, GitHub org admin and member become the
  LBW rank. The LBW owner is left as owner.
- Each GitHub team is ensured by name. A personal tenant is promoted first.
  A maintainer is a team admin. A member is a team member. A linked person
  who left the team is removed when the member list was complete.
- For a project whose checkout is a repository this installation can see,
  collaborator admin or maintain becomes `manage`, and push, triage, or pull
  becomes `use`, on that person. A linked person with no access loses that
  identity grant.

Placing a repository is still a separate step. `POST /api/github/workspaces`
clones a visible repo and grants it to the organization, one person
(`manage`), or one team (`use`).

## The gap

Sync writes who is on a team. It does not write which repositories that team
may use. A person who can open a repo only because of a GitHub team gets an
identity grant from the collaborator list, when that list is complete. The
team itself has no `project_grants` row, so a rule, a skill, or a model
granted through the team does not follow the GitHub team, and the next
person who joins the team has nothing until someone syncs again and the
collaborator list names them.

A GitHub team is also matched by its display name. A rename creates a second
LBW team. The slug GitHub returns is not stored.

Sync runs only when someone presses Sync. Choosing an installation,
connecting a person, or placing a repo does not refresh grants.

`sign_in.provider` does not change `/api/auth/login`. An Entra, Auth0, or
SAML user still needs the password on their identity.

## Invariants

- A person is created by an owner or admin, or by the setup and sign-up
  pages. Sync and SSO do not create one. A GitHub login or an SSO email that
  matches nobody is skipped.
- The LBW owner rank is not overwritten from GitHub.
- Rank does not include a model, an agent, or a duty. Sync writes membership
  and `project_grants` only.
- `manage` includes `use`. An organization owner or admin can use every
  project without a grant. Do not add a second permission table.
- Do not store an installation token, a user access token, a prompt, a diff,
  or a file. The private key and the SSO client secret stay sealed with the
  tenant key.
- A truncated GitHub page must not delete memberships or grants. The summary
  already says when the list was partial. Keep that, and follow it for team
  repositories.
- An empty team can still be removed by an organization admin. Sync does not
  delete a team. Crossing guard does not remove a team.
- Folder and plain git projects are left alone. Sync touches a project only
  when its checkout is a repository on this installation.

## GitHub organization mapping

One installation is one GitHub account. When `account_type` is
`Organization`, that account is the source for teams and repository access
inside the LBW organization that saved the app. A user installation still
syncs collaborator grants on placed repos and does not invent teams.

Store the GitHub team slug on `teams` (`github_slug`, empty for a team
created in LBW). Sync finds the team by slug, then by name. A rename updates
the name and keeps the id, so grants stay put.

For each GitHub team, after membership is applied, read
`GET /orgs/{org}/teams/{slug}/repos`. Map permission the same way
collaborators are mapped:

| GitHub | `project_grants.access` |
| --- | --- |
| admin, maintain | `manage` |
| push, triage, pull | `use` |
| otherwise | no grant |

The subject is the team, not each member. `grant_project` already accepts
`subject_type=team`. Write that row for every placed project whose
`owner/repo` is in the team list. When the team repo list is complete,
delete team grants on those projects for teams that GitHub no longer lists
on the repo. Leave grants on projects this installation cannot see.

Identity collaborator grants stay. They cover outside collaborators and
direct access. Team grants cover everyone on the team, including someone
who connects after the last collaborator pass. Both can exist. Access is
the better of the two, because `can_access` already treats a team grant as
enough.

Placing a repo does not change. The checkout is a directory, and LBW does
not clone every repository on the installation by itself. Once the checkout
exists, the next sync attaches the teams GitHub already gave that repo.
The grant written at place time (`use` for a team, `manage` for a person)
is the starting row. Sync replaces it when GitHub says something else.

Run the same `sync_access` at three moments, in addition to
`POST /api/github/sync`:

- after the installation is chosen
- after a person finishes connecting GitHub
- after a repository is placed

Failure at those moments stays in the JSON error or the redirect LBW
already uses. A partial sync still must not delete rows.

Do not turn the webhook on for this slice. The app is installed with the
webhook off. A later slice can accept `membership`, `team`, and
`team_add` events, call `sync_access`, and drop the body. Until then,
those three moments plus the Sync button are the automatic path.

Pagination stays safe. Prefer the `Link` header over a fixed three pages.
If a page is missing, set the truncated flag and skip deletes for that
list only.

## Sign-in after GitHub

Connecting GitHub is not signing in. The person is already in a session.
A later "Sign in with GitHub" uses the same app, the same callback shape,
and `open_session`. It looks up `github_login`. If that login is new, a
verified email that already belongs to a person stores the login and
opens the session. No match adds nobody. The installation token is
still not the user token. Password sign-in keeps working for the owner
and for a crossing guard.

## SSO and SAML

The crossing-guard form is the configuration. The missing piece is the
login.

`GET /api/auth/sso` redirects to the provider saved for the organization
being entered. `GET /api/auth/sso/callback` checks the assertion, reads the
email, and calls `open_session` for the identity with that email in that
organization. Unknown email, a locked identity, or a bad assertion is the
same class of refusal as a wrong password: no session, no new person.

Entra and Auth0 are OIDC. SAML uses the metadata URL already stored. The
client secret stays sealed and is not returned. Password login remains
available so the owner can still get in when the provider is down. A
crossing guard can still unlock a person and can still switch the provider
back to `password`.

SSO does not copy teams. GitHub remains the source for team and repository
grants. An SSO email and a GitHub login are two fields on the same person.
An admin sets the email. The person connects GitHub themselves. Sync joins
those, and nothing merges two people automatically.

## Order

1. Store `teams.github_slug` and teach sync to update a renamed team.
   Prove it through `POST /api/github/sync`: a slug rename keeps the team
   id and its members.
2. Read team repositories and write team `project_grants`. Prove that a
   team with push on `acme/demo` gets `use` on the placed project, a team
   with admin gets `manage`, and a team removed from the repo loses that
   grant when the list is complete. A truncated list leaves the old grant.
   Extend `tests/test_github_access.py`. Do not add a new permission table.
3. Call `sync_access` after installation select, user connect, and place.
   The Sync button still works. A failed refresh returns the error the
   page already shows and does not wipe grants.
4. SSO callback matches an existing email and opens the session. Prove
   `GET /api/auth/sso/callback` with a known email, a locked email, and an
   unknown email. The secret is still absent from the response.
5. Optional: sign in with GitHub for a login that is already stored.
   Unknown logins do not create people.

## Out of scope

- Cloning every repository on the installation.
- Creating people from GitHub members or from SSO.
- Mapping GitHub roles onto billing, security, rules, models, or agents.
- Storing webhook bodies, installation tokens, or user tokens.
- A second grant table for "GitHub access" beside `project_grants`.
