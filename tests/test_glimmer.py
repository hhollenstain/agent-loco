from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from agent_loco.agent.loop import CodingAgent
from agent_loco.agent.prompts import SYSTEM_PROMPT, adapt_system_prompt
from agent_loco.llm.client import AssistantTurn, OpenAICompatClient, ScriptedClient
from agent_loco.llm.glimmer import is_atem_parse_error, uses_atem_tools
from agent_loco.llm.toolparse import parse_tool_calls
from agent_loco.sandbox import Workspace
from agent_loco.tools import build_tools


class _FakeCompletions:
    def __init__(self, results: list[object]) -> None:
        self.results = list(results)
        self.calls: list[dict] = []

    def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def test_uses_atem_tools_for_muse_glimmer() -> None:
    assert uses_atem_tools("muse-glimmer:latest")
    assert uses_atem_tools("Muse-Glimmer-30B")
    assert not uses_atem_tools("qwen3.5:27b")


def test_adapt_system_prompt_swaps_json_for_atem() -> None:
    adapted = adapt_system_prompt(SYSTEM_PROMPT, "muse-glimmer:latest")
    assert "<atem:function_calls>" in adapted
    assert "Prefer native tool calls" not in adapted
    assert adapt_system_prompt(SYSTEM_PROMPT, "qwen3.5") == SYSTEM_PROMPT.strip()


def test_is_atem_parse_error() -> None:
    assert is_atem_parse_error(
        RuntimeError(
            "Error code: 500 - {'error': {'message': "
            "'parse Glimmer call to read_file: missing ATEM function_calls wrapper'}}"
        )
    )
    assert not is_atem_parse_error(RuntimeError("Error code: 500 - backend timeout"))


def test_glimmer_retries_after_missing_wrapper(monkeypatch) -> None:
    first = RuntimeError(
        "Error code: 500 - {'error': {'message': "
        "'parse Glimmer call to read_file: missing ATEM function_calls wrapper'}}"
    )
    second = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=(
                        "<atem:function_calls>\n"
                        '<atem:invoke name="read_file">\n'
                        '<atem:parameter name="path">cli.py</atem:parameter>\n'
                        "</atem:invoke>\n"
                        "</atem:function_calls>"
                    ),
                    tool_calls=None,
                )
            )
        ],
        usage=None,
    )
    fake = _FakeCompletions([first, second])
    llm = OpenAICompatClient(
        model="muse-glimmer:latest",
        base_url="http://127.0.0.1:9/v1",
        api_key="test",
    )
    monkeypatch.setattr(llm.client.chat, "completions", fake)

    turn = llm.complete(
        [{"role": "user", "content": "read cli.py"}],
        [{"type": "function", "function": {"name": "read_file"}}],
    )
    assert len(fake.calls) == 2
    assert "<atem:function_calls>" in fake.calls[1]["messages"][-1]["content"]
    assert turn.tool_calls == []
    parsed = parse_tool_calls(turn.text, {"read_file"})
    assert parsed[0].arguments == {"path": "cli.py"}


def test_glimmer_completes_without_tools_after_atem_retry_fails(monkeypatch) -> None:
    first = RuntimeError(
        "Error code: 500 - {'error': {'message': "
        "'parse Glimmer call to read_file: missing ATEM function_calls wrapper'}}"
    )
    second = RuntimeError(
        "Error code: 500 - {'error': {'message': "
        "'parse Glimmer call to read_file: missing ATEM function_calls wrapper'}}"
    )
    third = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=(
                        "<atem:function_calls>\n"
                        '<atem:invoke name="read_file">\n'
                        '<atem:parameter name="path">cli.py</atem:parameter>\n'
                        "</atem:invoke>\n"
                        "</atem:function_calls>"
                    ),
                    tool_calls=None,
                )
            )
        ],
        usage=None,
    )
    fake = _FakeCompletions([first, second, third])
    llm = OpenAICompatClient(
        model="muse-glimmer:latest",
        base_url="http://127.0.0.1:9/v1",
        api_key="test",
    )
    monkeypatch.setattr(llm.client.chat, "completions", fake)

    tools = [{"type": "function", "function": {"name": "read_file"}}]
    turn = llm.complete([{"role": "user", "content": "read cli.py"}], tools)
    assert len(fake.calls) == 3
    assert fake.calls[2]["tools"] is None
    parsed = parse_tool_calls(turn.text, {"read_file"})
    assert parsed[0].arguments == {"path": "cli.py"}


def test_glimmer_keeps_tool_arguments_as_json_string() -> None:
    llm = OpenAICompatClient(
        model="muse-glimmer:latest",
        base_url="http://127.0.0.1:9/v1",
        api_key="test",
    )
    fake = _FakeCompletions(
        [
            SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="ok", tool_calls=None))],
                usage=None,
            )
        ]
    )
    llm.client.chat.completions = fake
    encoded = '{"path": "cli.py"}'
    llm.complete(
        [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": encoded,
                        },
                    }
                ],
            }
        ],
        [],
    )
    sent = fake.calls[0]["messages"][0]["tool_calls"][0]["function"]["arguments"]
    assert sent == encoded
    assert isinstance(sent, str)


def test_agent_executes_atem_read(tmp_path: Path) -> None:
    (tmp_path / "note.txt").write_text("hello from glimmer\n", encoding="utf-8")
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
                text=(
                    "<atem:function_calls>\n"
                    '<atem:invoke name="read_file">\n'
                    '<atem:parameter name="path">note.txt</atem:parameter>\n'
                    "</atem:invoke>\n"
                    "</atem:function_calls>"
                )
            ),
            AssistantTurn(text="The file says hello from glimmer"),
        ]
    )
    llm.model = "muse-glimmer:latest"
    result = CodingAgent(llm, tools, max_iterations=4).run("Read note.txt")
    assert result.tool_calls == 1
    assert "<atem:function_calls>" in llm.calls[0][0]["content"]
    tool_results = [message.get("content", "") for message in llm.calls[1]]
    assert any("hello from glimmer" in text for text in tool_results)
