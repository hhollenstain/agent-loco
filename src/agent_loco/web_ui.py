from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from agent_loco.config import Settings
from agent_loco.llm.client import normalize_model_base_url
from agent_loco.runtime.servers import list_known_servers, remember_server
from agent_loco.runtime.tasks import TaskManager

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))


class TaskCreate(BaseModel):
    workspace: str | None = None
    goal: str | None = None
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    auto_commit: bool | None = None
    create_pr: bool | None = None


class ModelsQuery(BaseModel):
    base_url: str | None = None
    api_key: str | None = None


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
        self.default_workspace = str(default_workspace.expanduser().resolve())
        self.default_goal = default_goal or ""
        self.default_auto_commit = manager.settings.auto_commit
        self.default_create_pr = bool(
            default_create_pr if default_create_pr is not None else manager.settings.create_pr
        )

    def known_servers(self) -> list[str]:
        return list_known_servers(
            Path(self.default_workspace),
            default=self.manager.settings.model_base_url,
        )

    def remember_server(self, url: str) -> list[str]:
        return remember_server(
            Path(self.default_workspace),
            url,
            default=self.manager.settings.model_base_url,
        )

    def template_vars(self) -> dict[str, Any]:
        return {
            "default_workspace": self.default_workspace,
            "default_goal": self.default_goal,
            "default_auto_commit": self.default_auto_commit,
            "default_create_pr": self.default_create_pr,
            "default_model": self.manager.settings.model_name,
            "default_base_url": self.manager.settings.model_base_url,
            "known_servers": self.known_servers(),
            "max_concurrent": self.manager.max_concurrent,
        }

    def meta(self) -> dict[str, Any]:
        return {
            "default_workspace": self.default_workspace,
            "default_goal": self.default_goal,
            "default_auto_commit": self.default_auto_commit,
            "default_create_pr": self.default_create_pr,
            "default_model": self.manager.settings.model_name,
            "default_base_url": self.manager.settings.model_base_url,
            "known_servers": self.known_servers(),
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

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        ui: UiState = request.app.state.ui
        return templates.TemplateResponse(request, "index.html", ui.template_vars())

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
    ) -> Any:
        try:
            resolved = normalize_model_base_url(
                base_url or ui.manager.settings.model_base_url
            )
            names = ui.manager.list_models(base_url=resolved, api_key=api_key)
        except ValueError as exc:
            return JSONResponse({"error": str(exc), "models": []}, status_code=400)
        servers = ui.remember_server(resolved) if remember else ui.known_servers()
        return {
            "default": ui.manager.settings.model_name,
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
        )

    @app.get("/api/servers")
    def servers(request: Request) -> dict[str, Any]:
        ui: UiState = request.app.state.ui
        return {
            "default": ui.manager.settings.model_base_url,
            "servers": ui.known_servers(),
        }

    @app.get("/api/tasks")
    def list_tasks(request: Request) -> list[dict[str, Any]]:
        ui: UiState = request.app.state.ui
        return [task.to_dict() for task in ui.manager.list()]

    @app.get("/api/tasks/{task_id}", response_model=None)
    def get_task(task_id: str, request: Request) -> Any:
        ui: UiState = request.app.state.ui
        task = ui.manager.get(task_id)
        if task is None:
            return JSONResponse({"error": "task not found"}, status_code=404)
        return task.to_dict()

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
            )
            ui.remember_server(task.model_base_url)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse(task.to_dict(), status_code=201)

    @app.get("/api/history")
    def get_history(request: Request) -> list[dict[str, Any]]:
        ui: UiState = request.app.state.ui
        return ui.load_history(Path(ui.default_workspace))

    return app


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