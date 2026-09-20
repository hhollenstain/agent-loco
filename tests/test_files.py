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
    assert events[0]["at"].endswith("Z")


def test_search_finds_literal(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path)
    (tmp_path / "note.txt").write_text("alpha loco beta\n", encoding="utf-8")
    result = _tool(workspace, "search_text").handler(query="loco")
    assert result.ok
    assert "note.txt" in result.output
