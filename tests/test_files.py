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
