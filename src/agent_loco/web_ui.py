from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from agent_loco.config import Settings
from agent_loco.llm.client import normalize_model_base_url
from agent_loco.runtime.importer import load_goals_from_workspace
from agent_loco.runtime.project import (
    default_guidelines,
    guidelines_are_custom,
    save_guidelines,
)
from agent_loco.runtime.servers import (
    list_known_servers,
    load_selection,
    remember_server,
    update_server_alias,
)
from agent_loco.runtime.tasks import TaskManager
from agent_loco.runtime.uireview import resolve_ui_screenshot
from agent_loco.runtime.workspaces import (
    archive_workspace,
    browse_directory,
    clone_workspace,
    create_workspace,
    forget_workspace,
    last_workspace,
    load_archived_workspaces,
    load_workspaces,
    remember_workspace,
)

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent / "static"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
FAVICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">'
    '<rect width="16" height="16" rx="3" fill="#111111"/>'
    '<circle cx="8" cy="8" r="4" fill="#7dba5d"/>'
    "</svg>"
)


class TaskCreate(BaseModel):
    workspace: str | None = None
    goal: str | None = None
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    auto_commit: bool | None = None
    create_pr: bool | None = None
    resume_branch: str | None = None
    resume_sha: str | None = None


class RerunPayload(BaseModel):
    sha: str | None = None


class ModelsQuery(BaseModel):
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None


class HistoryQuery(BaseModel):
    page: int = 1
    page_size: int = 10
    search: str | None = None


class ServerAliasUpdate(BaseModel):
    url: str
    alias: str | None = None


class UrlCreate(BaseModel):
    url: str
    alias: str | None = None


class WorkspaceSelect(BaseModel):
    path: str
    guidelines: str | None = None


class WorkspaceGuidelines(BaseModel):
    path: str | None = None
    guidelines: str | None = None


class WorkspaceClone(BaseModel):
    url: str
    parent: str | None = None
    name: str | None = None
    guidelines: str | None = None


