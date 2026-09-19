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
