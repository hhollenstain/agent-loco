from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tests.support import init_git_repo

from agent_lbw.config import Settings
from agent_lbw.runtime.account import load_account
from agent_lbw.runtime.db import database
from agent_lbw.runtime.improve import CycleResult
from agent_lbw.runtime.store.projects import ensure_project
from agent_lbw.runtime.store.recording import record_task, sync_task
from agent_lbw.runtime.tasks import Task, TaskManager
from agent_lbw.web_ui import create_app


def _ok(task: Task) -> CycleResult:
    return CycleResult(
        status="success",
        goal=task.goal,
        summary="done",
        tests_passed=True,
        committed=False,
        published=False,
        commit_sha=None,
        reason="completed",
    )


def test_a_project_without_a_checkout_stays_selectable(settings: Settings, tmp_path: Path) -> None:
    account = load_account(tmp_path)
    gone = tmp_path / "removed" / "demo"
    with database(tmp_path) as db:
        ensure_project(
            db,
            account["org"]["id"],
            name="demo",
            kind="folder",
            locator=str(gone),
        )
    manager = TaskManager(settings, runner=_ok)
    app = create_app(manager, default_workspace=tmp_path)
    client = TestClient(app)
    try:
        listed = client.get("/api/workspaces")
        assert listed.status_code == 200
        missing = next(item for item in listed.json()["workspaces"] if item["name"] == "demo")
        assert missing["missing"] is True
        assert missing["path"] == str(gone)

        selected = client.post("/api/workspaces/select", json={"path": str(gone)})
        assert selected.status_code == 200
        assert selected.json()["current"] == str(gone)
        assert any(item["name"] == "demo" for item in selected.json()["workspaces"])

        refused = client.post("/api/tasks", json={"workspace": str(gone), "goal": "try again"})
        assert refused.status_code == 400
        assert "restore" in refused.json()["error"]
    finally:
        manager.shutdown(wait=False)


