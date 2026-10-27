from __future__ import annotations

from pathlib import Path

from agent_loco.agent.loop import CodingAgent, is_comment_only_edit, note_edit
from agent_loco.llm.client import AssistantTurn, ScriptedClient, ToolCall
from agent_loco.sandbox import Workspace
from agent_loco.tools import file_tools


def _replace(call_id: str, old: str, new: str) -> AssistantTurn:
    return AssistantTurn(
        text=None,
        tool_calls=[
            ToolCall(
                id=call_id,
                name="str_replace",
                arguments={"path": "app.py", "old_string": old, "new_string": new},
            )
        ],
    )


def test_comment_only_edit_is_not_a_code_change() -> None:
    old = "def hello():\n    return 'world'\n"
    new = "def hello():\n    # note\n    return 'world'\n"
    assert is_comment_only_edit("app.py", old, new)
    assert not is_comment_only_edit("app.py", old, "def hello():\n    return 'loco'\n")
    assert not is_comment_only_edit("README.md", "# Old\n", "# New\n")


def test_note_edit_marks_a_reversal() -> None:
    recent: list[tuple[str, str, str]] = []
    old = "def hello():\n    return 'world'\n"
    new = "def hello():\n    # note\n    return 'world'\n"
    assert note_edit(recent, "app.py", old, new) == "churn"
    assert note_edit(recent, "app.py", new, old) == "churn"


def test_adding_and_removing_a_comment_stops_the_run(tmp_path: Path) -> None:
    original = "def hello():\n    return 'world'\n"
    commented = "def hello():\n    # note\n    return 'world'\n"
    (tmp_path / "app.py").write_text(original, encoding="utf-8")
    turns = []
    for index in range(6):
        if index % 2 == 0:
            turns.append(_replace(f"call-{index}", original, commented))
        else:
            turns.append(_replace(f"call-{index}", commented, original))
    turns.append(AssistantTurn(text="done"))
    llm = ScriptedClient(turns)
    result = CodingAgent(llm, file_tools(Workspace(tmp_path)), max_iterations=12).run(
        "Make hello return loco"
    )
    assert result.stopped_reason == "oscillating"
    assert result.iterations < 8
    assert "return 'loco'" not in (tmp_path / "app.py").read_text(encoding="utf-8")


def test_a_real_edit_still_finishes(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("def hello():\n    return 'world'\n", encoding="utf-8")
    llm = ScriptedClient(
        [
            _replace(
                "call-1",
                "return 'world'",
                "return 'loco'",
            ),
            AssistantTurn(text="Hello now returns loco."),
        ]
    )
    result = CodingAgent(llm, file_tools(Workspace(tmp_path)), max_iterations=6).run(
        "Make hello return loco"
    )
    assert result.stopped_reason == "completed"
    assert "return 'loco'" in (tmp_path / "app.py").read_text(encoding="utf-8")
