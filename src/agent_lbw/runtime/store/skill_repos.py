"""Cloned skill repositories. The files live in a KiwiFS space.

The space is named from ids: the organization, then a team, then a project.
A request cannot choose the space, and a stored path cannot leave
`skills/<id>/`. One scope cannot read a space that belongs to another.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from agent_lbw.runtime.db import Database
from agent_lbw.runtime.skills import (
    Skill,
    enabled_skill_names,
    fetch_skill_checkout,
    parse_skill_markdown,
    skill_documents,
)
from agent_lbw.runtime.store.assignments import can_manage_org
from agent_lbw.runtime.store.names import new_id
from agent_lbw.runtime.store.projects import can_access
from agent_lbw.runtime.store.rules import ensure_workspace_project, project_for_workspace
from agent_lbw.tools.kiwi import KiwiError

_HEX = re.compile(r"^[a-f0-9]{12}$")
_FILE_PATH = re.compile(r"^skills/src_[a-f0-9]{12}/[A-Za-z0-9][A-Za-z0-9_./-]{0,160}$")
_SCOPES = frozenset({"tenant", "team", "project"})


def skill_space(tenant_id: str, *, team_id: str = "", project_id: str = "") -> str:
    """The only KiwiFS space for this organization, team, and project."""
    org = _body("org_", tenant_id)
    team = _body("team_", team_id) if team_id else ""
    project = _body("proj_", project_id) if project_id else ""
    if project and team:
        return f"sk-org-{org}-team-{team}-proj-{project}"
    if project:
        return f"sk-org-{org}-proj-{project}"
    if team:
        return f"sk-org-{org}-team-{team}"
    return f"sk-org-{org}"


def add_repo(
    db: Database,
    kiwi: Any,
    account: dict,
    *,
    cache: Path,
    url: str,
    scope_type: str,
    scope_id: str,
    team_id: str = "",
    ref: str = "",
) -> dict:
    tenant_id = account["org"]["id"]
    scope, scope_key, team_key = _bound_scope(db, account, tenant_id, scope_type, scope_id, team_id)
    space = _space_for(tenant_id, scope, scope_key, team_key)
    repo_url = url.strip()
    if not repo_url:
        raise ValueError("repository URL is required")
    existing = db.fetchone(
        """
        SELECT id FROM skill_repos
        WHERE tenant_id = ? AND scope_type = ? AND scope_id = ? AND team_id = ? AND url = ?
        """,
        (tenant_id, scope, scope_key, team_key, repo_url),
    )
    if existing is not None:
        return sync_repo(db, kiwi, account, repo_id=str(existing["id"]), cache=cache)
    repo_id = new_id("src")
    checkout, sha = fetch_skill_checkout(cache, repo_url, repo_id, ref=ref)
    documents = skill_documents(checkout)
    db.execute(
        """
        INSERT INTO skill_repos (
            id, tenant_id, scope_type, scope_id, team_id, url, sha, space_name
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (repo_id, tenant_id, scope, scope_key, team_key, repo_url, sha, space),
    )
    _replace_files(db, kiwi, repo_id, space, documents)
    return _public(db, _row(db, tenant_id, repo_id), account)


def sync_repo(
    db: Database,
    kiwi: Any,
    account: dict,
    *,
    repo_id: str,
    cache: Path,
) -> dict:
    tenant_id = account["org"]["id"]
    row = _row(db, tenant_id, repo_id)
    _require_write(db, account, row)
    space = _required_space(row)
    checkout, sha = fetch_skill_checkout(cache, str(row["url"]), str(row["id"]))
    documents = skill_documents(checkout)
    previous = [str(item["path"]) for item in _files(db, str(row["id"]))]
    current = _replace_files(db, kiwi, str(row["id"]), space, documents)
    for path in previous:
        if path not in current:
            _delete_file(kiwi, space, path)
    db.execute("UPDATE skill_repos SET sha = ? WHERE id = ?", (sha, row["id"]))
    return _public(db, _row(db, tenant_id, str(row["id"])), account)


