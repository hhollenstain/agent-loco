"""Organization settings: skill assignments and enforced roles."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from agent_lbw.runtime.account import load_account
from agent_lbw.runtime.db import database
from agent_lbw.runtime.roles import roles_catalog
from agent_lbw.runtime.skills import list_skills
from agent_lbw.runtime.store.assignments import (
    can_manage_org,
    enforced_role_names,
    list_skill_grants,
    set_enforced_role,
    set_skill_grant,
    skills_for_project,
)
from agent_lbw.runtime.store.install import require_account
from agent_lbw.runtime.store.rules import project_for_workspace


class SkillGrantBody(BaseModel):
    skill_name: str
    scope_type: str
    scope_id: str
    assigned: bool = True


class EnforcedRoleBody(BaseModel):
    role_name: str
    enforced: bool = True


class SkillRepoBody(BaseModel):
    url: str
    scope_type: str
    scope_id: str
    ref: str = ""


def register_org(app: FastAPI) -> None:
    @app.get("/api/org/settings", response_model=None)
    def read_settings(request: Request) -> object:
        return _act(request, lambda db, root: _settings(db, root))

    @app.put("/api/org/skills", response_model=None)
    def write_skill(request: Request, payload: SkillGrantBody) -> object:
        def apply(db, root: Path) -> dict:
            if payload.skill_name.strip() not in _bundled_skill_names(root):
                raise ValueError("unknown skill")
            set_skill_grant(
                db,
                skill_name=payload.skill_name,
                scope_type=payload.scope_type,
                scope_id=payload.scope_id,
                assigned=payload.assigned,
            )
            return _settings(db, root)

        return _act(request, apply)

    @app.put("/api/org/roles", response_model=None)
    def write_role(request: Request, payload: EnforcedRoleBody) -> object:
        def apply(db, root: Path) -> dict:
            known = {role["name"] for role in _bundled_roles(root)}
            if payload.role_name.strip() not in known:
                raise ValueError("unknown role")
            set_enforced_role(db, payload.role_name, enforced=payload.enforced)
            return _settings(db, root)

        return _act(request, apply)

    @app.post("/api/org/skill-repos", response_model=None)
    def add_skill_repo(request: Request, payload: SkillRepoBody) -> object:
        def apply(db, root: Path, kiwi) -> dict:
            from agent_lbw.runtime.store.skill_repos import add_repo

            add_repo(
                db,
                kiwi,
                require_account(db),
                cache=root,
                url=payload.url,
                scope_type=payload.scope_type,
                scope_id=payload.scope_id,
                ref=payload.ref,
            )
            return _settings(db, root)

        return _act_kiwi(request, apply)

    @app.post("/api/org/skill-repos/{repo_id}/sync", response_model=None)
    def sync_skill_repo(request: Request, repo_id: str) -> object:
        def apply(db, root: Path, kiwi) -> dict:
            from agent_lbw.runtime.store.skill_repos import sync_repo

            sync_repo(db, kiwi, require_account(db), repo_id=repo_id, cache=root)
            return _settings(db, root)

        return _act_kiwi(request, apply)


def assigned_skill_names(root: Path, workspace: Path, database_url: str = "") -> list[str]:
    """Skills the organization assigned to the project at this workspace."""
    try:
        with database(root, database_url) as db:
            account = require_account(db)
            project = project_for_workspace(db, account["org"]["id"], workspace)
            if project is None:
                return []
            return skills_for_project(db, account["org"]["id"], project["id"])
    except (OSError, ValueError):
        return []


def enforced_roles_for(root: Path, database_url: str = "") -> list[str]:
    try:
        with database(root, database_url) as db:
            return enforced_role_names(db)
    except (OSError, ValueError):
        return []


def _settings(db, root: Path) -> dict:
    account = require_account(db)
    skills = _bundled_skills(root)
    roles = _bundled_roles(root)
    known_skills = {skill.name for skill in skills}
    known_roles = {role["name"] for role in roles}
    return {
        "can_manage": can_manage_org(account),
        "org_id": account["org"]["id"],
        "skills": [skill.public_dict() for skill in skills],
        "grants": [grant for grant in list_skill_grants(db) if grant["skill_name"] in known_skills],
        "roles": roles,
        "enforced": [name for name in enforced_role_names(db) if name in known_roles],
        "teams": [
            {"id": row["id"], "name": row["name"]}
            for row in db.fetchall(
                "SELECT id, name FROM teams WHERE tenant_id = ? ORDER BY name, id",
                (account["org"]["id"],),
            )
        ],
        "projects": [
            {"id": row["id"], "name": row["name"]}
            for row in db.fetchall(
                "SELECT id, name FROM projects WHERE tenant_id = ? ORDER BY name, id",
                (account["org"]["id"],),
            )
        ],
        "skill_repos": _skill_repos(db, account),
    }


def _bundled_skills(root: Path):
    return [skill for skill in list_skills(root) if skill.origin == "bundled"]


def _bundled_roles(root: Path) -> list[dict]:
    catalog = roles_catalog(root)
    roles = catalog.get("roles")
    if not isinstance(roles, list):
        return []
    return [role for role in roles if role.get("origin") == "bundled"]


def _bundled_skill_names(root: Path) -> set[str]:
    return {skill.name for skill in _bundled_skills(root)}


def _skill_repos(db, account: dict) -> list[dict]:
    from agent_lbw.runtime.store.skill_repos import repos_for_settings

    return repos_for_settings(db, account)


def _act(request: Request, fn):
    root, database_url = _root(request)
    try:
        load_account(root, database_url=database_url)
        with database(root, database_url) as db:
            return fn(db, root)
    except PermissionError as exc:
        return JSONResponse({"error": str(exc)}, status_code=403)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


def _act_kiwi(request: Request, fn):
    from agent_lbw.rules_http import open_kiwi
    from agent_lbw.tools.kiwi import KiwiError, normalize_kiwifs_url

    ui = request.app.state.ui
    kiwi_url = normalize_kiwifs_url(ui.manager.settings.kiwifs_url)
    if not kiwi_url:
        return JSONResponse({"error": "KiwiFS is not configured"}, status_code=400)
    root, database_url = _root(request)
    try:
        load_account(root, database_url=database_url)
        kiwi = open_kiwi(kiwi_url, ui.manager.settings.kiwifs_api_key)
        with database(root, database_url) as db:
            return fn(db, root, kiwi)
    except PermissionError as exc:
        return JSONResponse({"error": str(exc)}, status_code=403)
    except KeyError:
        return JSONResponse({"error": "not found"}, status_code=404)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except KiwiError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)


def _root(request: Request) -> tuple[Path, str]:
    ui = request.app.state.ui
    return Path(ui.store_root), ui.manager.settings.database_url
