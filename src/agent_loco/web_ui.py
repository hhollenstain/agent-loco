from __future__ import annotations

from pathlib import Path
from typing import Any

import json
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from agent_loco.config import Settings
from agent_loco.runtime.tasks import TaskManager
from agent_loco.runtime.improve import CycleResult

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))


class TaskCreate(BaseModel):
    workspace: str | None = None
    goal: str | None = None
    model: str | None = None
    auto_commit: bool | None = None
    publish: bool | None = None


class UiState:
    def __init__(
        self,
        manager: TaskManager,
        *,
        default_workspace: Path,
        default_goal: str | None,
        default_publish: bool | None,
    ) -> None:
        self.manager = manager
        self.default_workspace = str(default_workspace.expanduser().resolve())
        self.default_goal = default_goal or ""
        self.default_auto_commit = manager.settings.auto_commit
        self.default_publish = bool(
            default_publish if default_publish is not None else manager.settings.publish
        )

    def template_vars(self) -> dict[str, Any]:
        return {
            "default_workspace": self.default_workspace,
            "default_goal": self.default_goal,
            "default_auto_commit": self.default_auto_commit,
            "default_publish": self.default_publish,
            "default_model": self.manager.settings.model_name,
            "max_concurrent": self.manager.max_concurrent,
        }

    def meta(self) -> dict[str, Any]:
        return {
            "default_workspace": self.default_workspace,
            "default_goal": self.default_goal,
            "default_auto_commit": self.default_auto_commit,
            "default_publish": self.default_publish,
            "default_model": self.manager.settings.model_name,
            "max_concurrent": self.manager.max_concurrent,
            **self.manager.counts(),
        }

    def load_history(self, workspace_root: Path) -> list[dict[str, Any]]:
        """Load history runs from .loco/runs/ directory."""
        runs_dir = Path(workspace_root) / ".loco" / "runs"
        if not runs_dir.exists():
            return []
        files = sorted(runs_dir.glob("*.json"))
        results = []
        for f in files:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                results.append(data)
            except json.JSONDecodeError:
                pass
        # Sort by timestamp (completed_at field in CycleResult)
        results.sort(
            key=lambda r: r.get("finished_at", ""),
            reverse=True,
        )
        return results


def create_app(
    manager: TaskManager,
    *,
    default_workspace: Path,
    default_goal: str | None = None,
    default_publish: bool | None = None,
) -> FastAPI:
    app = FastAPI(title="loco", docs_url=None, redoc_url=None)
    app.state.ui = UiState(
        manager,
        default_workspace=default_workspace,
        default_goal=default_goal,
        default_publish=default_publish,
    )

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        ui: UiState = request.app.state.ui
        return templates.TemplateResponse(request, "index.html", ui.template_vars())

    @app.get("/api/meta")
    def meta(request: Request) -> dict[str, Any]:
        ui: UiState = request.app.state.ui
        return ui.meta()

    @app.get("/api/models")
    def models(request: Request) -> dict[str, Any]:
        ui: UiState = request.app.state.ui
        return {
            "default": ui.manager.settings.model_name,
            "models": ui.manager.list_models(),
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
                publish=body.publish,
                model_name=body.model,
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse(task.to_dict(), status_code=201)

    @app.get("/api/history")
    def get_request_id(request: Request) -> list[dict[str, Any]]:
        ui: UiState = request.app.state.ui
        workspace_root = Path(ui.default_workspace)
        return ui.load_history(workspace_root)

    return app


def serve(
    workspace: Path,
    settings: Settings,
    *,
    host: str = "127.0.0.1",
    port: int = 8080,
    max_concurrent: int = 1,
    default_goal: str | None = None,
    default_publish: bool | None = None,
) -> None:
    manager = TaskManager(settings, max_concurrent=max_concurrent)
    app = create_app(
        manager,
        default_workspace=workspace,
        default_goal=default_goal,
        default_publish=default_publish,
    )
    try:
        uvicorn.run(app, host=host, port=port, log_level="info")
    finally:
        manager.shutdown(wait=False)
