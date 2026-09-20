from __future__ import annotations

from pathlib import Path

from tests.support import init_git_repo

from agent_loco.sandbox import Workspace
from agent_loco.tools.git import (
    agent_commit,
    commit_changes,
    current_branch,
    current_sha,
    ensure_pr_branch,
    has_changes,
    is_runtime_artifact,
    push_changes,
    run_git,
    upstream_state,
)


def test_run_log_paths_are_runtime_artifacts() -> None:
    assert is_runtime_artifact(".loco/runs/cycle.json")
    assert is_runtime_artifact(".loco/runs")
    assert is_runtime_artifact(".loco/servers.json")
    assert is_runtime_artifact(".loco/workspaces.json")
    assert is_runtime_artifact("history.json")
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


def test_commit_skips_history_json(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("print('ok')\n", encoding="utf-8")
    init_git_repo(tmp_path)
    workspace = Workspace(tmp_path)
    (tmp_path / "history.json").write_text("[]\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("print('changed')\n", encoding="utf-8")
    assert has_changes(workspace)
    result = commit_changes(workspace, "update app")
    assert result.ok
    tracked = run_git(workspace, ["ls-files", "history.json"])
    assert tracked.stdout.strip() == ""
    assert (tmp_path / "history.json").exists()


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


def test_agent_commit_refuses_protected_branch(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("print('ok')\n", encoding="utf-8")
    init_git_repo(tmp_path)
    workspace = Workspace(tmp_path)
    (tmp_path / "app.py").write_text("print('changed')\n", encoding="utf-8")
    result = agent_commit(workspace, "update app")
    assert result.ok is False
    assert "refusing to commit" in result.output
    assert has_changes(workspace)
    assert current_branch(workspace) in {"main", "master"}


def test_agent_commit_allows_feature_branch(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("print('ok')\n", encoding="utf-8")
    init_git_repo(tmp_path)
    workspace = Workspace(tmp_path)
    run_git(workspace, ["checkout", "-b", "loco/feature"])
    (tmp_path / "app.py").write_text("print('changed')\n", encoding="utf-8")
    result = agent_commit(workspace, "update app")
    assert result.ok
    assert "committed" in result.output
    assert not has_changes(workspace)


def test_cycle_commit_helper_still_works_on_protected_branch(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("print('ok')\n", encoding="utf-8")
    init_git_repo(tmp_path)
    workspace = Workspace(tmp_path)
    (tmp_path / "app.py").write_text("print('changed')\n", encoding="utf-8")
    result = commit_changes(workspace, "update app")
    assert result.ok
    assert current_branch(workspace) in {"main", "master"}


def test_push_refuses_protected_branches(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("print('ok')\n", encoding="utf-8")
    init_git_repo(tmp_path)
    workspace = Workspace(tmp_path)
    for name in ("main", "master"):
        result = push_changes(workspace, "origin", name)
        assert result.ok is False
        assert "refusing" in result.output


def test_ensure_pr_branch_moves_commits_off_main(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("one\n", encoding="utf-8")
    init_git_repo(tmp_path)
    workspace = Workspace(tmp_path)
    protected = current_branch(workspace)
    sha_before = current_sha(workspace)
    (tmp_path / "app.py").write_text("two\n", encoding="utf-8")
    committed = commit_changes(workspace, "change app")
    assert committed.ok
    after = current_sha(workspace)
    result = ensure_pr_branch(workspace, "Improve the adder", sha_before)
    assert result.ok
    assert result.output.startswith("loco/")
    assert current_branch(workspace) == result.output
    assert current_sha(workspace) == after
    assert run_git(workspace, ["rev-parse", protected or "HEAD"]).stdout.strip() == sha_before


def test_upstream_state_without_remote_is_not_pushed(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("print('ok')\n", encoding="utf-8")
    init_git_repo(tmp_path)
    state = upstream_state(Workspace(tmp_path))
    assert state.pushed is False
    assert state.tracking is None
    assert "not pushed" in state.detail


def test_upstream_state_matches_tracking_ref(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("print('ok')\n", encoding="utf-8")
    init_git_repo(tmp_path)
    workspace = Workspace(tmp_path)
    branch = current_branch(workspace)
    sha = current_sha(workspace)
    run_git(workspace, ["update-ref", f"refs/remotes/origin/{branch}", sha or ""])
    run_git(workspace, ["branch", f"--set-upstream-to=origin/{branch}"])
    state = upstream_state(workspace)
    assert state.pushed is True
    assert state.ahead == 0
    assert "pushed" in state.detail


def test_upstream_state_detects_unpushed_local_commits(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("print('ok')\n", encoding="utf-8")
    init_git_repo(tmp_path)
    workspace = Workspace(tmp_path)
    branch = current_branch(workspace)
    sha = current_sha(workspace)
    run_git(workspace, ["update-ref", f"refs/remotes/origin/{branch}", sha or ""])
    run_git(workspace, ["branch", f"--set-upstream-to=origin/{branch}"])
    (tmp_path / "app.py").write_text("print('next')\n", encoding="utf-8")
    commit_changes(workspace, "local only")
    state = upstream_state(workspace)
    assert state.pushed is False
    assert state.ahead == 1
    assert "not pushed" in state.detail