def test_a_missing_github_project_can_start_a_proposal(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    account = load_account(tmp_path)
    gone = tmp_path / "removed" / "demo"
    with database(tmp_path) as db:
        ensure_project(
            db,
            account["org"]["id"],
            name="demo",
            kind="github",
            locator=str(gone),
        )

    def one_repo(_db, _root):
        return [{"full_name": "acme/demo", "private": False}]

    def fake_clone(url, parent, *, name=None, git_config=None):
        dest = Path(parent) / (name or "demo")
        dest.mkdir(parents=True)
        (dest / ".git").mkdir()
        return {"path": str(dest)}

    monkeypatch.setattr("agent_lbw.runtime.store.github_sync.list_repos", one_repo)
    monkeypatch.setattr("agent_lbw.runtime.workspaces.clone_workspace", fake_clone)
    manager = TaskManager(settings, runner=_ok)
    app = create_app(manager, default_workspace=tmp_path)
    client = TestClient(app)
    try:
        created = client.post("/api/tasks", json={"workspace": str(gone), "goal": "ship it"})
        assert created.status_code == 201
        assert created.json()["workspace"] == str((tmp_path / "demo").resolve())
        assert created.json()["goal"] == "ship it"
    finally:
        manager.shutdown(wait=False)


def test_a_finished_job_keeps_its_plan_steps_and_diff(settings: Settings, tmp_path: Path) -> None:
    (tmp_path / "base.txt").write_text("base\n", encoding="utf-8")
    init_git_repo(tmp_path)
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True).strip()
    (tmp_path / "note.txt").write_text("hello from the job\n", encoding="utf-8")
    subprocess.run(["git", "add", "note.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "add note"], cwd=tmp_path, check=True)
    task = Task(
        id="job-kept",
        workspace=str(tmp_path),
        goal="add a note",
        auto_commit=False,
        create_pr=False,
        model_name="scripted",
        model_base_url="http://127.0.0.1:9/v1",
        model_api_key="ollama",
        status="success",
        plan="write note.txt",
        summary="added the note",
        reason="completed",
        tests_passed=True,
        sha_before=sha,
        events=[
            {"kind": "step", "at": "2026-11-04T12:00:00Z", "message": "writing the note"},
            {
                "kind": "test",
                "ok": False,
                "phase": "tests",
                "command": "pytest",
                "output": "SECRET-TEST-OUTPUT",
            },
            {"kind": "llm", "messages": [{"role": "user", "content": "SECRET-PROMPT"}]},
        ],
        logs=["SECRET-LOG-LINE"],
    )
    record_task(tmp_path, "", task)
    sync_task(tmp_path, "", task)

    stored = (tmp_path / ".lbw" / "lbw.db").read_bytes()
    assert b"SECRET-LOG-LINE" not in stored
    assert b"SECRET-TEST-OUTPUT" not in stored
    assert b"SECRET-PROMPT" not in stored
    assert b"hello from the job" not in stored
    assert b"write note.txt" not in stored

    manager = TaskManager(settings, runner=_ok)
    app = create_app(manager, default_workspace=tmp_path)
    client = TestClient(app)
    try:
        history = client.get("/api/history")
        assert history.status_code == 200
        item = next(entry for entry in history.json()["items"] if entry["id"] == "job-kept")
        assert item["plan"] == "write note.txt"
        assert item["summary"] == "added the note"
        assert item["reason"] == "completed"
        assert item["tests_passed"] is True
        assert "logs" not in item
        steps = [event for event in item["events"] if event.get("kind") == "step"]
        assert steps[0]["message"] == "writing the note"
        tests = [event for event in item["events"] if event.get("kind") == "test"]
        assert tests[0]["ok"] is False
        assert "output" not in tests[0]
        files = [event for event in item["events"] if event.get("kind") == "file"]
        assert any(event["path"] == "note.txt" for event in files)
        assert any("hello from the job" in (event.get("net_diff") or "") for event in files)
    finally:
        manager.shutdown(wait=False)


def _wait_for(client: TestClient, task_id: str, status: str) -> dict:
    for _ in range(50):
        response = client.get(f"/api/tasks/{task_id}")
        body = response.json()
        if response.status_code == 200 and body.get("status") == status:
            return body
        time.sleep(0.05)
    raise AssertionError(status)


def _parked_change(settings: Settings, tmp_path: Path) -> str:
    def runner(task: Task) -> CycleResult:
        if task.decision == "build":
            return CycleResult(
                status="success",
                goal=task.goal,
                summary="built",
                tests_passed=True,
                committed=False,
                published=False,
                commit_sha=None,
                reason="completed",
                stage="done",
            )
        return CycleResult(
            status="waiting",
            goal=task.goal,
            summary="Implement: hello",
            tests_passed=None,
            committed=False,
            published=False,
            commit_sha=None,
            reason="waiting for triage",
            stage="triage",
            plan="Implement: hello",
        )

    manager = TaskManager(settings, runner=runner)
    app = create_app(manager, default_workspace=tmp_path)
    client = TestClient(app)
    try:
        created = client.post(
            "/api/tasks",
            json={"workspace": str(tmp_path), "goal": "Say hello", "gate": "ask"},
        )
        assert created.status_code == 201
        task_id = created.json()["id"]
        waiting = _wait_for(client, task_id, "waiting")
        assert waiting["stage"] == "triage"
        assert waiting["plan"] == "Implement: hello"
        return task_id
    finally:
        manager.shutdown(wait=True)


def test_build_starts_after_the_agent_restarts(settings: Settings, tmp_path: Path) -> None:
    task_id = _parked_change(settings, tmp_path)
    started: list[str | None] = []

    def runner(task: Task) -> CycleResult:
        started.append(task.decision)
        return CycleResult(
            status="success",
            goal=task.goal,
            summary="built",
            tests_passed=True,
            committed=False,
            published=False,
            commit_sha=None,
            reason="completed",
            stage="done",
        )

    manager = TaskManager(settings, runner=runner)
    app = create_app(manager, default_workspace=tmp_path)
    client = TestClient(app)
    try:
        listed = client.get("/api/tasks").json()
        restored = next(item for item in listed if item["id"] == task_id)
        assert restored["status"] == "waiting"
        assert restored["stage"] == "triage"
        assert started == []
        with manager._lock:
            manager._tasks.pop(task_id)
        built = client.post(f"/api/tasks/{task_id}/decide", json={"decision": "build"})
        assert built.status_code == 200
        done = _wait_for(client, task_id, "success")
        assert done["stage"] == "done"
        assert started == ["build"]
    finally:
        manager.shutdown(wait=True)


def test_a_queued_plan_waits_for_build_after_restart(settings: Settings, tmp_path: Path) -> None:
    task_id = _parked_change(settings, tmp_path)
    with database(tmp_path) as db:
        db.execute("UPDATE tasks SET status = ? WHERE id = ?", ("queued", task_id))
    started: list[str | None] = []

    def runner(task: Task) -> CycleResult:
        started.append(task.decision)
        return CycleResult(
            status="success",
            goal=task.goal,
            summary="built",
            tests_passed=True,
            committed=False,
            published=False,
            commit_sha=None,
            reason="completed",
            stage="done",
        )

    manager = TaskManager(settings, runner=runner)
    app = create_app(manager, default_workspace=tmp_path)
    client = TestClient(app)
    try:
        time.sleep(0.2)
        assert started == []
        listed = client.get("/api/tasks").json()
        restored = next(item for item in listed if item["id"] == task_id)
        assert restored["status"] == "waiting"
        assert restored["stage"] == "triage"
        history = client.get("/api/history").json()["items"]
        saved = next(item for item in history if item["id"] == task_id)
        assert saved["status"] == "waiting"
        built = client.post(f"/api/tasks/{task_id}/decide", json={"decision": "build"})
        assert built.status_code == 200
        assert _wait_for(client, task_id, "success")["stage"] == "done"
        assert started == ["build"]
    finally:
        manager.shutdown(wait=True)


def test_a_build_choice_resumes_after_restart(settings: Settings, tmp_path: Path) -> None:
    task_id = _parked_change(settings, tmp_path)
    parked = TaskManager(settings, runner=_ok)
    parked.store_root = tmp_path
    task = parked.adopt_job(task_id)
    assert task is not None
    task.decision = "build"
    task.status = "queued"
    sync_task(tmp_path, "", task)
    parked.shutdown(wait=True)

    started: list[str | None] = []

    def runner(task: Task) -> CycleResult:
        started.append(task.decision)
        return _ok(task)

    manager = TaskManager(settings, runner=runner)
    app = create_app(manager, default_workspace=tmp_path)
    client = TestClient(app)
    try:
        done = _wait_for(client, task_id, "success")
        assert done["status"] == "success"
        assert started == ["build"]
    finally:
        manager.shutdown(wait=True)


def test_a_queued_job_runs_again_after_restart(settings: Settings, tmp_path: Path) -> None:
    started: list[str] = []

    def runner(task: Task) -> CycleResult:
        started.append(task.id)
        return _ok(task)

    waiting = Task(
        id="queued-job",
        workspace=str(tmp_path),
        goal="Say hello",
        auto_commit=False,
        create_pr=False,
        model_name="scripted",
        model_base_url="http://127.0.0.1:9/v1",
        model_api_key="ollama",
    )
    record_task(tmp_path, "", waiting)
    manager = TaskManager(settings, runner=runner)
    app = create_app(manager, default_workspace=tmp_path)
    client = TestClient(app)
    try:
        assert _wait_for(client, "queued-job", "success")["summary"] == "done"
        assert started == ["queued-job"]
    finally:
        manager.shutdown(wait=True)
