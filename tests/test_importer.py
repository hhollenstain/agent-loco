from __future__ import annotations

from pathlib import Path

from tests.support import init_git_repo

from agent_loco.runtime.importer import (
    github_repo_for_workspace,
    load_goals_from_issues,
    load_goals_from_workspace,
)
from agent_loco.sandbox import Workspace
from agent_loco.tools.git import run_git


def test_github_repo_for_workspace_reads_origin(tmp_path: Path) -> None:
    (tmp_path / "readme.txt").write_text("hello\n", encoding="utf-8")
    init_git_repo(tmp_path)
    assert github_repo_for_workspace(tmp_path) is None
    run_git(
        Workspace(tmp_path),
        ["remote", "add", "origin", "https://github.com/acme/demo.git"],
    )
    assert github_repo_for_workspace(tmp_path) == ("acme", "demo")


def test_load_goals_from_issues_skips_pull_requests(monkeypatch) -> None:
    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> list[dict]:
            return [
                {
                    "id": 1,
                    "number": 9,
                    "title": "Ship it",
                    "body": "## Why\nDo the thing",
                    "html_url": "https://github.com/acme/demo/issues/9",
                    "state": "open",
                },
                {
                    "id": 2,
                    "number": 10,
                    "title": "A PR",
                    "body": "",
                    "html_url": "https://github.com/acme/demo/pull/10",
                    "state": "open",
                    "pull_request": {"url": "https://api.github.com/repos/acme/demo/pulls/10"},
                },
            ]

    monkeypatch.setattr(
        "agent_loco.runtime.importer.httpx.get",
        lambda *args, **kwargs: FakeResponse(),
    )
    result = load_goals_from_issues("acme", "demo")
    assert "error" not in result
    assert [issue["number"] for issue in result["issues"]] == [9]
    assert result["issues"][0]["goal"] == "#9 Ship it"
    assert result["issues"][0]["body"] == "Do the thing"


def test_load_goals_from_workspace_requires_github_remote(tmp_path: Path) -> None:
    result = load_goals_from_workspace(tmp_path)
    assert result["issues"] == []
    assert result["error"] == "workspace is not a GitHub repository"