def set_repo_enabled(
    db: Database,
    account: dict,
    *,
    repo_id: str,
    enabled: bool,
) -> None:
    """Turn every skill in one repository on or off."""
    tenant_id = account["org"]["id"]
    row = _row(db, tenant_id, repo_id)
    _require_write(db, account, row)
    flag = 1 if enabled else 0
    db.execute("UPDATE skill_repos SET enabled = ? WHERE id = ?", (flag, row["id"]))
    db.execute(
        "UPDATE skill_repo_files SET enabled = ? WHERE repo_id = ?",
        (flag, row["id"]),
    )


def set_skill_enabled(
    db: Database,
    account: dict,
    *,
    repo_id: str,
    name: str,
    enabled: bool,
) -> None:
    """Turn one skill in a repository on or off."""
    tenant_id = account["org"]["id"]
    row = _row(db, tenant_id, repo_id)
    _require_write(db, account, row)
    wanted = name.strip()
    flag = 1 if enabled else 0
    matched = False
    for item in _files(db, str(row["id"])):
        if Path(str(item["path"])).parent.name != wanted:
            continue
        db.execute(
            "UPDATE skill_repo_files SET enabled = ? WHERE repo_id = ? AND path = ?",
            (flag, row["id"], item["path"]),
        )
        matched = True
    if not matched:
        raise KeyError(wanted)
    still_on = any(int(item["enabled"]) for item in _files(db, str(row["id"])))
    db.execute(
        "UPDATE skill_repos SET enabled = ? WHERE id = ?",
        (1 if still_on else 0, row["id"]),
    )


def skill_document(
    db: Database,
    kiwi: Any,
    account: dict,
    *,
    repo_id: str,
    name: str,
) -> dict:
    """The stored markdown for one skill in a repository."""
    tenant_id = account["org"]["id"]
    row = _row(db, tenant_id, repo_id)
    space = _required_space(row)
    wanted = name.strip()
    for item in _files(db, str(row["id"])):
        path = str(item["path"])
        if Path(path).parent.name != wanted:
            continue
        text = kiwi.in_space(space).raw(path)
        parsed, description, body = parse_skill_markdown(text, fallback_name=wanted)
        return Skill(
            name=parsed or wanted,
            description=description,
            body=body,
            origin="source",
            path=path,
            source_url=str(row["url"]),
            enabled=bool(int(item["enabled"])),
        ).public_dict(include_body=True)
    raise KeyError(wanted)


def remove_repo(
    db: Database,
    kiwi: Any,
    account: dict,
    *,
    repo_id: str,
) -> None:
    """Drop one skill repository and the files stored for it."""
    tenant_id = account["org"]["id"]
    row = _row(db, tenant_id, repo_id)
    _require_write(db, account, row)
    space = _required_space(row)
    for item in _files(db, str(row["id"])):
        _delete_file(kiwi, space, str(item["path"]))
    db.execute("DELETE FROM skill_repo_files WHERE repo_id = ?", (row["id"],))
    db.execute(
        "DELETE FROM skill_repos WHERE id = ? AND tenant_id = ?",
        (row["id"], tenant_id),
    )


def remove_workspace_repo(
    db: Database,
    kiwi: Any,
    account: dict,
    workspace: Path,
    *,
    repo_id: str,
) -> None:
    """Remove a repository stored on this project. An organization source stays."""
    project, _teams = project_repo_context(db, account, workspace)
    tenant_id = account["org"]["id"]
    row = _row(db, tenant_id, repo_id)
    if str(row["scope_type"]) != "project" or str(row["scope_id"]) != str(project["id"]):
        raise PermissionError("that repository belongs to the organization")
    remove_repo(db, kiwi, account, repo_id=str(row["id"]))


