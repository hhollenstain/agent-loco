"""Directory database. One module per table group.

`schema` creates the tables. `crypto` seals task goals and the GitHub App
private key. `install` is the local account. `teams`, `duties`, and `projects`
are access. `tasks` stores a request, and `schedules` stores when one should
run. `rules` indexes a rule whose text lives in that scope's KiwiFS space.
`github` stores the app id and installation. `assignments` records which
skills and enforced roles an organization applies down to teams and projects.
`skill_repos` indexes a cloned skill repository. Its files and commit live in
a KiwiFS space named from the organization, team, and project ids.
Model context stays in the run directory.
"""
