from __future__ import annotations

import threading
from pathlib import Path

from fastapi.testclient import TestClient

from agent_loco.config import Settings
from agent_loco.llm.client import model_ids_from_payload, normalize_model_base_url
from agent_loco.runtime.improve import CycleResult
from agent_loco.runtime.tasks import Task, TaskManager
from agent_loco.web_ui import create_app


def _ok_result(goal: str | None = "do the thing") -> CycleResult:
    return CycleResult(
        status="success",
        goal=goal,
        summary="done",
        tests_passed=True,
        committed=False,
        published=False,
        commit_sha=None,
        reason="completed",
    )


def test_submit_rejects_missing_workspace(settings: Settings, tmp_path: Path) -> None:
    manager = TaskManager(settings, runner=lambda task: _ok_result())
    try:
        try:
            manager.submit(tmp_path / "missing", "goal")
        except ValueError as exc:
            assert "not a directory" in str(exc)
        else:
            raise AssertionError("expected ValueError")
    finally:
        manager.shutdown(wait=False)


def test_queue_runs_one_at_a_time(settings: Settings, tmp_path: Path) -> None:
    started = threading.Event()
    release = threading.Event()
    running_seen = []

    def runner(task: Task) -> CycleResult:
        running_seen.append(task.id)
        started.set()
        assert release.wait(timeout=2)
        return _ok_result(task.goal)

    manager = TaskManager(settings, max_concurrent=1, runner=runner)
    try:
        first = manager.submit(tmp_path, "one")
        second = manager.submit(tmp_path, "two")
        assert started.wait(timeout=2)
        assert manager.get(first.id).status == "running"
        assert manager.get(second.id).status == "queued"
        release.set()
        manager.shutdown(wait=True, cancel_futures=False)
        assert manager.get(first.id).status == "success"
        assert manager.get(second.id).status == "success"
        assert running_seen == [first.id, second.id]
    finally:
        release.set()
        manager.shutdown(wait=False)


def test_web_ui_queues_and_lists_tasks(settings: Settings, tmp_path: Path) -> None:
    gate = threading.Event()

    def runner(task: Task) -> CycleResult:
        gate.wait(timeout=2)
        return _ok_result(task.goal)

    manager = TaskManager(settings, runner=runner)
    app = create_app(manager, default_workspace=tmp_path)
    client = TestClient(app)
    try:
        home = client.get("/")
        assert home.status_code == 200
        assert home.content.strip().startswith(b"<!DOCTYPE html>")
        assert b"</html>" in home.content
        assert b"loco task runner" in home.content
        assert b"\n  10|" not in home.content

        created = client.post(
            "/api/tasks",
            json={"workspace": str(tmp_path), "goal": "Improve the UI", "auto_commit": False},
        )
        assert created.status_code == 201
        task_id = created.json()["id"]

        listed = client.get("/api/tasks")
        assert listed.status_code == 200
        body = listed.json()
        assert len(body) == 1
        assert body[0]["id"] == task_id
        assert body[0]["goal"] == "Improve the UI"
        assert body[0]["model"] == settings.model_name
        assert body[0]["base_url"] == settings.model_base_url
        assert body[0]["status"] in {"queued", "running"}

        missing = client.get("/api/tasks/nope")
        assert missing.status_code == 404
        assert missing.json()["error"] == "task not found"

        bad = client.post("/api/tasks", json={"workspace": str(tmp_path / "nope")})
        assert bad.status_code == 400
        assert "error" in bad.json()
    finally:
        gate.set()
        manager.shutdown(wait=False)


def test_submit_uses_requested_model(settings: Settings, tmp_path: Path) -> None:
    manager = TaskManager(settings, runner=lambda task: _ok_result(task.goal))
    try:
        task = manager.submit(
            tmp_path,
            "goal",
            model_name="other-coder",
            model_base_url="10.0.0.8:8000",
        )
        assert task.model_name == "other-coder"
        assert task.model_base_url == "http://10.0.0.8:8000/v1"
        fallback = manager.submit(tmp_path, "goal")
        assert fallback.model_name == settings.model_name
        assert fallback.model_base_url == settings.model_base_url
    finally:
        manager.shutdown(wait=False)


