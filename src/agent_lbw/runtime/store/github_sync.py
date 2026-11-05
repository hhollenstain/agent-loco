"""Place an installation repository, and copy GitHub access onto linked people.

A person is linked after they authorize the GitHub App. Sync does not create
people, and it does not store an installation token or a user access token.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from agent_lbw.runtime.db import Database, database, database_configured
from agent_lbw.runtime.store.github import (
    GitHubAppError,
    github_api,
    github_api_page,
    github_installation,
    github_manager,
    installation_token,
    require_github_manager,
)
from agent_lbw.runtime.store.install import read_account, require_account
from agent_lbw.runtime.store.projects import ensure_project, grant_project
from agent_lbw.runtime.store.teams import (
    add_team_member,
    ensure_github_team,
    promote_to_organization,
)
from agent_lbw.sandbox import Workspace
from agent_lbw.tools.git import github_owner_repo, parse_github_remote

log = logging.getLogger("lbw")

_LOGIN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
_SEGMENT = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}$")
_PAGE_CAP = 20


def link_github_login(db: Database, *, user_id: str, github_login: str) -> dict:
    """Store the login GitHub returned for this person."""
    account = require_account(db)
    target = user_id.strip()
    if not any(user["id"] == target for user in account["users"]):
        raise KeyError(target)
    login = github_login.strip()
    if login and _LOGIN.fullmatch(login) is None:
        raise ValueError("GitHub login is the account name, such as octocat")
    if login:
        taken = db.fetchall(
            "SELECT id, github_login FROM identities WHERE github_login IS NOT NULL",
        )
        for row in taken:
            if row["id"] != target and str(row["github_login"]).casefold() == login.casefold():
                raise ValueError("that GitHub login is already connected")
    db.execute(
        "UPDATE identities SET github_login = ? WHERE id = ?",
        (login or None, target),
    )
    fresh = read_account(db)
    if fresh is None:
        raise ValueError("this install has no account")
    return fresh


def clear_github_login(db: Database) -> dict:
    """The current person disconnects the GitHub account they authorized."""
    account = require_account(db)
    return link_github_login(db, user_id=account["current_user_id"], github_login="")


def list_repos(db: Database, root: Path) -> list[dict]:
    """Repositories this installation can see. Nothing returns the token."""
    token, _login, _kind = _connected(db, root)
    payload = _collect(token, "/installation/repositories", key="repositories")
    repos: list[dict] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        try:
            full_name = _full_name(str(item.get("full_name") or ""))
        except ValueError:
            continue
        repos.append(
            {
                "full_name": full_name,
                "private": bool(item.get("private")),
            }
        )
    return repos


def restore_checkouts(store_root: Path, database_url: str = "") -> None:
    """Clone recorded GitHub projects back onto the workspace volume.

    The project row survives a rebuild. A checkout under the container home
    does not. An existing checkout is shown again. A missing one is cloned
    into the durable folder and the row is pointed at that folder.
    """
    from agent_lbw.runtime.db import database, sqlite_path
    from agent_lbw.runtime.workspaces import (
        clone_workspace,
        default_checkout_parent,
        remember_workspace,
    )
    from agent_lbw.tools.git import github_auth_args, github_https_url

    url = (database_url or "").strip()
    if not (url.startswith("postgres://") or url.startswith("postgresql://")):
        path = sqlite_path(store_root, database_url)
        if path is None or not path.is_file():
            return
    try:
        with database(store_root, database_url) as db:
            account = read_account(db)
            if account is None:
                return
            tenant_id = account["org"]["id"]
            rows = db.fetchall(
                "SELECT id, name, kind, locator FROM projects WHERE tenant_id = ?",
                (tenant_id,),
            )
            missing: list[dict] = []
            for row in rows:
                folder = Path(str(row["locator"])).expanduser()
                if folder.is_dir():
                    remember_workspace(store_root, folder)
                    continue
                if row["kind"] == "github":
                    missing.append(dict(row))
            if not missing:
                return
            try:
                visible = list_repos(db, store_root)
            except (ValueError, GitHubAppError, PermissionError, OSError):
                return
            parent = default_checkout_parent(store_root)
            token = installation_token(db, store_root)
            for row in missing:
                full_name = _matching_repo(visible, str(row["name"]))
                if full_name is None:
                    continue
                owner, repo = full_name.split("/", 1)
                dest = parent / repo
                if dest.is_dir():
                    origin = _folder_repo(dest)
                    if origin != full_name.casefold():
                        continue
                    place = str(dest.resolve())
                else:
                    try:
                        cloned = clone_workspace(
                            github_https_url(owner, repo),
                            parent,
                            name=repo,
                            git_config=github_auth_args(token) if token else None,
                        )
                    except (ValueError, OSError) as exc:
                        log.warning("could not restore %s: %s", full_name, exc)
                        continue
                    place = str(Path(cloned["path"]).resolve())
                try:
                    db.execute(
                        "UPDATE projects SET locator = ? WHERE id = ? AND tenant_id = ?",
                        (place, row["id"], tenant_id),
                    )
                except Exception as exc:
                    log.warning("could not record %s at %s: %s", full_name, place, exc)
                    continue
                remember_workspace(store_root, place)
                log.info("restored %s at %s", full_name, place)
    except (OSError, ValueError) as exc:
        log.warning("could not restore recorded checkouts: %s", exc)


class CheckoutMissing(ValueError):
    """The project is recorded, and its checkout directory is not on this machine."""

    def __init__(self, path: str) -> None:
        super().__init__("could not restore the checkout for this project")
        self.path = path


def recorded_workspaces(store_root: Path, database_url: str = "") -> list[dict]:
    """Every project, including one whose checkout directory is gone."""
    if not database_configured(store_root, database_url):
        return []
    try:
        with database(store_root, database_url) as db:
            account = read_account(db)
            if account is None:
                return []
            rows = db.fetchall(
                """
                SELECT id, name, kind, locator FROM projects
                WHERE tenant_id = ?
                ORDER BY name, id
                """,
                (account["org"]["id"],),
            )
    except (OSError, ValueError):
        return []
    from agent_lbw.runtime.workspaces import describe_workspace

    entries: list[dict] = []
    for row in rows:
        locator = Path(str(row["locator"])).expanduser()
        if locator.is_dir():
            try:
                entries.append(describe_workspace(locator))
            except ValueError:
                continue
            continue
        kind = str(row["kind"] or "")
        entries.append(
            {
                "path": str(locator),
                "name": str(row["name"] or locator.name or locator),
                "is_git": kind in {"git", "github"},
                "git_remote": "",
                "is_lbw": False,
                "guidelines": "",
                "custom_guidelines": False,
                "missing": True,
            }
        )
    return entries


def materialize_checkout(store_root: Path, database_url: str, path: str) -> str:
    """Return a project's checkout, cloning a missing GitHub checkout first."""
    requested = Path(path).expanduser()
    if requested.is_dir():
        return str(requested.resolve())
    if not database_configured(store_root, database_url):
        raise ValueError(f"workspace is not a directory: {requested}")
    project_id, locator = _project_for_path(store_root, database_url, path)
    if project_id is None:
        raise ValueError(f"workspace is not a directory: {requested}")
    restore_checkouts(store_root, database_url)
    try:
        with database(store_root, database_url) as db:
            row = db.fetchone("SELECT locator FROM projects WHERE id = ?", (project_id,))
    except (OSError, ValueError) as exc:
        raise CheckoutMissing(locator) from exc
    if row is None:
        raise CheckoutMissing(locator)
    place = Path(str(row["locator"])).expanduser()
    if place.is_dir():
        return str(place.resolve())
    raise CheckoutMissing(str(row["locator"]))


