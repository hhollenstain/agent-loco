from __future__ import annotations

from pathlib import Path

from agent_loco.agent.loop import CodingAgent
from agent_loco.llm.client import AssistantTurn, ScriptedClient, ToolCall
from agent_loco.sandbox import Workspace
from agent_loco.tools import build_tools


def test_agent_writes_file_then_stops(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path)
    tools = build_tools(
        workspace,
        test_command=None,
        command_timeout_seconds=10,
        git_author_name=None,
        git_author_email=None,
    )
    llm = ScriptedClient(
        [
            AssistantTurn(
                text=None,
                tool_calls=[
                    ToolCall(
                        id="call-1",
                        name="write_file",
                        arguments={"path": "note.txt", "content": "done\n"},
                    )
                ],
            ),
            AssistantTurn(text="Wrote note.txt"),
        ]
    )
    result = CodingAgent(llm, tools, max_iterations=5).run("Write note.txt")
    assert result.stopped_reason == "completed"
    assert result.tool_calls == 1
    assert (tmp_path / "note.txt").read_text(encoding="utf-8") == "done\n"
    assert result.summary == "Wrote note.txt"


def test_agent_logs_llm_response_time(tmp_path: Path, caplog) -> None:
    import logging

    workspace = Workspace(tmp_path)
    tools = build_tools(
        workspace,
        test_command=None,
        command_timeout_seconds=10,
        git_author_name=None,
        git_author_email=None,
    )
    llm = ScriptedClient(
        [
            AssistantTurn(
                text=None,
                tool_calls=[
                    ToolCall(
                        id="call-1",
                        name="write_file",
                        arguments={"path": "note.txt", "content": "done\n"},
                    )
                ],
            ),
            AssistantTurn(text="Wrote note.txt"),
        ]
    )
    caplog.set_level(logging.INFO, logger="loco")
    CodingAgent(llm, tools, max_iterations=5).run("Write note.txt")
    messages = [record.getMessage() for record in caplog.records]
    assert any(message.startswith("llm agent response in ") for message in messages)


def test_agent_accepts_json_text_tool_calls(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path)
    tools = build_tools(
        workspace,
        test_command=None,
        command_timeout_seconds=10,
        git_author_name=None,
        git_author_email=None,
    )
    llm = ScriptedClient(
        [
            AssistantTurn(
                text='{"name": "write_file", "arguments": {"path": "via.json", "content": "ok\\n"}}'
            ),
            AssistantTurn(text="Wrote via.json"),
        ]
    )
    result = CodingAgent(llm, tools, max_iterations=5).run("Write via.json")
    assert result.tool_calls == 1
    assert (tmp_path / "via.json").read_text(encoding="utf-8") == "ok\n"


def test_agent_nudges_after_plan_only_turn(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path)
    tools = build_tools(
        workspace,
        test_command=None,
        command_timeout_seconds=10,
        git_author_name=None,
        git_author_email=None,
    )
    llm = ScriptedClient(
        [
            AssistantTurn(text="I will add a progress UI next."),
            AssistantTurn(
                text=None,
                tool_calls=[
                    ToolCall(
                        id="call-1",
                        name="write_file",
                        arguments={"path": "ui.txt", "content": "progress\n"},
                    )
                ],
            ),
            AssistantTurn(text="Added a progress UI."),
        ]
    )
    result = CodingAgent(llm, tools, max_iterations=5).run("Add a progress UI")
    assert result.tool_calls == 1
    assert (tmp_path / "ui.txt").read_text(encoding="utf-8") == "progress\n"
    contents = [
        message["content"]
        for message in llm.calls[-1]
        if message.get("role") == "user" and isinstance(message.get("content"), str)
    ]
    assert any("have not changed any files" in content for content in contents)


def test_agent_nudges_through_multiple_plan_turns(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path)
    tools = build_tools(
        workspace,
        test_command=None,
        command_timeout_seconds=10,
        git_author_name=None,
        git_author_email=None,
    )
    llm = ScriptedClient(
        [
            AssistantTurn(text="First I will inspect the repo."),
            AssistantTurn(text="Then I will write the file."),
            AssistantTurn(
                text=None,
                tool_calls=[
                    ToolCall(
                        id="call-1",
                        name="write_file",
                        arguments={"path": "done.txt", "content": "ok\n"},
                    )
                ],
            ),
            AssistantTurn(text="Wrote done.txt"),
        ]
    )
    result = CodingAgent(llm, tools, max_iterations=8).run("Write done.txt")
    assert result.tool_calls == 1
    assert (tmp_path / "done.txt").read_text(encoding="utf-8") == "ok\n"