def test_web_ui_lists_models_and_queues_with_selection(
    settings: Settings, tmp_path: Path
) -> None:
    gate = threading.Event()

    def runner(task: Task) -> CycleResult:
        gate.wait(timeout=2)
        return _ok_result(task.goal)

    manager = TaskManager(
        settings,
        runner=runner,
        models_fn=lambda: ["alpha-coder", "beta-coder"],
    )
    app = create_app(manager, default_workspace=tmp_path)
    client = TestClient(app)
    try:
        home = client.get("/")
        assert home.status_code == 200
        assert b'id="model"' in home.content
        assert b'id="base-url"' in home.content
        assert b"Load models" in home.content

        models = client.get("/api/models")
        assert models.status_code == 200
        body = models.json()
        assert body["default"] == settings.model_name
        assert body["models"][0] == settings.model_name
        assert "alpha-coder" in body["models"]
        assert "beta-coder" in body["models"]

        created = client.post(
            "/api/tasks",
            json={
                "workspace": str(tmp_path),
                "goal": "Use the other model",
                "model": "beta-coder",
                "auto_commit": False,
            },
        )
        assert created.status_code == 201
        assert created.json()["model"] == "beta-coder"
        assert created.json()["base_url"] == settings.model_base_url
    finally:
        gate.set()
        manager.shutdown(wait=False)


def test_web_ui_lists_models_from_requested_host(
    settings: Settings, tmp_path: Path
) -> None:
    gate = threading.Event()

    def runner(task: Task) -> CycleResult:
        gate.wait(timeout=2)
        return _ok_result(task.goal)

    def models_fn(url: str, key: str) -> list[str]:
        if "10.0.0.8" in url:
            return ["remote-coder"]
        return ["alpha-coder", "beta-coder"]

    manager = TaskManager(settings, runner=runner, models_fn=models_fn)
    app = create_app(manager, default_workspace=tmp_path)
    client = TestClient(app)
    try:
        remote = client.get("/api/models", params={"base_url": "10.0.0.8:8000"})
        assert remote.status_code == 200
        body = remote.json()
        assert body["base_url"] == "http://10.0.0.8:8000/v1"
        assert body["models"] == ["remote-coder"]
        assert settings.model_name not in body["models"]

        created = client.post(
            "/api/tasks",
            json={
                "workspace": str(tmp_path),
                "goal": "Use the remote host",
                "model": "remote-coder",
                "base_url": "10.0.0.8:8000",
                "auto_commit": False,
            },
        )
        assert created.status_code == 201
        payload = created.json()
        assert payload["model"] == "remote-coder"
        assert payload["base_url"] == "http://10.0.0.8:8000/v1"
    finally:
        gate.set()
        manager.shutdown(wait=False)


def test_list_models_skips_local_default_for_other_host(settings: Settings) -> None:
    manager = TaskManager(
        settings,
        runner=lambda task: _ok_result(task.goal),
        models_fn=lambda url, key: ["remote-a"],
    )
    try:
        remote = manager.list_models(base_url="http://10.0.0.5:11434")
        assert remote == ["remote-a"]
        local = manager.list_models()
        assert local[0] == settings.model_name
        assert "remote-a" in local
    finally:
        manager.shutdown(wait=False)


def test_history_endpoint_reads_run_logs(settings: Settings, tmp_path: Path) -> None:
    runs = tmp_path / ".loco" / "runs"
    runs.mkdir(parents=True)
    (runs / "20260919T180000Z.json").write_text(
        '{"status": "success", "goal": "Past goal", "summary": "done"}\n',
        encoding="utf-8",
    )
    manager = TaskManager(settings, runner=lambda task: _ok_result(task.goal))
    app = create_app(manager, default_workspace=tmp_path)
    client = TestClient(app)
    try:
        history = client.get("/api/history")
        assert history.status_code == 200
        body = history.json()
        assert len(body) == 1
        assert body[0]["id"] == "20260919T180000Z"
        assert body[0]["goal"] == "Past goal"
    finally:
        manager.shutdown(wait=False)


def test_model_ids_from_openai_payload() -> None:
    names = model_ids_from_payload(
        {"data": [{"id": "qwen2.5-coder:14b"}, {"id": "llama3.1"}, {"id": "llama3.1"}]}
    )
    assert names == ["qwen2.5-coder:14b", "llama3.1"]


def test_normalize_model_base_url() -> None:
    assert (
        normalize_model_base_url("http://127.0.0.1:11434/v1")
        == "http://127.0.0.1:11434/v1"
    )
    assert (
        normalize_model_base_url("http://10.0.0.5:11434") == "http://10.0.0.5:11434/v1"
    )
    assert normalize_model_base_url("10.0.0.5:8000") == "http://10.0.0.5:8000/v1"
    assert (
        normalize_model_base_url("https://llm.example.com/v1/")
        == "https://llm.example.com/v1"
    )
    try:
        normalize_model_base_url("  ")
    except ValueError as exc:
        assert "required" in str(exc)
    else:
        raise AssertionError("expected ValueError")
