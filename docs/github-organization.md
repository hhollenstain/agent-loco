# GitHub for your organization

Your GitHub account installs the LBW app. You do not create a GitHub App,
and you do not paste a private key or a client secret. An LBW engineer
saves that key on the engineer dashboard, which is a different host.
Locally that is `https://crossing-local.lookbothways.dev`. The organization site is `https://www-local.lookbothways.dev`. Their steps are
in [github-app.md](github-app.md).

The install covers one GitHub account: your GitHub organization, or your
personal account when the repositories live there. LBW records that
install on this LBW organization only. Repositories, teams, and access
from it stay here. Another LBW organization does not receive them.

You need an owner of the GitHub account, and an owner or admin of this
LBW organization.

## Install the app

1. In LBW, open Organization, then GitHub, and choose Install on GitHub.
2. On GitHub, choose the account that owns the repositories.
3. Grant all of its repositories, or only the ones LBW should work in.
   LBW can see only the repositories included in this install. You can
   change that later under GitHub Settings, Applications, then the LBW
   app, Configure.
4. Back in LBW, choose Refresh, then select that GitHub account and use
   it. Select the account you just installed. Leave every other account
   in the list alone. That selection belongs to this LBW organization.

## Connect people

An owner or admin adds each person to this LBW organization first.
Connecting GitHub does not create a person.

Each person opens Personal, then GitHub, and connects. LBW reads their
login from GitHub and stores it. The user token is used for that read
and is not kept. The organization page then shows the login and the
teams they are on.

Sign-in is separate. The email and password for this install still work.
When this organization uses Entra, Auth0, or SAML, the sign-in page also
offers that provider. Sign in with GitHub matches a stored login, or a verified email
already on a person here. That match stores the login. An unknown
GitHub account does not create a person.

## Add a repository

On Organization, then GitHub, load repositories and add one to this
organization, to one person, or to one team. LBW clones that repository
and records the checkout here. It does not clone every repository on the
install.

Adding it to the organization lets owners and admins use it. Adding it
to a person starts that person at manage. Adding it to a team starts
that team at use. The next sync replaces those with what GitHub says.

## Sync access

Sync copies this GitHub account onto people who have already connected:

- A GitHub organization owner or member becomes the same rank here. The
  LBW owner stays owner.
- A GitHub team is created or updated here. A maintainer is a team admin.
  A member is a team member. A rename on GitHub keeps the same team.
- For a repository whose checkout is one this install can see, admin or
  maintain becomes manage, and push, triage, or pull becomes use. That
  grant is on the team as well as on each connected person.

Sync runs when the installation is chosen, when a person finishes
connecting, when a repository is added, and when someone presses Sync.
A team that GitHub removes from a repository loses that grant here. The
team itself stays until an organization admin removes it, and only when
it is empty. A folder or a plain git project is left unchanged.

Public repositories can still be added by URL with no GitHub App. Push,
pull requests, and merge use the installation above.
