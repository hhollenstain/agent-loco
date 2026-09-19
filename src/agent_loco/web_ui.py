from __future__ import annotations

from pathlib import Path

from flask import Flask, jsonify, render_template, request

from agent_loco.config import Settings
from agent_loco.runtime.tasks import TaskManager

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"


def create_app(
    manager: TaskManager,
    *,
    default_workspace: Path,
    default_goal: str | None = None,
    default_publish: bool | None = None,
) -> Flask:
    app = Flask(__name__, template_folder=str(TEMPLATE_DIR))
    app.config["TASK_MANAGER"] = manager
    app.config["DEFAULT_WORKSPACE"] = str(default_workspace.expanduser().resolve())
    app.config["DEFAULT_GOAL"] = default_goal or ""
    app.config["DEFAULT_AUTO_COMMIT"] = manager.settings.auto_commit
    app.config["DEFAULT_PUBLISH"] = bool(
        default_publish if default_publish is not None else manager.settings.publish
    )

    @app.get("/")
    def index():
        return render_template(
            "index.html",
            default_workspace=app.config["DEFAULT_WORKSPACE"],
            default_goal=app.config["DEFAULT_GOAL"],
            default_auto_commit=app.config["DEFAULT_AUTO_COMMIT"],
            default_publish=app.config["DEFAULT_PUBLISH"],
            max_concurrent=manager.max_concurrent,
        )

    @app.get("/api/meta")
    def meta():
        counts = manager.counts()
        return jsonify(
            {
                "default_workspace": app.config["DEFAULT_WORKSPACE"],
                "default_goal": app.config["DEFAULT_GOAL"],
                "default_auto_commit": app.config["DEFAULT_AUTO_COMMIT"],
                "default_publish": app.config["DEFAULT_PUBLISH"],
                "max_concurrent": manager.max_concurrent,
                **counts,
            }
        )

    @app.get("/api/tasks")
    def list_tasks():
        return jsonify([task.to_dict() for task in manager.list()])

    @app.get("/api/tasks/<task_id>")
    def get_task(task_id: str):
        task = manager.get(task_id)
        if task is None:
            return jsonify({"error": "task not found"}), 404
        return jsonify(task.to_dict())

    @app.post("/api/tasks")
    def create_task():
        payload = request.get_json(silent=True) or {}
        workspace_raw = payload.get("workspace") or app.config["DEFAULT_WORKSPACE"]
        goal = payload.get("goal")
        auto_commit = payload.get("auto_commit")
        publish = payload.get("publish")
        try:
            task = manager.submit(
                Path(str(workspace_raw)),
                None if goal is None else str(goal),
                auto_commit=None if auto_commit is None else bool(auto_commit),
                publish=None if publish is None else bool(publish),
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(task.to_dict()), 201

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
        app.run(
            host=host,
            port=port,
            debug=False,
            use_reloader=False,
            threaded=True,
        )
    finally:
        manager.shutdown(wait=False)
