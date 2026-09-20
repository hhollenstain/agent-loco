from __future__ import annotations

from pathlib import Path

from tests.support import init_git_repo

from agent_loco.sandbox import Workspace
from agent_loco.tools.git import commit_changes, has_changes, is_runtime_artifact, run_git


def test_run_log_paths_are_runtime_artifacts() -> None:
    assert is_runtime_artifact(".loco/runs/cycle.json")
    assert is_runtime_artifact(".loco/runs")
    assert is_runtime_artifact(".loco/servers.json")
    assert not is_runtime_artifact("src/agent_loco/cli.py")
    assert not is_runtime_artifact("loco/runs/cycle.json")


def test_commit_happy_path(tmp_path: Path) -> None:
    (tmp_path / "readme.txt").write_text("hello\n", encoding="utf-8")
    init_git_repo(tmp_path)
    workspace = Workspace(tmp_path)
    (tmp_path / "readme.txt").write_text("hello world\n", encoding="utf-8")
    assert has_changes(workspace)
    result = commit_changes(workspace, "update readme")
    assert result.ok
    assert "committed" in result.output
    assert not has_changes(workspace)


def test_commit_rejects_env_file(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("print('ok')\n", encoding="utf-8")
    init_git_repo(tmp_path)
    workspace = Workspace(tmp_path)
    (tmp_path / ".env").write_text("SECRET=1\n", encoding="utf-8")
    result = commit_changes(workspace, "add env")
    assert not result.ok
    assert "secrets" in result.output


def test_commit_skips_run_logs(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("print('ok')\n", encoding="utf-8")
    init_git_repo(tmp_path)
    workspace = Workspace(tmp_path)
    runs = tmp_path / ".loco" / "runs"
    runs.mkdir(parents=True)
    (runs / "cycle.json").write_text('{"status": "success"}\n', encoding="utf-8")
    (tmp_path / "app.py").write_text("print('changed')\n", encoding="utf-8")
    assert has_changes(workspace)
    result = commit_changes(workspace, "update app")
    assert result.ok
    tracked = run_git(workspace, ["ls-files", ".loco/runs"])
    assert tracked.stdout.strip() == ""
    assert (runs / "cycle.json").exists()


def test_run_logs_alone_are_not_changes(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("print('ok')\n", encoding="utf-8")
    init_git_repo(tmp_path)
    workspace = Workspace(tmp_path)
    runs = tmp_path / ".loco" / "runs"
    runs.mkdir(parents=True)
    (runs / "cycle.json").write_text("{}\n", encoding="utf-8")
    assert not has_changes(workspace)
    result = commit_changes(workspace, "should not commit logs")
    assert not result.ok

