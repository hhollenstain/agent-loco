"""GitHub App routes. The page saves the app here. Tokens are not returned."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from agent_lbw.runtime.account import load_account
from agent_lbw.runtime.db import database
from agent_lbw.runtime.store.github import (
    GitHubAppError,
    current_token,
    github_app_state,
    install_url,
    list_installations,
    save_github_app,
    select_installation,
)
from agent_lbw.runtime.store.github_sync import (
    claim_repo,
    list_repos,
    place_repo,
    set_github_login,
    sync_access,
)
from agent_lbw.runtime.workspaces import clone_workspace
from agent_lbw.tools.git import github_auth_args, github_https_url


class GitHubAppBody(BaseModel):
    app_id: str
    private_key: str = ""


class InstallationBody(BaseModel):
    installation_id: str


class GitHubLoginBody(BaseModel):
    user_id: str
    github_login: str = ""


class GitHubWorkspaceBody(BaseModel):
    full_name: str
    subject_type: str
    subject_id: str = ""
    parent: str = ""


def register_github(app: FastAPI) -> None:
    @app.get("/api/github/app", response_model=None)
    def read_app(request: Request) -> object:
        return _act(request, github_app_state)

    @app.put("/api/github/app", response_model=None)
    def write_app(request: Request, payload: GitHubAppBody) -> object:
        return _act(
            request,
            lambda db, root: save_github_app(
                db, root, app_id=payload.app_id, private_key=payload.private_key
            ),
        )

    @app.get("/api/github/installations", response_model=None)
    def read_installations(request: Request) -> object:
        return _act(
            request,
            lambda db, root: {
                "installations": list_installations(db, root),
                "install_url": install_url(db, root),
            },
        )

    @app.put("/api/github/installation", response_model=None)
    def write_installation(request: Request, payload: InstallationBody) -> object:
        return _act(
            request,
            lambda db, root: select_installation(db, root, payload.installation_id),
        )

    @app.get("/api/github/repos", response_model=None)
    def read_repos(request: Request) -> object:
        return _act(request, lambda db, root: {"repos": list_repos(db, root)})

    @app.put("/api/github/login", response_model=None)
    def write_login(request: Request, payload: GitHubLoginBody) -> object:
        return _act(
            request,
            lambda db, _root: set_github_login(
                db, user_id=payload.user_id, github_login=payload.github_login
            ),
        )

    @app.post("/api/github/sync", response_model=None)
    def sync_github(request: Request) -> object:
        return _act(request, sync_access)

    @app.post("/api/github/workspaces", response_model=None)
    def add_workspace(request: Request, payload: GitHubWorkspaceBody) -> object:
        root, database_url = _root(request)
        try:
            load_account(root, database_url=database_url)
            with database(root, database_url) as db:
                full_name = claim_repo(db, root, payload.full_name)
            parent = _clone_parent(request, payload.parent)
            owner, repo = full_name.split("/", 1)
            token = current_token(root, database_url)
            dest = Path(parent).expanduser().resolve() / repo
            if dest.exists():
                path = str(dest)
            else:
                cloned = clone_workspace(
                    github_https_url(owner, repo),
                    parent,
                    name=repo,
                    git_config=github_auth_args(token) if token else None,
                )
                path = str(cloned["path"])
            with database(root, database_url) as db:
                return place_repo(
                    db,
                    full_name=full_name,
                    path=path,
                    subject_type=payload.subject_type,
                    subject_id=payload.subject_id,
                )
        except PermissionError as exc:
            return JSONResponse({"error": str(exc)}, status_code=403)
        except GitHubAppError as exc:
            return JSONResponse({"error": str(exc)}, status_code=502)
        except KeyError:
            return JSONResponse({"error": "person not found"}, status_code=404)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)


def _act(request: Request, fn):
    root, database_url = _root(request)
    try:
        load_account(root, database_url=database_url)
        with database(root, database_url) as db:
            return fn(db, root)
    except PermissionError as exc:
        return JSONResponse({"error": str(exc)}, status_code=403)
    except GitHubAppError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)
    except KeyError:
        return JSONResponse({"error": "person not found"}, status_code=404)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


def _clone_parent(request: Request, requested: str) -> str:
    if requested.strip():
        return requested.strip()
    current = Path(request.app.state.ui.default_workspace)
    if (current / ".git").exists():
        return str(current.parent)
    return str(current)


def _root(request: Request) -> tuple[Path, str]:
    ui = request.app.state.ui
    return Path(ui.store_root), ui.manager.settings.database_url
