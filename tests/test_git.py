from __future__ import annotations

from pathlib import Path

from tests.support import init_git_repo

from agent_loco.sandbox import Workspace
from agent_loco.tools.git import commit_changes, has_changes


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