def repos_for_settings(db: Database, account: dict) -> list[dict]:
    """Organization and team repositories an owner can resync."""
    tenant_id = account["org"]["id"]
    rows = db.fetchall(
        """
        SELECT id, tenant_id, scope_type, scope_id, team_id, url, sha, space_name, enabled
        FROM skill_repos
        WHERE tenant_id = ? AND scope_type IN ('tenant', 'team')
        ORDER BY scope_type, url
        """,
        (tenant_id,),
    )
    found: list[dict] = []
    for row in rows:
        if _required_space(row, strict=False) is None:
            continue
        if not can_manage_org(account) and str(row["scope_type"]) != "tenant":
            continue
        found.append(_public(db, row, account))
    return found


def visible_repos(db: Database, tenant_id: str, project_id: str) -> list[dict]:
    """Repositories this project may read. A mismatched space is left out."""
    teams = _granted_teams(db, tenant_id, project_id)
    rows = db.fetchall(
        """
        SELECT id, tenant_id, scope_type, scope_id, team_id, url, sha, space_name, enabled
        FROM skill_repos
        WHERE tenant_id = ?
        ORDER BY scope_type, scope_id, url
        """,
        (tenant_id,),
    )
    visible: list[dict] = []
    for row in rows:
        if not _visible(row, project_id, teams):
            continue
        if _required_space(row, strict=False) is None:
            continue
        visible.append(row)
    rank = {"tenant": 0, "team": 1, "project": 2}
    visible.sort(key=lambda row: (rank.get(str(row["scope_type"]), 9), str(row["url"])))
    return visible


def skills_for_project(
    db: Database,
    kiwi: Any,
    *,
    tenant_id: str,
    project_id: str,
    enabled: set[str] | None = None,
) -> list[Skill]:
    turned_on = enabled if enabled is not None else set()
    found: list[Skill] = []
    for row in visible_repos(db, tenant_id, project_id):
        if not int(row["enabled"]):
            continue
        space = _required_space(row, strict=False)
        if space is None:
            continue
        for item in _files(db, str(row["id"])):
            if not int(item["enabled"]):
                continue
            path = str(item["path"])
            if not _file_path(str(row["id"]), path):
                continue
            try:
                text = kiwi.in_space(space).raw(path)
            except KiwiError:
                continue
            if not text.strip():
                continue
            fallback = Path(path).parent.name
            name, description, body = parse_skill_markdown(text, fallback_name=fallback)
            if not name:
                continue
            found.append(
                Skill(
                    name=name,
                    description=description,
                    body=body,
                    origin="source",
                    path=path,
                    source_url=str(row["url"]),
                    enabled=name in turned_on,
                )
            )
    return found


def project_repo_context(
    db: Database,
    account: dict,
    workspace: Path,
    *,
    create: bool = False,
) -> tuple[dict, list[dict]]:
    """The project for this folder, and the teams that can use it."""
    tenant_id = account["org"]["id"]
    project = project_for_workspace(db, tenant_id, workspace)
    if project is None and create and can_manage_org(account):
        project = ensure_workspace_project(db, tenant_id, workspace)
    if project is None:
        raise ValueError("this workspace is not a project yet")
    if not can_access(db, account["current_user_id"], project["id"], need="use"):
        raise PermissionError("you cannot use this project")
    teams = [
        {"id": row["id"], "name": row["name"]} for row in _team_rows(db, tenant_id, project["id"])
    ]
    return project, teams


def add_workspace_repo(
    db: Database,
    kiwi: Any,
    account: dict,
    workspace: Path,
    *,
    cache: Path,
    url: str,
    team_id: str = "",
    ref: str = "",
) -> dict:
    project, _teams = project_repo_context(db, account, workspace, create=True)
    return add_repo(
        db,
        kiwi,
        account,
        cache=cache,
        url=url,
        scope_type="project",
        scope_id=project["id"],
        team_id=team_id,
        ref=ref,
    )


def skills_on_workspace(
    db: Database,
    kiwi: Any,
    account: dict,
    workspace: Path,
) -> tuple[list[dict], list[Skill], list[dict]]:
    """Visible repositories, their skills, and teams for the add form."""
    try:
        project, teams = project_repo_context(db, account, workspace, create=True)
    except (PermissionError, ValueError):
        return [], [], []
    tenant_id = account["org"]["id"]
    enabled = set(enabled_skill_names(workspace))
    repos = [_public(db, row, account) for row in visible_repos(db, tenant_id, project["id"])]
    skills = skills_for_project(
        db,
        kiwi,
        tenant_id=tenant_id,
        project_id=project["id"],
        enabled=enabled,
    )
    return repos, skills, teams


