from __future__ import annotations

from agent_loco.llm.toolparse import parse_tool_calls


def test_parse_fenced_json() -> None:
    text = """```json
{"name": "git_status", "arguments": {}}
```"""
    calls = parse_tool_calls(text, {"git_status", "write_file"})
    assert len(calls) == 1
    assert calls[0].name == "git_status"
    assert calls[0].arguments == {}


def test_ignores_unknown_tools() -> None:
    text = '{"name": "rm_rf", "arguments": {"path": "/"}}'
    assert parse_tool_calls(text, {"git_status"}) == []


def test_parse_json_embedded_in_prose() -> None:
    text = (
        "Let's inspect the dependencies and then decide.\n\n"
        '{"name": "read_file", "arguments": {"path": "src/agent_loco/cli.py"}}'
    )
    calls = parse_tool_calls(text, {"read_file", "write_file"})
    assert len(calls) == 1
    assert calls[0].name == "read_file"
    assert calls[0].arguments == {"path": "src/agent_loco/cli.py"}
