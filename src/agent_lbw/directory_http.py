"""Directory routes: tenants, teams, grants, and a sealed task request.

The profile routes stay on `/api/account`. These routes are the rest of the
directory. A task goal is returned only to the current person, and only when
that person can use the project.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from agent_lbw.runtime.account import load_account
from agent_lbw.runtime.db import database
from agent_lbw.runtime.store.duties import grant_duty, list_duties
from agent_lbw.runtime.store.install import require_account
from agent_lbw.runtime.store.names import new_id
from agent_lbw.runtime.store.projects import (
    ensure_project,
    grant_project,
    list_grants,
    list_projects,
    require_project,
)
from agent_lbw.runtime.store.schedules import save_schedule
from agent_lbw.runtime.store.tasks import read_task
from agent_lbw.runtime.store.teams import (
    add_team_member,
    create_team,
    list_teams,
    promote_to_organization,
    remove_team,
    remove_team_member,
)


class TeamBody(BaseModel):
    name: str


class TeamMemberBody(BaseModel):
    identity_id: str
    rank: str = "member"


class DutyBody(BaseModel):
    scope_type: str
    scope_id: str
    identity_id: str
    duty: str


class ProjectBody(BaseModel):
    name: str
    kind: str
    locator: str


class GrantBody(BaseModel):
    subject_type: str
    subject_id: str
    access: str


class ScanBody(BaseModel):
    project_id: str
    cadence: str


def register_directory(app: FastAPI) -> None:
    @app.get("/api/directory", response_model=None)
    def directory(request: Request) -> object:
        root, url = _ready(request)
        with database(root, url) as db:
            account = require_account(db)
            tenant_id = account["org"]["id"]
            return {
                "tenant": account["org"],
                "members": account["users"],
                "teams": list_teams(db, tenant_id),
                "duties": list_duties(db, tenant_id),
                "projects": list_projects(db, tenant_id),
                "grants": list_grants(db, tenant_id),
            }

    @app.post("/api/directory/promote", response_model=None)
    def promote(request: Request) -> object:
        return _act(request, _promote)

    @app.post("/api/directory/teams", response_model=None)
    def teams(request: Request, body: TeamBody) -> object:
        return _act(request, lambda db, account, root: _team(db, account, body))

    @app.post("/api/directory/teams/{team_id}/members", response_model=None)
    def team_member(request: Request, team_id: str, body: TeamMemberBody) -> object:
        return _act(request, lambda db, account, root: _team_member(db, account, team_id, body))

    @app.delete("/api/directory/teams/{team_id}", response_model=None)
    def drop_team(request: Request, team_id: str) -> object:
        return _act(request, lambda db, account, root: _drop_team(db, account, team_id))

    @app.delete("/api/directory/teams/{team_id}/members/{identity_id}", response_model=None)
    def drop_team_member(request: Request, team_id: str, identity_id: str) -> object:
        return _act(
            request,
            lambda db, account, root: _drop_team_member(db, account, team_id, identity_id),
        )

    @app.post("/api/directory/duties", response_model=None)
    def duties(request: Request, body: DutyBody) -> object:
        return _act(request, lambda db, account, root: _duty(db, body))

    @app.post("/api/directory/projects", response_model=None)
    def projects(request: Request, body: ProjectBody) -> object:
        return _act(request, lambda db, account, root: _project(db, account, body))

    @app.post("/api/directory/projects/{project_id}/grants", response_model=None)
    def grants(request: Request, project_id: str, body: GrantBody) -> object:
        return _act(request, lambda db, account, root: _grant(db, project_id, body))

    @app.get("/api/directory/tasks/{task_id}", response_model=None)
    def task(request: Request, task_id: str) -> object:
        return _act(request, lambda db, account, root: _task(db, root, account, task_id))

    @app.post("/api/directory/schedules", response_model=None)
    def scan(request: Request, body: ScanBody) -> object:
        return _act(request, lambda db, account, root: _scan(db, account, body))


def _ready(request: Request) -> tuple[Path, str]:
    ui = request.app.state.ui
    root = Path(ui.store_root)
    url = ui.manager.settings.database_url
    load_account(root, database_url=url)
    return root, url


def _act(request: Request, action) -> object:
    root, url = _ready(request)
    try:
        with database(root, url) as db:
            account = require_account(db)
            return action(db, account, root)
    except (ValueError, PermissionError, KeyError) as exc:
        return _error(exc)


def _error(exc: Exception) -> JSONResponse:
    if isinstance(exc, PermissionError):
        return JSONResponse({"error": str(exc)}, status_code=403)
    if isinstance(exc, KeyError):
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse({"error": str(exc)}, status_code=400)


def _promote(db, account: dict, root: Path) -> dict:
    promote_to_organization(db, account["org"]["id"])
    account["org"]["kind"] = "organization"
    return {"tenant": account["org"]}


def _team(db, account: dict, body: TeamBody) -> dict:
    _require_manager(account)
    return create_team(db, account["org"]["id"], body.name)


def _team_member(db, account: dict, team_id: str, body: TeamMemberBody) -> dict:
    _require_manager(account)
    if body.rank not in {"admin", "member"}:
        raise ValueError("a team person is an admin or a member")
    add_team_member(db, team_id, body.identity_id, body.rank)
    return {"teams": list_teams(db, account["org"]["id"])}


def _drop_team(db, account: dict, team_id: str) -> dict:
    _require_manager(account)
    remove_team(db, account["org"]["id"], team_id)
    return {"teams": list_teams(db, account["org"]["id"])}


def _drop_team_member(db, account: dict, team_id: str, identity_id: str) -> dict:
    _require_manager(account)
    remove_team_member(db, team_id, identity_id)
    return {"teams": list_teams(db, account["org"]["id"])}


def _require_manager(account: dict) -> None:
    current = next(
        (user for user in account["users"] if user["id"] == account["current_user_id"]),
        None,
    )
    if current is None or current["role"] not in {"owner", "admin"}:
        raise PermissionError("an owner or admin manages teams")


def _duty(db, body: DutyBody) -> dict:
    return grant_duty(
        db,
        scope_type=body.scope_type,
        scope_id=body.scope_id,
        identity_id=body.identity_id,
        duty=body.duty,
    )


def _project(db, account: dict, body: ProjectBody) -> dict:
    return ensure_project(
        db,
        account["org"]["id"],
        name=body.name,
        kind=body.kind,
        locator=body.locator,
    )


def _grant(db, project_id: str, body: GrantBody) -> dict:
    return grant_project(
        db,
        project_id,
        subject_type=body.subject_type,
        subject_id=body.subject_id,
        access=body.access,
    )


def _task(db, root: Path, account: dict, task_id: str) -> dict:
    return read_task(db, root, task_id=task_id, identity_id=account["current_user_id"])


def _scan(db, account: dict, body: ScanBody) -> dict:
    tenant_id = account["org"]["id"]
    require_project(db, tenant_id, body.project_id)
    schedule_id = new_id("sched")
    save_schedule(
        db,
        schedule_id=schedule_id,
        tenant_id=tenant_id,
        project_id=body.project_id,
        kind="scan",
        cadence=body.cadence,
        enabled=True,
        created_by=account["current_user_id"],
    )
    return {"id": schedule_id, "kind": "scan", "project_id": body.project_id}
