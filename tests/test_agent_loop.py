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


def test_agent_uses_custom_system_prompt(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path)
    tools = build_tools(
        workspace,
        test_command=None,
        command_timeout_seconds=10,
        git_author_name=None,
        git_author_email=None,
    )
    llm = ScriptedClient([AssistantTurn(text="ok")])
    CodingAgent(
        llm,
        tools,
        max_iterations=2,
        system_prompt="Prefer Rust. Never touch Python.",
    ).run("Say hi")
    assert llm.calls[0][0]["content"] == "Prefer Rust. Never touch Python."


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
    assert any("Add a progress UI" in content for content in contents)
    assert any("placeholder" in content.lower() for content in contents)


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


def test_looks_unfinished_detects_mid_work_replies() -> None:
    from agent_loco.agent.loop import looks_unfinished

    assert looks_unfinished(
        "There are two failing tests - I need to fix the mocks. Let me update the tests:"
    )
    assert looks_unfinished("I'll write the missing assertions next.")
    assert not looks_unfinished("Wrote tests and they pass. Two cases still lack coverage.")


def test_agent_keeps_going_after_unfinished_summary(tmp_path: Path) -> None:
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
                        arguments={"path": "app.py", "content": "ok\n"},
                    )
                ],
            ),
            AssistantTurn(
                text=(
                    "The tests are running and some are passing. There are two failing "
                    "tests - I need to fix the mocks to return coroutines properly. "
                    "Let me update the tests:"
                )
            ),
            AssistantTurn(
                text=None,
                tool_calls=[
                    ToolCall(
                        id="call-2",
                        name="write_file",
                        arguments={"path": "tests.py", "content": "fixed\n"},
                    )
                ],
            ),
            AssistantTurn(text="Fixed the test mocks."),
        ]
    )
    result = CodingAgent(llm, tools, max_iterations=8).run("Add tests")
    assert result.stopped_reason == "completed"
    assert result.summary == "Fixed the test mocks."
    assert (tmp_path / "tests.py").read_text(encoding="utf-8") == "fixed\n"
    contents = [
        message["content"]
        for message in llm.calls[-1]
        if message.get("role") == "user" and isinstance(message.get("content"), str)
    ]
    assert any("not a finish" in content for content in contents)


def test_agent_nudges_after_inspect_only_tools(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path)
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    tools = build_tools(
        workspace,
        test_command=None,
        command_timeout_seconds=10,
        git_author_name=None,
        git_author_email=None,
    )
    reads = [
        AssistantTurn(
            text=None,
            tool_calls=[
                ToolCall(
                    id=f"read-{index}",
                    name="read_file",
                    arguments={"path": "app.py"},
                )
            ],
        )
        for index in range(3)
    ]
    llm = ScriptedClient(
        [
            *reads,
            AssistantTurn(
                text=None,
                tool_calls=[
                    ToolCall(
                        id="write-1",
                        name="write_file",
                        arguments={"path": "done.txt", "content": "ok\n"},
                    )
                ],
            ),
            AssistantTurn(text="Wrote done.txt"),
        ]
    )
    result = CodingAgent(llm, tools, max_iterations=8).run("Write done.txt")
    assert result.tool_calls >= 4
    assert (tmp_path / "done.txt").read_text(encoding="utf-8") == "ok\n"
    contents = [
        message["content"]
        for message in llm.calls[-1]
        if message.get("role") == "user" and isinstance(message.get("content"), str)
    ]
    assert any("without changing files" in content for content in contents)


def test_agent_str_replace_counts_as_mutation(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path)
    (tmp_path / "ui.html").write_text("<main></main>\n", encoding="utf-8")
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
                        id="edit-1",
                        name="str_replace",
                        arguments={
                            "path": "ui.html",
                            "old_string": "<main></main>",
                            "new_string": "<main class='stages'></main>",
                        },
                    )
                ],
            ),
            AssistantTurn(text="Added stages."),
        ]
    )
    result = CodingAgent(llm, tools, max_iterations=5).run("Add stages")
    assert result.stopped_reason == "completed"
    assert result.tool_calls == 1
    assert "<main class='stages'></main>" in (tmp_path / "ui.html").read_text(
        encoding="utf-8"
    )
    contents = [
        message["content"]
        for message in llm.calls[-1]
        if message.get("role") == "user" and isinstance(message.get("content"), str)
    ]
    assert not any("have not changed any files" in content for content in contents)


def test_agent_accepts_qwen_xml_str_replace(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path)
    (tmp_path / "ui.html").write_text("<main></main>\n", encoding="utf-8")
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
                text=(
                    "<tool_call><function=str_replace>"
                    "<parameter=path>ui.html</parameter>"
                    "<parameter=old_string><main></main></parameter>"
                    "<parameter=new_string><main class='stages'></main></parameter>"
                    "</function></tool_call>"
                )
            ),
            AssistantTurn(text="Patched ui.html"),
        ]
    )
    result = CodingAgent(llm, tools, max_iterations=5).run("Add stages")
    assert result.tool_calls == 1
    assert "<main class='stages'></main>" in (tmp_path / "ui.html").read_text(
        encoding="utf-8"
    )