class UiState:
    def __init__(
        self,
        manager: TaskManager,
        *,
        default_workspace: Path,
        default_goal: str | None,
        default_create_pr: bool | None,
    ) -> None:
        self.manager = manager
        self.store_root = str(default_workspace.expanduser().resolve())
        self.default_workspace = last_workspace(
            Path(self.store_root), default=self.store_root
        )
        self.default_goal = default_goal or ""
        self.default_auto_commit = manager.settings.auto_commit
        self.default_create_pr = bool(
            default_create_pr if default_create_pr is not None else manager.settings.create_pr
        )

    def known_servers(self) -> list[dict[str, str]]:
        return list_known_servers(
            Path(self.default_workspace),
            default=self.manager.settings.model_base_url,
        )

    def selection(self) -> dict[str, str | list[dict[str, str]] | None]:
        return load_selection(
            Path(self.default_workspace),
            default_url=self.manager.settings.model_base_url,
            default_model=self.manager.settings.model_name,
        )

    def remember_current_workspace(self) -> list[dict[str, str | bool]]:
        return remember_workspace(Path(self.store_root), self.default_workspace)

    def set_workspace(self, path: str) -> list[dict[str, str | bool]]:
        remembered = remember_workspace(Path(self.store_root), path)
        self.default_workspace = last_workspace(
            Path(self.store_root), default=path
        )
        return remembered

    def _refresh_current_workspace(self) -> None:
        self.default_workspace = last_workspace(
            Path(self.store_root), default=self.store_root
        )

    def archive_workspace_path(self, path: str) -> list[dict[str, str | bool]]:
        remembered = archive_workspace(Path(self.store_root), path)
        self._refresh_current_workspace()
        return remembered

    def forget_workspace_path(self, path: str) -> list[dict[str, str | bool]]:
        remembered = forget_workspace(Path(self.store_root), path)
        self._refresh_current_workspace()
        return remembered

    def remember_server(self, url: str, model: str | None = None) -> list[dict[str, str]]:
        return remember_server(
            Path(self.default_workspace),
            url,
            model=model,
            default=self.manager.settings.model_base_url,
        )

    def template_vars(self) -> dict[str, Any]:
        selected = self.selection()
        workspaces = self.remember_current_workspace()
        current = next(
            (item for item in workspaces if item["path"] == self.default_workspace),
            None,
        )
        return {
            "default_workspace": self.default_workspace,
            "known_workspaces": workspaces,
            "workspace_is_git": bool((current or {}).get("is_git")),
            "workspace_git_remote": (current or {}).get("git_remote") or "",
            "guidelines": (current or {}).get("guidelines") or default_guidelines(),
            "custom_guidelines": bool((current or {}).get("custom_guidelines")),
            "default_guidelines": default_guidelines(),
            "default_goal": self.default_goal,
            "default_auto_commit": self.default_auto_commit,
            "default_create_pr": self.default_create_pr,
            "default_model": selected["last_model"] or self.manager.settings.model_name,
            "default_base_url": selected["last_base_url"]
            or self.manager.settings.model_base_url,
            "known_servers": selected["servers"],
            "max_concurrent": self.manager.max_concurrent,
        }

    def meta(self) -> dict[str, Any]:
        selected = self.selection()
        return {
            "default_workspace": self.default_workspace,
            "known_workspaces": load_workspaces(
                Path(self.store_root), default=self.default_workspace
            ),
            "default_goal": self.default_goal,
            "default_auto_commit": self.default_auto_commit,
            "default_create_pr": self.default_create_pr,
            "default_model": selected["last_model"] or self.manager.settings.model_name,
            "default_base_url": selected["last_base_url"]
            or self.manager.settings.model_base_url,
            "known_servers": selected["servers"],
            "max_concurrent": self.manager.max_concurrent,
            **self.manager.counts(),
        }

    def load_history(self, workspace_root: Path) -> list[dict[str, Any]]:
        """Load cycle logs from `.loco/runs/` and history.json, newest first."""
        runs_dir = Path(workspace_root) / ".loco" / "runs"
        results: list[dict[str, Any]] = []
        
        # Load from .loco/runs/ directory (current runs)
        if runs_dir.exists():
            for path in sorted(runs_dir.glob("*.json"), reverse=True):
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if not isinstance(data, dict):
                    continue
                data.setdefault("id", path.stem)
                if not data.get("created_at"):
                    parsed = _created_at_from_run_id(path.stem)
                    if parsed:
                        data["created_at"] = parsed
                results.append(data)
        
        # Load from history.json (persisted history)
        history_file = Path(workspace_root) / "history.json"
        try:
            if history_file.exists():
                with open(history_file, "r", encoding="utf-8") as f:
                    history = json.load(f)
                if isinstance(history, list):
                    # Add all historical items (the file has them newest first)
                    results.extend(history)
        except (OSError, json.JSONDecodeError):
            # If we can't read the history file, continue with current runs
            pass

        return results

    def load_goals_from_github(
        self,
        workspace: str | Path | None = None,
        state: str = "open",
    ) -> dict[str, Any]:
        """Load issues as selectable goals from the workspace's GitHub remote."""
        root = Path(workspace or self.default_workspace)
        return load_goals_from_workspace(root, state=state)

    def paginate_history(
        self,
        workspace_root: Path,
        *,
        page: int = 1,
        page_size: int = 10,
        search: str | None = None,
    ) -> dict[str, Any]:
        """Load and paginate history with optional search filter."""
        all_items = self.load_history(workspace_root)
        
        # Apply search filter if provided
        if search:
            search_lower = search.lower()
            all_items = [
                item for item in all_items
                if any(
                    str(value).lower().find(search_lower) >= 0
                    for value in [
                        item.get("goal", ""),
                        item.get("summary", ""),
                        item.get("reason", ""),
                        item.get("status", ""),
                        item.get("id", ""),
                        item.get("pr_url", ""),
                        item.get("created_at", ""),
                        " ".join(
                            str(
                                event.get("path")
                                or event.get("url")
                                or event.get("message")
                                or event.get("output")
                                or event.get("command")
                                or ""
                            )
                            for event in item.get("events") or []
                            if isinstance(event, dict)
                        ),
                    ]
                )
            ]
        
        total = len(all_items)
        total_pages = max(1, (total + page_size - 1) // page_size)
        page = max(1, min(page, total_pages))
        
        start_idx = (page - 1) * page_size
        end_idx = start_idx + page_size
        items = all_items[start_idx:end_idx]
        
        return {
            "items": items,
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": total_pages,
        }


def create_app(
    manager: TaskManager,
    *,
    default_workspace: Path,
    default_goal: str | None = None,
    default_create_pr: bool | None = None,
) -> FastAPI:
    app = FastAPI(title="loco", docs_url=None, redoc_url=None)
    app.state.ui = UiState(
        manager,
        default_workspace=default_workspace,
        default_goal=default_goal,
        default_create_pr=default_create_pr,
    )
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        ui: UiState = request.app.state.ui
        return templates.TemplateResponse(request, "index.html", ui.template_vars())

    @app.get("/favicon.ico")
    def favicon() -> Response:
        return Response(FAVICON_SVG, media_type="image/svg+xml")

    @app.get("/api/meta")
    def meta(request: Request) -> dict[str, Any]:
        ui: UiState = request.app.state.ui
        return ui.meta()

    def _models_payload(
        ui: UiState,
        base_url: str | None,
        api_key: str | None,
        *,
        remember: bool = False,
        model: str | None = None,
    ) -> Any:
        try:
            resolved = normalize_model_base_url(
                base_url or ui.manager.settings.model_base_url
            )
            names = ui.manager.list_models(base_url=resolved, api_key=api_key)
        except ValueError as exc:
            return JSONResponse({"error": str(exc), "models": []}, status_code=400)
        if remember:
            servers = ui.remember_server(resolved, model=model)
        else:
            servers = ui.known_servers()
        selected = ui.selection()
        return {
            "default": ui.manager.settings.model_name,
            "last_model": selected["last_model"],
            "base_url": resolved,
            "models": names,
            "servers": servers,
        }

    @app.get("/api/models", response_model=None)
    def models(
        request: Request,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> Any:
        return _models_payload(request.app.state.ui, base_url, api_key)

    @app.post("/api/models", response_model=None)
    def models_post(
        request: Request,
        payload: ModelsQuery | None = None,
    ) -> Any:
        body = payload or ModelsQuery()
        return _models_payload(
            request.app.state.ui,
            body.base_url,
            body.api_key,
            remember=True,
            model=body.model,
        )

    @app.get("/api/servers")
    def servers(request: Request) -> dict[str, Any]:
        ui: UiState = request.app.state.ui
        selected = ui.selection()
        return {
            "default": ui.manager.settings.model_base_url,
            "servers": selected["servers"],
            "last_base_url": selected["last_base_url"],
            "last_model": selected["last_model"],
        }

    @app.get("/api/goals", response_model=None)
    def goals_from_issues(
        request: Request,
        workspace: str | None = None,
        state: str = "open",
    ) -> Any:
        """Get selectable goals from the current workspace's GitHub issues."""
        ui: UiState = request.app.state.ui
        result = ui.load_goals_from_github(workspace, state=state)
        if result.get("error"):
            status = (
                400
                if result["error"] == "workspace is not a GitHub repository"
                else 502
            )
            return JSONResponse(result, status_code=status)
        return result


    @app.get("/api/tasks", response_model=None)
    def list_tasks(
        request: Request,
        page: int | None = None,
        page_size: int | None = None,
    ) -> Any:
        ui: UiState = request.app.state.ui
        tasks = [task.to_dict() for task in ui.manager.list()]
        if page is None and page_size is None:
            return tasks
        size = max(1, min(page_size or 10, 100))
        total = len(tasks)
        total_pages = max(1, math.ceil(total / size) if size else 1)
        current = max(1, min(page or 1, total_pages))
        start = (current - 1) * size
        return {
            "items": tasks[start : start + size],
            "page": current,
            "page_size": size,
            "total": total,
            "total_pages": total_pages,
        }

    @app.get("/api/tasks/{task_id}", response_model=None)
    def get_task(task_id: str, request: Request) -> Any:
        ui: UiState = request.app.state.ui
        task = ui.manager.get(task_id)
        if task is None:
            return JSONResponse({"error": "task not found"}, status_code=404)
        return task.to_dict()

    @app.post("/api/tasks/{task_id}/rerun")
    def rerun_task(task_id: str, request: Request, payload: RerunPayload | None = None) -> Any:
        ui: UiState = request.app.state.ui
        run_from_sha = payload.sha if payload else None
        new_task = ui.manager.rerun(task_id, run_from_sha)
        if new_task is None:
            return JSONResponse({"error": "task not found or not rerunnable"}, status_code=404)
        return JSONResponse(new_task.to_dict(), status_code=201)

    @app.post("/api/tasks")
    def create_task(
        request: Request,
        payload: TaskCreate | None = None,
    ) -> JSONResponse:
        ui: UiState = request.app.state.ui
        body = payload or TaskCreate()
        workspace_raw = body.workspace or ui.default_workspace
        try:
            task = ui.manager.submit(
                Path(workspace_raw),
                body.goal,
                auto_commit=body.auto_commit,
                create_pr=body.create_pr,
                model_name=body.model,
                model_base_url=body.base_url,
                model_api_key=body.api_key,
                resume_branch=body.resume_branch,
                resume_sha=body.resume_sha,
            )
            ui.remember_server(task.model_base_url, model=task.model_name)
            ui.set_workspace(task.workspace)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse(task.to_dict(), status_code=201)

    @app.get("/api/history")
    def get_history(
        request: Request,
        page: int = 1,
        page_size: int = 10,
        search: str | None = None,
    ) -> dict[str, Any]:
        ui: UiState = request.app.state.ui
        return ui.paginate_history(
            Path(ui.default_workspace),
            page=page,
            page_size=page_size,
            search=search,
        )

    @app.get("/api/ui-screenshot", response_model=None)
    def ui_screenshot(
        request: Request,
        name: str,
        workspace: str | None = None,
    ) -> Any:
        ui: UiState = request.app.state.ui
        root = Path(workspace or ui.default_workspace).expanduser()
        path = resolve_ui_screenshot(root, name)
        if path is None:
            return JSONResponse({"error": "screenshot not found"}, status_code=404)
        return FileResponse(path, media_type="image/png")

    @app.post("/api/servers/alias")
    def update_alias(
        request: Request,
        payload: ServerAliasUpdate | None = None,
    ) -> dict[str, Any]:
        """Update or add an alias for a server URL."""
        ui: UiState = request.app.state.ui
        body = payload or ServerAliasUpdate(url="", alias=None)
        if not body.url or not body.url.strip():
            return JSONResponse({"error": "url is required"}, status_code=400)
        servers = update_server_alias(
            Path(ui.default_workspace),
            body.url,
            body.alias,
        )
        return {
            "servers": servers,
            "last_base_url": ui.selection()["last_base_url"],
            "last_model": ui.selection()["last_model"],
        }

    @app.post("/api/servers")
    def create_server(
        request: Request,
        payload: UrlCreate | None = None,
    ) -> dict[str, Any]:
        """Create a new server with optional alias."""
        from agent_loco.runtime.servers import create_or_update_server
        
        ui: UiState = request.app.state.ui
        body = payload or UrlCreate(url="", alias=None)
        if not body.url or not body.url.strip():
            return JSONResponse({"error": "url is required"}, status_code=400)
        
        servers = create_or_update_server(
            Path(ui.default_workspace),
            body.url,
            body.alias or None,
        )
        return {
            "servers": servers,
            "last_base_url": ui.selection()["last_base_url"],
            "last_model": ui.selection()["last_model"],
        }

    def _workspace_payload(ui: UiState) -> dict[str, Any]:
        workspaces = load_workspaces(
            Path(ui.store_root), default=ui.default_workspace
        )
        current = next(
            (item for item in workspaces if item["path"] == ui.default_workspace),
            None,
        )
        return {
            "current": ui.default_workspace,
            "home": str(Path.home()),
            "default_guidelines": default_guidelines(),
            "guidelines": (current or {}).get("guidelines") or default_guidelines(),
            "custom_guidelines": bool((current or {}).get("custom_guidelines")),
            "workspaces": workspaces,
            "archived": load_archived_workspaces(Path(ui.store_root)),
        }

    def _seed_guidelines(path: str, text: str | None) -> None:
        if text is None:
            return
        root = Path(path)
        if guidelines_are_custom(root):
            return
        save_guidelines(root, text)

    @app.get("/api/workspaces")
    def list_workspaces(request: Request) -> dict[str, Any]:
        return _workspace_payload(request.app.state.ui)

    @app.get("/api/workspaces/browse")
    def browse_workspaces(path: str | None = None) -> Any:
        try:
            return browse_directory(path)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.post("/api/workspaces/select")
    def select_workspace(request: Request, payload: WorkspaceSelect) -> Any:
        ui: UiState = request.app.state.ui
        try:
            _seed_guidelines(payload.path, payload.guidelines)
            ui.set_workspace(payload.path)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return _workspace_payload(ui)

    @app.post("/api/workspaces/create")
    def make_workspace(request: Request, payload: WorkspaceSelect) -> Any:
        ui: UiState = request.app.state.ui
        try:
            created = create_workspace(payload.path)
            _seed_guidelines(str(created["path"]), payload.guidelines)
            ui.set_workspace(str(created["path"]))
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return {**_workspace_payload(ui), "created": created}

    @app.post("/api/workspaces/clone")
    def clone_repo(request: Request, payload: WorkspaceClone) -> Any:
        ui: UiState = request.app.state.ui
        parent = payload.parent or str(Path.home() / "projects")
        try:
            cloned = clone_workspace(payload.url, parent, name=payload.name)
            _seed_guidelines(str(cloned["path"]), payload.guidelines)
            ui.set_workspace(str(cloned["path"]))
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return {**_workspace_payload(ui), "created": cloned}

    @app.put("/api/workspaces/guidelines")
    def update_workspace_guidelines(
        request: Request, payload: WorkspaceGuidelines | None = None
    ) -> Any:
        ui: UiState = request.app.state.ui
        body = payload or WorkspaceGuidelines()
        path = body.path or ui.default_workspace
        try:
            save_guidelines(Path(path), body.guidelines)
            ui.set_workspace(path)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return _workspace_payload(ui)

    @app.post("/api/workspaces/archive")
    def archive_workspace_tab(request: Request, payload: WorkspaceSelect) -> Any:
        ui: UiState = request.app.state.ui
        try:
            ui.archive_workspace_path(payload.path)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return _workspace_payload(ui)

    @app.post("/api/workspaces/restore")
    def restore_workspace_tab(request: Request, payload: WorkspaceSelect) -> Any:
        ui: UiState = request.app.state.ui
        try:
            ui.set_workspace(payload.path)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return _workspace_payload(ui)

    @app.post("/api/workspaces/forget")
    def forget_workspace_tab(request: Request, payload: WorkspaceSelect) -> Any:
        ui: UiState = request.app.state.ui
        try:
            ui.forget_workspace_path(payload.path)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return _workspace_payload(ui)

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    return app


def _created_at_from_run_id(stem: str) -> str | None:
    try:
        parsed = datetime.strptime(stem, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None
    return parsed.strftime("%Y-%m-%dT%H:%M:%SZ")


def serve(
    workspace: Path,
    settings: Settings,
    *,
    host: str = "127.0.0.1",
    port: int = 8080,
    max_concurrent: int = 1,
    default_goal: str | None = None,
    default_create_pr: bool | None = None,
) -> None:
    manager = TaskManager(settings, max_concurrent=max_concurrent)
    app = create_app(
        manager,
        default_workspace=workspace,
        default_goal=default_goal,
        default_create_pr=default_create_pr,
    )
    try:
        uvicorn.run(app, host=host, port=port, log_level="info")
    finally:
        manager.shutdown(wait=False)