def _bound_scope(
    db: Database,
    account: dict,
    tenant_id: str,
    scope_type: str,
    scope_id: str,
    team_id: str,
) -> tuple[str, str, str]:
    if scope_type not in _SCOPES:
        raise ValueError("unknown scope")
    if scope_type == "tenant":
        if scope_id != tenant_id:
            raise ValueError("unknown scope")
        _require_manager(account)
        return "tenant", tenant_id, ""
    if scope_type == "team":
        _require_manager(account)
        _require_team(db, tenant_id, scope_id)
        return "team", scope_id, ""
    project = db.fetchone(
        "SELECT id FROM projects WHERE id = ? AND tenant_id = ?",
        (scope_id, tenant_id),
    )
    if project is None:
        raise ValueError("unknown project")
    if not can_access(db, account["current_user_id"], scope_id, need="manage"):
        raise PermissionError("manage access is required")
    chosen = _choose_team(db, tenant_id, scope_id, team_id)
    return "project", scope_id, chosen


def _choose_team(db: Database, tenant_id: str, project_id: str, team_id: str) -> str:
    teams = _granted_teams(db, tenant_id, project_id)
    wanted = (team_id or "").strip()
    if not teams:
        if wanted:
            raise ValueError("this project has no team")
        return ""
    if len(teams) == 1 and not wanted:
        return teams[0]
    if wanted not in teams:
        raise ValueError("choose a team")
    return wanted


def _require_manager(account: dict) -> None:
    if not can_manage_org(account):
        raise PermissionError("an owner or admin manages organization settings")


def _require_write(db: Database, account: dict, row: dict) -> None:
    _bound_scope(
        db,
        account,
        str(row["tenant_id"]),
        str(row["scope_type"]),
        str(row["scope_id"]),
        str(row["team_id"] or ""),
    )


def _visible(row: dict, project_id: str, teams: list[str]) -> bool:
    scope = str(row["scope_type"])
    if scope == "tenant":
        return str(row["scope_id"]) == str(row["tenant_id"]) and not row["team_id"]
    if scope == "team":
        return str(row["scope_id"]) in teams and not row["team_id"]
    if scope == "project":
        if str(row["scope_id"]) != project_id:
            return False
        team = str(row["team_id"] or "")
        return team == "" or team in teams
    return False


def _space_for(tenant_id: str, scope: str, scope_id: str, team_id: str) -> str:
    if scope == "tenant":
        return skill_space(tenant_id)
    if scope == "team":
        return skill_space(tenant_id, team_id=scope_id)
    return skill_space(tenant_id, team_id=team_id, project_id=scope_id)


def _required_space(row: dict, *, strict: bool = True) -> str | None:
    try:
        expected = _space_for(
            str(row["tenant_id"]),
            str(row["scope_type"]),
            str(row["scope_id"]),
            str(row["team_id"] or ""),
        )
    except ValueError:
        if strict:
            raise
        return None
    if str(row["space_name"]) != expected:
        if strict:
            raise ValueError("skill repository is not in its scope")
        return None
    return expected


def _replace_files(
    db: Database,
    kiwi: Any,
    repo_id: str,
    space: str,
    documents: list[tuple[str, str]],
) -> set[str]:
    kiwi.ensure_space(space)
    client = kiwi.in_space(space)
    paths: set[str] = set()
    previous = {str(item["path"]): int(item["enabled"]) for item in _files(db, repo_id)}
    repo = db.fetchone("SELECT enabled FROM skill_repos WHERE id = ?", (repo_id,))
    inherited = int(repo["enabled"]) if repo is not None else 1
    db.execute("DELETE FROM skill_repo_files WHERE repo_id = ?", (repo_id,))
    for relative, text in documents:
        path = f"skills/{repo_id}/{relative}"
        if not _file_path(repo_id, path):
            raise ValueError("skill path is not in this scope")
        client.write(path, text)
        db.execute(
            "INSERT INTO skill_repo_files (repo_id, path, enabled) VALUES (?, ?, ?)",
            (repo_id, path, previous.get(path, inherited)),
        )
        paths.add(path)
    return paths