def _project_for_path(store_root: Path, database_url: str, path: str) -> tuple[str | None, str]:
    keys = {str(Path(path).expanduser())}
    try:
        keys.add(str(Path(path).expanduser().resolve()))
    except OSError:
        pass
    try:
        with database(store_root, database_url) as db:
            account = read_account(db)
            if account is None:
                return None, str(Path(path).expanduser())
            rows = db.fetchall(
                "SELECT id, locator FROM projects WHERE tenant_id = ?",
                (account["org"]["id"],),
            )
    except (OSError, ValueError):
        return None, str(Path(path).expanduser())
    for row in rows:
        locator = str(row["locator"])
        if locator in keys or str(Path(locator).expanduser()) in keys:
            return str(row["id"]), locator
    return None, str(Path(path).expanduser())


def _matching_repo(repos: list[dict], name: str) -> str | None:
    want = name.casefold()
    found = [
        str(item.get("full_name") or "")
        for item in repos
        if str(item.get("full_name") or "").split("/", 1)[-1].casefold() == want
    ]
    if len(found) != 1:
        return None
    return found[0]


def _folder_repo(folder: Path) -> str:
    try:
        origin = github_owner_repo(Workspace(folder))
    except (OSError, ValueError):
        return ""
    if origin is None:
        return ""
    return f"{origin[0]}/{origin[1]}".casefold()


