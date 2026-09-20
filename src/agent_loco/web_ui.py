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
            "default_base_url": self.manager.settings.model_base_url,
            "max_concurrent": self.manager.max_concurrent,
        }

    def meta(self) -> dict[str, Any]:
        return {
            "default_workspace": self.default_workspace,
            "default_goal": self.default_goal,
            "default_auto_commit": self.default_auto_commit,
            "default_publish": self.default_publish,
            "default_model": self.manager.settings.model_name,
            "default_base_url": self.manager.settings.model_base_url,
            "max_concurrent": self.manager.max_concurrent,
            **self.manager.counts(),
        }

    def load_history(self, workspace_root: Path) -> list[dict[str, Any]]:
        """Load cycle logs from `.loco/runs/` newest first."""
        runs_dir = Path(workspace_root) / ".loco" / "runs"
        if not runs_dir.exists():
            return []
        results: list[dict[str, Any]] = []
        for path in sorted(runs_dir.glob("*.json"), reverse=True):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(data, dict):
                continue
            data.setdefault("id", path.stem)
            results.append(data)
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

    @app.get("/api/models", response_model=None)
    def models(
        request: Request,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> Any:
        ui: UiState = request.app.state.ui
        try:
            resolved = normalize_model_base_url(
                base_url or ui.manager.settings.model_base_url
            )
            names = ui.manager.list_models(base_url=resolved, api_key=api_key)
        except ValueError as exc:
            return JSONResponse({"error": str(exc), "models": []}, status_code=400)
        return {
            "default": ui.manager.settings.model_name,
            "base_url": resolved,
            "models": names,
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
                model_base_url=body.base_url,
                model_api_key=body.api_key,
            )
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
