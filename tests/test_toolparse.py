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


def test_parse_qwen_xml_function_call() -> None:
    text = """<tool_call>
<function=str_replace>
<parameter=path>src/agent_loco/templates/index.html</parameter>
<parameter=old_string>
<div class="task"></div>
</parameter>
<parameter=new_string>
<div class="task-stages">progress</div>
</parameter>
</function>
</tool_call>"""
    calls = parse_tool_calls(text, {"read_file", "str_replace", "write_file"})
    assert len(calls) == 1
    assert calls[0].name == "str_replace"
    assert calls[0].arguments == {
        "path": "src/agent_loco/templates/index.html",
        "old_string": '<div class="task"></div>',
        "new_string": '<div class="task-stages">progress</div>',
    }


def test_parse_qwen_arg_key_tool_call() -> None:
    text = """<tool_call>
str_replace
<arg_key>path</arg_key>
<arg_value>ui.html</arg_value>
<arg_key>old_string</arg_key>
<arg_value>old</arg_value>
<arg_key>new_string</arg_key>
<arg_value>new</arg_value>
</tool_call>"""
    calls = parse_tool_calls(text, {"str_replace"})
    assert len(calls) == 1
    assert calls[0].name == "str_replace"
    assert calls[0].arguments == {
        "path": "ui.html",
        "old_string": "old",
        "new_string": "new",
    }


def test_parse_atem_function_calls() -> None:
    text = """<|start|>assistant to=read_file<|message|>
<atem:function_calls>
<atem:invoke name="read_file">
<atem:parameter name="path">src/agent_loco/cli.py</atem:parameter>
</atem:invoke>
</atem:function_calls>"""
    calls = parse_tool_calls(text, {"read_file", "write_file"})
    assert len(calls) == 1
    assert calls[0].name == "read_file"
    assert calls[0].arguments == {"path": "src/agent_loco/cli.py"}


def test_parse_atem_invoke_without_wrapper() -> None:
    text = """<atem:invoke name="str_replace">
<atem:parameter name="path">ui.html</atem:parameter>
<atem:parameter name="old_string">old</atem:parameter>
<atem:parameter name="new_string">new</atem:parameter>
</atem:invoke>"""
    calls = parse_tool_calls(text, {"str_replace"})
    assert len(calls) == 1
    assert calls[0].arguments == {
        "path": "ui.html",
        "old_string": "old",
        "new_string": "new",
    }


def test_parse_atem_namespaced_tool() -> None:
    text = """<atem:function_calls>
<atem:invoke name="read_file.read_file">
<atem:parameter name="path">note.txt</atem:parameter>
</atem:invoke>
</atem:function_calls>"""
    calls = parse_tool_calls(text, {"read_file"})
    assert calls[0].name == "read_file"


def test_parse_json_embedded_in_prose() -> None:
    text = (
        "Let's inspect the dependencies and then decide.\n\n"
        '{"name": "read_file", "arguments": {"path": "src/agent_loco/cli.py"}}'
    )
    calls = parse_tool_calls(text, {"read_file", "write_file"})
    assert len(calls) == 1
    assert calls[0].name == "read_file"
    assert calls[0].arguments == {"path": "src/agent_loco/cli.py"}