def _delete_file(kiwi: Any, space: str, path: str) -> None:
    if not _FILE_PATH.fullmatch(path):
        return
    try:
        kiwi.in_space(space).delete(path)
    except KiwiError as exc:
        if "404" not in str(exc):
            raise


def _file_path(repo_id: str, path: str) -> bool:
    if not path.startswith(f"skills/{repo_id}/"):
        return False
    if ".." in path.split("/"):
        return False
    if not path.endswith("/SKILL.md") and not path.endswith("/skill.md"):
        return False
    return _FILE_PATH.fullmatch(path) is not None


def _files(db: Database, repo_id: str) -> list[dict]:
    return db.fetchall(
        "SELECT path, enabled FROM skill_repo_files WHERE repo_id = ? ORDER BY path",
        (repo_id,),
    )


def _row(db: Database, tenant_id: str, repo_id: str) -> dict:
    row = db.fetchone(
        """
        SELECT id, tenant_id, scope_type, scope_id, team_id, url, sha, space_name, enabled
        FROM skill_repos WHERE id = ?
        """,
        (repo_id,),
    )
    if row is None or str(row["tenant_id"]) != tenant_id:
        raise KeyError(repo_id)
    return row


def _loaded_names(db: Database, repo_id: str) -> list[str]:
    """Skill folder names stored for this repository."""
    return [entry["name"] for entry in _skill_entries(db, repo_id)]


def _skill_entries(db: Database, repo_id: str) -> list[dict]:
    entries: list[dict] = []
    seen: set[str] = set()
    for item in _files(db, repo_id):
        parent = Path(str(item["path"])).parent.name
        if not parent or parent == repo_id or parent.startswith("src_") or parent in seen:
            continue
        seen.add(parent)
        entries.append({"name": parent, "enabled": bool(int(item["enabled"]))})
    return entries


def _public(db: Database, row: dict, account: dict) -> dict:
    space = _required_space(row)
    try:
        _require_write(db, account, row)
        can_sync = True
    except (PermissionError, ValueError):
        can_sync = False
    return {
        "id": row["id"],
        "slug": row["id"],
        "url": row["url"],
        "sha": row["sha"],
        "scope_type": row["scope_type"],
        "scope_id": row["scope_id"],
        "team_id": row["team_id"],
        "space": space,
        "can_sync": can_sync,
        "enabled": bool(int(row["enabled"])),
        "skills": _loaded_names(db, str(row["id"])),
        "entries": _skill_entries(db, str(row["id"])),
    }


def _granted_teams(db: Database, tenant_id: str, project_id: str) -> list[str]:
    return [str(row["id"]) for row in _team_rows(db, tenant_id, project_id)]


def _team_rows(db: Database, tenant_id: str, project_id: str) -> list[dict]:
    return db.fetchall(
        """
        SELECT teams.id, teams.name
        FROM project_grants
        JOIN teams ON teams.id = project_grants.subject_id
        WHERE project_grants.project_id = ?
          AND project_grants.subject_type = 'team'
          AND teams.tenant_id = ?
        ORDER BY teams.id
        """,
        (project_id, tenant_id),
    )


def _require_team(db: Database, tenant_id: str, team_id: str) -> None:
    _body("team_", team_id)
    row = db.fetchone(
        "SELECT id FROM teams WHERE id = ? AND tenant_id = ?",
        (team_id, tenant_id),
    )
    if row is None:
        raise ValueError("that team is not in this organization")


def _body(prefix: str, value: str) -> str:
    text = str(value or "")
    if not text.startswith(prefix):
        raise ValueError("unknown scope")
    body = text[len(prefix) :]
    if not _HEX.fullmatch(body):
        raise ValueError("unknown scope")
    return body
