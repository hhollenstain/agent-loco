from __future__ import annotations

from pathlib import Path

from agent_loco.sandbox import Workspace
from agent_loco.tools.files import file_tools


def _tool(workspace: Workspace, name: str):
    return next(tool for tool in file_tools(workspace) if tool.name == name)


def test_write_and_read_roundtrip(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path)
    written = _tool(workspace, "write_file").handler(path="hello.txt", content="hi\n")
    assert written.ok
    listed = _tool(workspace, "list_dir").handler(path=".")
    assert "hello.txt" in listed.output
    read = _tool(workspace, "read_file").handler(path="hello.txt")
    assert read.ok
    assert "hi" in read.output


def test_write_file_records_create_and_update_diffs(tmp_path: Path) -> None:
    from agent_loco.progress import bind_progress, current_events, reset_progress

    workspace = Workspace(tmp_path)
    token = bind_progress()
    try:
        created = _tool(workspace, "write_file").handler(path="note.txt", content="alpha\n")
        assert created.ok
        updated = _tool(workspace, "write_file").handler(path="note.txt", content="beta\n")
        assert updated.ok
        events = current_events()
    finally:
        reset_progress(token)
    assert [event["action"] for event in events] == ["created", "updated"]
    assert events[0]["path"] == "note.txt"
    assert events[0]["diff"].startswith("--- /dev/null")
    assert "+alpha" in events[0]["diff"]
    assert "--- a/note.txt" in events[1]["diff"]
    assert "-alpha" in events[1]["diff"]
    assert "+beta" in events[1]["diff"]
    assert events[1]["net_action"] == "created"
    assert events[1]["net_diff"].startswith("--- /dev/null")
    assert "+beta" in events[1]["net_diff"]
    assert "-alpha" not in events[1]["net_diff"]
    assert events[0]["at"].endswith("Z")


def test_str_replace_updates_unique_match(tmp_path: Path) -> None:
    from agent_loco.progress import bind_progress, current_events, reset_progress

    workspace = Workspace(tmp_path)
    (tmp_path / "ui.html").write_text("<header>old</header>\n<main></main>\n", encoding="utf-8")
    token = bind_progress()
    try:
        result = _tool(workspace, "str_replace").handler(
            path="ui.html",
            old_string="<header>old</header>",
            new_string="<header>new</header>",
        )
        second = _tool(workspace, "str_replace").handler(
            path="ui.html",
            old_string="<main></main>",
            new_string="<main>body</main>",
        )
        events = current_events()
    finally:
        reset_progress(token)
    assert result.ok
    assert "1 replacement" in result.output
    assert second.ok
    assert (tmp_path / "ui.html").read_text(encoding="utf-8") == (
        "<header>new</header>\n<main>body</main>\n"
    )
    assert events[0]["action"] == "updated"
    assert "-<header>old</header>" in events[0]["diff"]
    assert "+<header>new</header>" in events[0]["diff"]
    assert len(events) == 2
    assert "-<main></main>" in events[1]["diff"]
    assert "+<main>body</main>" in events[1]["diff"]
    assert "-<header>old</header>" in events[1]["net_diff"]
    assert "+<header>new</header>" in events[1]["net_diff"]
    assert "+<main>body</main>" in events[1]["net_diff"]
    assert events[1]["net_action"] == "updated"


def test_str_replace_requires_unique_match_unless_replace_all(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path)
    (tmp_path / "note.txt").write_text("alpha\nalpha\n", encoding="utf-8")
    missing = _tool(workspace, "str_replace").handler(
        path="note.txt",
        old_string="beta",
        new_string="gamma",
    )
    assert not missing.ok
    assert "not found" in missing.output
    ambiguous = _tool(workspace, "str_replace").handler(
        path="note.txt",
        old_string="alpha",
        new_string="beta",
    )
    assert not ambiguous.ok
    assert "2 times" in ambiguous.output
    replaced = _tool(workspace, "str_replace").handler(
        path="note.txt",
        old_string="alpha",
        new_string="beta",
        replace_all="true",
    )
    assert replaced.ok
    assert (tmp_path / "note.txt").read_text(encoding="utf-8") == "beta\nbeta\n"


def test_search_finds_literal(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path)
    (tmp_path / "note.txt").write_text("alpha loco beta\n", encoding="utf-8")
    result = _tool(workspace, "search_text").handler(query="loco")
    assert result.ok
    assert "note.txt" in result.output


from agent_loco.sandbox import SandboxError, Workspace
from agent_loco.tools.base import ToolSpec
from agent_loco.tools.issues import issues_tools


def _issues_tool(workspace: Workspace, name: str):
    from agent_loco.tools.issues import issues_tools
    return next(tool for tool in issues_tools(workspace) if tool.name == name)


def test_issue_tools_exist(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path)
    tools = issues_tools(workspace)
    tool_names = [t.name for t in tools]
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
    tmp_path: Path, monkeypatch
) -> None:
    from tests.support import init_git_repo
    from agent_loco.tools.git import run_git

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
