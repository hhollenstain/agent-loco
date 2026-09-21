from __future__ import annotations

from pathlib import Path

from tests.support import init_git_repo

from agent_loco.runtime.importer import (
    format_issue_goal,
    github_repo_for_workspace,
    goal_headline,
    load_goals_from_issues,
    load_goals_from_workspace,
    parse_issue_ref,
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


def test_parse_issue_ref_accepts_urls_and_numbers() -> None:
    url = parse_issue_ref("https://github.com/acme/demo/issues/9")
    assert url is not None
    assert url.owner == "acme"
    assert url.repo == "demo"
    assert url.number == 9
    bare = parse_issue_ref("#12")
    assert bare is not None
    assert bare.number == 12
    assert bare.owner is None


def test_format_issue_goal_keeps_markdown_and_comments() -> None:
    goal = format_issue_goal(
        number=9,
        title="Ship it",
        url="https://github.com/acme/demo/issues/9",
        body="## Why\nDo the thing",
        comments=[{"user": {"login": "alice"}, "body": "## Note\nUse SVG"}],
    )
    assert goal.startswith("#9 Ship it\n")
    assert "https://github.com/acme/demo/issues/9" in goal
    assert "Implement this GitHub issue" in goal
    assert "## Why\nDo the thing" in goal
    assert "@alice:\n## Note\nUse SVG" in goal
    assert goal_headline(goal) == "#9 Ship it"


def test_load_goals_from_issues_includes_body_and_comments(monkeypatch) -> None:
    class FakeResponse:
        def __init__(self, url: str) -> None:
            self.url = url
            self.status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> list[dict]:
            if self.url.endswith("/comments"):
                return [
                    {
                        "user": {"login": "alice"},
                        "body": "## Note\nUse SVG",
                    }
                ]
            return [
                {
                    "id": 1,
                    "number": 9,
                    "title": "Ship it",
                    "body": "## Why\nDo the thing",
                    "html_url": "https://github.com/acme/demo/issues/9",
                    "state": "open",
                    "comments": 1,
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
        lambda url, *args, **kwargs: FakeResponse(url),
    )
    result = load_goals_from_issues("acme", "demo")
    assert "error" not in result
    assert [issue["number"] for issue in result["issues"]] == [9]
    issue = result["issues"][0]
    assert issue["label"] == "#9 Ship it"
    assert issue["body"] == "## Why\nDo the thing"
    assert issue["goal"].startswith("#9 Ship it\n")
    assert "## Why\nDo the thing" in issue["goal"]
    assert "@alice:\n## Note\nUse SVG" in issue["goal"]
    assert "Implement this GitHub issue" in issue["goal"]


def test_load_goals_from_workspace_requires_github_remote(tmp_path: Path) -> None:
    result = load_goals_from_workspace(tmp_path)
    assert result["issues"] == []
    assert result["error"] == "workspace is not a GitHub repository"