def claim_repo(db: Database, root: Path, full_name: str) -> str:
    """Reject a repository this installation cannot see."""
    require_github_manager(db)
    chosen = _full_name(full_name)
    visible = {item["full_name"].casefold() for item in list_repos(db, root)}
    if chosen.casefold() not in visible:
        raise ValueError("that repository is not on this installation")
    return chosen


def place_repo(
    db: Database,
    *,
    full_name: str,
    path: str,
    subject_type: str,
    subject_id: str,
) -> dict:
    """Record the checkout and grant it to the organization, a person, or a team."""
    account = require_github_manager(db)
    chosen = _full_name(full_name)
    tenant_id = account["org"]["id"]
    kind, subject, placed = _place_target(db, account, subject_type, subject_id)
    folder = Path(path).expanduser().resolve()
    if not folder.is_dir():
        raise ValueError("the checkout is not a directory")
    origin = github_owner_repo(Workspace(folder))
    wanted = tuple(part.casefold() for part in chosen.split("/", 1))
    if origin is None or tuple(part.casefold() for part in origin) != wanted:
        raise ValueError("that folder is not this repository")
    project = ensure_project(
        db,
        tenant_id,
        name=chosen.split("/", 1)[1],
        kind="github",
        locator=str(folder),
    )
    if kind == "identity":
        grant_project(
            db, project["id"], subject_type="identity", subject_id=subject, access="manage"
        )
    elif kind == "team":
        grant_project(db, project["id"], subject_type="team", subject_id=subject, access="use")
    return {
        "full_name": chosen,
        "path": str(folder),
        "project_id": project["id"],
        "placed": placed,
    }


def refresh_access(db: Database, root: Path) -> None:
    """Sync after a person connects. A member can connect before an installation exists."""
    if not github_manager(db):
        return
    try:
        sync_access(db, root)
    except ValueError as exc:
        if str(exc) != "connect a GitHub installation first":
            raise


def sync_access(db: Database, root: Path) -> dict:
    """Copy GitHub roles, teams, and repository access onto linked people."""
    token, login, kind = _connected(db, root)
    account = require_account(db)
    linked = _linked(db, account)
    people: list[dict] = []
    teams: list[dict] = []
    truncated = False
    repos = list_repos(db, root)
    known = {item["full_name"].casefold() for item in repos}
    if kind == "Organization":
        roles, role_cut = _org_roles(token, login)
        truncated = truncated or role_cut
        people = _apply_roles(db, account, linked, roles)
        teams, team_cut = _apply_teams(db, account, linked, token, login, known)
        truncated = truncated or team_cut
    projects = _sync_repositories(db, account, linked, token, known)
    summary = _summary(len(people), len(teams), projects, truncated)
    fresh = read_account(db) or account
    return {
        "summary": summary,
        "account_login": login,
        "account_type": kind,
        "people": people,
        "teams": teams,
        "projects": projects,
        "account": fresh,
    }


