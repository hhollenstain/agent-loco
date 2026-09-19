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
