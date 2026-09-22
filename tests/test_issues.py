from __future__ import annotations

from pathlib import Path

import pytest
from tests.support import init_git_repo

from agent_loco.sandbox import Workspace
from agent_loco.tools.git import run_git
from agent_loco.tools.issues import issues_tools


def _issues_tool(workspace: Workspace, name: str):
    return next(tool for tool in issues_tools(workspace) if tool.name == name)


def test_issue_tools_exist(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path)
    tools = issues_tools(workspace)
    tool_names = [tool.name for tool in tools]
    assert "list_issues" in tool_names
    assert "get_issue" in tool_names
    assert "parse_issue_url" in tool_names
    assert "list_goals_from_issues" in tool_names


def test_parse_issue_url(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path)
    result = _issues_tool(workspace, "parse_issue_url").handler(
        url="https://github.com/octocat/Spoon-Knife/issues/123"
    )
    assert result.ok
    assert "123" in result.output
    assert "octocat" in result.output
    assert "Spoon-Knife" in result.output


def test_get_issue_returns_body_headings_and_comments(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "readme.txt").write_text("hello\n", encoding="utf-8")
    init_git_repo(tmp_path)
    run_git(
        Workspace(tmp_path),
        ["remote", "add", "origin", "https://github.com/acme/demo.git"],
    )

    class FakeResponse:
        def __init__(self, url: str) -> None:
            self.url = url
            self.status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self):
            if self.url.endswith("/comments"):
                return [{"user": {"login": "alice"}, "body": "## Note\nUse SVG"}]
            return {
                "number": 9,
                "title": "Ship it",
                "body": "## Why\nDo the thing",
                "html_url": "https://github.com/acme/demo/issues/9",
                "state": "open",
                "comments": 1,
            }

    monkeypatch.setattr(
        "httpx.get",
        lambda url, *args, **kwargs: FakeResponse(url),
    )
    result = _issues_tool(Workspace(tmp_path), "get_issue").handler(
        issue_ref="9",
        include_comments=True,
    )
    assert result.ok
    assert result.output.startswith("Status: open")
    assert "#9 Ship it" in result.output
    assert "## Why\nDo the thing" in result.output
    assert "@alice:\n## Note\nUse SVG" in result.output
    assert "Implement this GitHub issue" in result.output