def _connected(db: Database, root: Path) -> tuple[str, str, str]:
    account = require_github_manager(db)
    token = installation_token(db, root)
    row = github_installation(db, account["org"]["id"])
    if token is None or row is None or not row["account_login"]:
        raise ValueError("connect a GitHub installation first")
    login = str(row["account_login"])
    kind = str(row["account_type"] or "")
    if kind not in {"Organization", "User"}:
        kind = _detect_kind(token, login)
        db.execute(
            "UPDATE github_apps SET account_type = ? WHERE tenant_id = ?",
            (kind, account["org"]["id"]),
        )
    return token, login, kind


def _detect_kind(token: str, login: str) -> str:
    try:
        payload = github_api("GET", f"/orgs/{login}", token=token)
    except GitHubAppError as exc:
        if "404" in str(exc):
            return "User"
        raise
    if isinstance(payload, dict) and payload.get("login"):
        return "Organization"
    return "User"


def _place_target(db: Database, account: dict, subject_type: str, subject_id: str):
    tenant_id = account["org"]["id"]
    kind = subject_type.strip()
    subject = subject_id.strip()
    if kind == "tenant":
        if subject not in {"", tenant_id}:
            raise ValueError("that organization is not this install")
        return "tenant", tenant_id, "the organization"
    if kind == "identity":
        person = next((user for user in account["users"] if user["id"] == subject), None)
        if person is None:
            raise ValueError("that person is not in this organization")
        return "identity", subject, person["name"]
    if kind == "team":
        team = db.fetchone(
            "SELECT id, name FROM teams WHERE id = ? AND tenant_id = ?",
            (subject, tenant_id),
        )
        if team is None:
            raise ValueError("that team is not in this organization")
        return "team", subject, f"team {team['name']}"
    raise ValueError("choose the organization, a person, or a team")


def _linked(db: Database, account: dict) -> dict[str, dict]:
    rows = db.fetchall(
        "SELECT id, github_login FROM identities WHERE github_login IS NOT NULL",
    )
    by_login = {str(row["github_login"]).casefold(): str(row["id"]) for row in rows}
    found: dict[str, dict] = {}
    for user in account["users"]:
        login = str(user.get("github_login") or "").casefold()
        if login and by_login.get(login) == user["id"]:
            found[login] = user
    return found


def _org_roles(token: str, org: str) -> tuple[dict[str, str], bool]:
    roles: dict[str, str] = {}
    truncated = False
    for role, rank in (("admin", "admin"), ("member", "member")):
        people, cut = _collect_flag(token, f"/orgs/{org}/members?role={role}")
        truncated = truncated or cut
        for item in people:
            if not isinstance(item, dict):
                continue
            login = str(item.get("login") or "").casefold()
            if login:
                roles[login] = rank
    return roles, truncated


def _apply_roles(db: Database, account: dict, linked: dict[str, dict], roles: dict[str, str]):
    changed: list[dict] = []
    tenant_id = account["org"]["id"]
    for login, user in linked.items():
        rank = roles.get(login)
        if rank is None or user["role"] == "owner" or user["role"] == rank:
            continue
        db.execute(
            """
            UPDATE tenant_memberships SET rank = ?
            WHERE tenant_id = ? AND identity_id = ?
            """,
            (rank, tenant_id, user["id"]),
        )
        user["role"] = rank
        changed.append({"login": user["github_login"], "name": user["name"], "rank": rank})
    return changed


