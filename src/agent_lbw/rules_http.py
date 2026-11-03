"""Rule routes. The page edits rules here. The text is stored in KiwiFS."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from agent_lbw.runtime.account import load_account
from agent_lbw.runtime.db import database
from agent_lbw.runtime.store.install import require_account
from agent_lbw.runtime.store.rules import (
    applied_view,
    delete_rule,
    editor_view,
    ensure_workspace_project,
    project_for_workspace,
    save_rule,
    set_required,
    update_rule,
)
from agent_lbw.tools.kiwi import KiwiError, KiwiFS, normalize_kiwifs_url


class RuleBody(BaseModel):
    scope_type: str
    scope_id: str
    name: str
    body: str


class RuleUpdate(BaseModel):
    name: str
    body: str


class RequiredBody(BaseModel):
    project_id: str
    required: bool = True


def open_kiwi(base_url: str, api_key: str = "") -> KiwiFS:
    return KiwiFS(base_url, api_key=api_key)


def register_rules(app: FastAPI) -> None:
    @app.get("/api/rules/editor", response_model=None)
    def editor(request: Request) -> object:
        return _act(request, lambda db, account, kiwi, _root: _editor(db, account, kiwi))

    @app.get("/api/rules/applied", response_model=None)
    def applied(request: Request, workspace: str = "") -> object:
        return _act(
            request,
            lambda db, account, kiwi, root: _applied(db, account, kiwi, root, workspace),
        )

    @app.post("/api/rules", response_model=None)
    def create(request: Request, body: RuleBody) -> object:
        return _act(request, lambda db, account, kiwi, _root: _create(db, account, kiwi, body))

    @app.put("/api/rules/{rule_id}", response_model=None)
    def update(request: Request, rule_id: str, body: RuleUpdate) -> object:
        return _act(
            request,
            lambda db, account, kiwi, _root: _update(db, account, kiwi, rule_id, body),
        )

    @app.delete("/api/rules/{rule_id}", response_model=None)
    def remove(request: Request, rule_id: str) -> object:
        return _act(
            request,
            lambda db, account, kiwi, _root: _remove(db, account, kiwi, rule_id),
        )

    @app.put("/api/rules/{rule_id}/required", response_model=None)
    def required(request: Request, rule_id: str, body: RequiredBody) -> object:
        return _act(
            request,
            lambda db, account, kiwi, _root: _required(db, account, rule_id, body),
        )


def _editor(db, account: dict, kiwi: KiwiFS) -> dict:
    return editor_view(
        db,
        kiwi,
        identity_id=account["current_user_id"],
        tenant_id=account["org"]["id"],
    )


def _applied(db, account: dict, kiwi: KiwiFS, _root: Path, workspace: str) -> dict:
    place = (workspace or "").strip()
    if not place:
        raise ValueError("workspace is required")
    path = Path(place).expanduser()
    if not path.is_dir():
        raise ValueError("workspace is not a directory")
    tenant_id = account["org"]["id"]
    identity_id = account["current_user_id"]
    project = project_for_workspace(db, tenant_id, path)
    rank = _rank(account, identity_id)
    if project is None and rank in {"owner", "admin"}:
        project = ensure_workspace_project(db, tenant_id, path)
    if project is None:
        return {
            "project": None,
            "can_write": False,
            "rules": [],
            "note": "This workspace is not a project yet.",
        }
    view = applied_view(
        db,
        kiwi,
        identity_id=identity_id,
        tenant_id=tenant_id,
        project_id=project["id"],
    )
    if not view["rules"]:
        view["note"] = "No organization, team, or project rules apply to this workspace yet."
    return view


def _create(db, account: dict, kiwi: KiwiFS, body: RuleBody) -> dict:
    return save_rule(
        db,
        kiwi,
        identity_id=account["current_user_id"],
        tenant_id=account["org"]["id"],
        scope_type=body.scope_type,
        scope_id=body.scope_id,
        name=body.name,
        body=body.body,
    )


def _update(db, account: dict, kiwi: KiwiFS, rule_id: str, body: RuleUpdate) -> dict:
    return update_rule(
        db,
        kiwi,
        identity_id=account["current_user_id"],
        tenant_id=account["org"]["id"],
        rule_id=rule_id,
        name=body.name,
        body=body.body,
    )


def _remove(db, account: dict, kiwi: KiwiFS, rule_id: str) -> dict:
    return delete_rule(
        db,
        kiwi,
        identity_id=account["current_user_id"],
        tenant_id=account["org"]["id"],
        rule_id=rule_id,
    )


def _required(db, account: dict, rule_id: str, body: RequiredBody) -> dict:
    return set_required(
        db,
        identity_id=account["current_user_id"],
        tenant_id=account["org"]["id"],
        rule_id=rule_id,
        project_id=body.project_id,
        required=body.required,
    )


def _rank(account: dict, identity_id: str) -> str:
    for user in account["users"]:
        if user["id"] == identity_id:
            return str(user["role"])
    return ""


def _act(request: Request, action) -> object:
    ui = request.app.state.ui
    root = Path(ui.store_root)
    url = ui.manager.settings.database_url
    kiwi_url = normalize_kiwifs_url(ui.manager.settings.kiwifs_url)
    if not kiwi_url:
        return JSONResponse({"error": "KiwiFS is not configured"}, status_code=400)
    load_account(root, database_url=url)
    kiwi = open_kiwi(kiwi_url, ui.manager.settings.kiwifs_api_key)
    try:
        with database(root, url) as db:
            account = require_account(db)
            return action(db, account, kiwi, root)
    except PermissionError as exc:
        return JSONResponse({"error": str(exc)}, status_code=403)
    except KeyError:
        return JSONResponse({"error": "not found"}, status_code=404)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except KiwiError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)