def _apply_teams(
    db: Database,
    account: dict,
    linked: dict[str, dict],
    token: str,
    org: str,
    known: set[str],
):
    raw, team_cut = _collect_flag(token, f"/orgs/{org}/teams")
    truncated = team_cut
    listed: list[dict] = []
    tenant_id = account["org"]["id"]
    tenant = db.fetchone("SELECT kind FROM tenants WHERE id = ?", (tenant_id,))
    if raw and tenant is not None and tenant["kind"] != "organization":
        promote_to_organization(db, tenant_id)
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = " ".join(str(item.get("name") or "").split())
        slug = str(item.get("slug") or "")
        if not name or len(name) > 80 or not slug or _SEGMENT.fullmatch(slug) is None:
            continue
        team = ensure_github_team(db, tenant_id, name, slug)
        seen.add(slug)
        maintainers, maint_cut = _collect_flag(
            token, f"/orgs/{org}/teams/{slug}/members?role=maintainer"
        )
        members, member_cut = _collect_flag(token, f"/orgs/{org}/teams/{slug}/members?role=member")
        truncated = truncated or maint_cut or member_cut
        wanted: dict[str, str] = {}
        for person in members:
            if isinstance(person, dict) and person.get("login"):
                wanted[str(person["login"]).casefold()] = "member"
        for person in maintainers:
            if isinstance(person, dict) and person.get("login"):
                wanted[str(person["login"]).casefold()] = "admin"
        present: list[str] = []
        for login, user in linked.items():
            rank = wanted.get(login)
            if rank is None:
                if not maint_cut and not member_cut:
                    db.execute(
                        "DELETE FROM team_memberships WHERE team_id = ? AND identity_id = ?",
                        (team["id"], user["id"]),
                    )
                continue
            add_team_member(db, team["id"], user["id"], rank)
            present.append(user["github_login"])
        repos, repo_cut = _collect_flag(token, f"/orgs/{org}/teams/{slug}/repos")
        truncated = truncated or repo_cut
        _apply_team_repos(db, account, team["id"], repos, known, delete=not repo_cut)
        listed.append({"name": team["name"], "members": present})
    if not team_cut:
        _drop_missing_teams(db, account, seen, known)
    return listed, truncated


def _sync_repositories(
    db: Database,
    account: dict,
    linked: dict[str, dict],
    token: str,
    known: set[str],
) -> list[dict]:
    rows = db.fetchall(
        "SELECT id, name, locator FROM projects WHERE tenant_id = ?",
        (account["org"]["id"],),
    )
    updated: list[dict] = []
    for project in rows:
        full_name = _project_repo(str(project["locator"]))
        if full_name is None or full_name.casefold() not in known:
            continue
        collaborators, cut = _collect_flag(
            token, f"/repos/{full_name}/collaborators?affiliation=all"
        )
        access = _collaborator_access(collaborators)
        for login, user in linked.items():
            level = access.get(login, "")
            if not level:
                if cut:
                    continue
                db.execute(
                    """
                    DELETE FROM project_grants
                    WHERE project_id = ? AND subject_type = 'identity' AND subject_id = ?
                    """,
                    (project["id"], user["id"]),
                )
                continue
            grant_project(
                db,
                str(project["id"]),
                subject_type="identity",
                subject_id=user["id"],
                access=level,
            )
        updated.append({"name": project["name"], "full_name": full_name})
    return updated


def _collaborator_access(people: list[object]) -> dict[str, str]:
    found: dict[str, str] = {}
    for item in people:
        if not isinstance(item, dict) or not item.get("login"):
            continue
        level = _access_level(item.get("permissions"))
        if level:
            found[str(item["login"]).casefold()] = level
    return found


def _access_level(permissions: object) -> str:
    perms = permissions if isinstance(permissions, dict) else {}
    if perms.get("admin") or perms.get("maintain"):
        return "manage"
    if perms.get("push") or perms.get("triage") or perms.get("pull"):
        return "use"
    return ""


def _apply_team_repos(
    db: Database,
    account: dict,
    team_id: str,
    repos: list[object],
    known: set[str],
    *,
    delete: bool,
) -> None:
    """Copy one team's repositories onto placed projects. A short list may delete."""
    access: dict[str, str] = {}
    for item in repos:
        if not isinstance(item, dict):
            continue
        try:
            full_name = _full_name(str(item.get("full_name") or ""))
        except ValueError:
            continue
        level = _access_level(item.get("permissions"))
        if level:
            access[full_name.casefold()] = level
    for project_id, full_name in _placed_projects(db, account, known):
        level = access.get(full_name.casefold(), "")
        if level:
            grant_project(
                db,
                project_id,
                subject_type="team",
                subject_id=team_id,
                access=level,
            )
        elif delete:
            db.execute(
                """
                DELETE FROM project_grants
                WHERE project_id = ? AND subject_type = 'team' AND subject_id = ?
                """,
                (project_id, team_id),
            )


def _drop_missing_teams(db: Database, account: dict, slugs: set[str], known: set[str]) -> None:
    """A GitHub team that disappeared loses grants on repos this installation can see."""
    rows = db.fetchall(
        """
        SELECT id, github_slug FROM teams
        WHERE tenant_id = ? AND github_slug <> ''
        """,
        (account["org"]["id"],),
    )
    placed = _placed_projects(db, account, known)
    for row in rows:
        if str(row["github_slug"]) in slugs:
            continue
        for project_id, _full_name in placed:
            db.execute(
                """
                DELETE FROM project_grants
                WHERE project_id = ? AND subject_type = 'team' AND subject_id = ?
                """,
                (project_id, row["id"]),
            )


def _placed_projects(db: Database, account: dict, known: set[str]) -> list[tuple[str, str]]:
    """Checkouts whose repository this installation can see."""
    rows = db.fetchall(
        "SELECT id, locator FROM projects WHERE tenant_id = ?",
        (account["org"]["id"],),
    )
    found: list[tuple[str, str]] = []
    for project in rows:
        full_name = _project_repo(str(project["locator"]))
        if full_name is None or full_name.casefold() not in known:
            continue
        found.append((str(project["id"]), full_name))
    return found


def _project_repo(locator: str) -> str | None:
    folder = Path(locator)
    if folder.is_dir():
        try:
            origin = github_owner_repo(Workspace(folder))
        except FileNotFoundError:
            origin = None
        if origin is not None:
            return f"{origin[0]}/{origin[1]}"
    parsed = parse_github_remote(locator)
    if parsed is None:
        return None
    return f"{parsed[0]}/{parsed[1]}"


def _collect(token: str, path: str, *, key: str | None = None) -> list[object]:
    found, _truncated = _collect_flag(token, path, key=key)
    return found


def _collect_flag(token: str, path: str, *, key: str | None = None) -> tuple[list[object], bool]:
    """Follow GitHub's next link. A missing page is truncated and must not delete."""
    found: list[object] = []
    url = path
    for page in range(1, _PAGE_CAP + 1):
        request = url
        if not request.startswith("https://") and "page=" not in request:
            sep = "&" if "?" in request else "?"
            request = f"{request}{sep}per_page=100&page={page}"
        payload, link = github_api_page("GET", request, token=token)
        if key is None:
            batch = payload if isinstance(payload, list) else None
        else:
            batch = payload.get(key) if isinstance(payload, dict) else None
        if not isinstance(batch, list):
            raise GitHubAppError("GitHub returned an unreadable response")
        found.extend(batch)
        nxt = _link_next(link)
        if nxt:
            url = nxt
            continue
        if len(batch) < 100:
            return found, False
        return found, True
    return found, True


def _link_next(header: str) -> str:
    for part in header.split(","):
        if 'rel="next"' not in part:
            continue
        start = part.find("<")
        end = part.find(">")
        if start != -1 and end > start:
            return part[start + 1 : end].strip()
    return ""


def _full_name(value: str) -> str:
    text = value.strip()
    owner, _, repo = text.partition("/")
    if (
        not owner
        or not repo
        or "/" in repo
        or owner in {".", ".."}
        or repo in {".", ".."}
        or _SEGMENT.fullmatch(owner) is None
        or _SEGMENT.fullmatch(repo) is None
    ):
        raise ValueError("choose a repository")
    return f"{owner}/{repo}"


def _summary(people: int, teams: int, projects: list[dict], truncated: bool) -> str:
    text = (
        f"Synced {people} people, {teams} teams, and repository access on "
        f"{len(projects)} workspaces."
    )
    if truncated:
        return text + " GitHub returned a partial list."
    return text
