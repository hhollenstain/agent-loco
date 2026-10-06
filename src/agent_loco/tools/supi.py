from __future__ import annotations

from agent_loco.runtime.supi_review import format_review, review_diff
from agent_loco.runtime.workdiff import collect_work_diff
from agent_loco.sandbox import Workspace
from agent_loco.tools.base import ToolResult, ToolSpec, object_schema


def review_changes(workspace: Workspace, goal: str = "") -> ToolResult:
    """Review the working tree. The goal comes from the initial prompt."""
    try:
        diff = collect_work_diff(workspace, None, goal=goal or None)
    except OSError as exc:
        return ToolResult(False, f"could not read the change: {exc}")
    findings = review_diff(diff, goal=goal)
    text = format_review(findings)
    errors = [item for item in findings if item.severity == "error"]
    if errors:
        return ToolResult(False, text)
    return ToolResult(True, text)


def supi_tools(workspace: Workspace, goal: str = "") -> list[ToolSpec]:
    stated = goal

    def handler(goal: str = "") -> ToolResult:
        return review_changes(workspace, goal or stated)

    return [
        ToolSpec(
            name="review_changes",
            description=(
                "Code-review the working diff for hard-coded secrets, shell "
                "injection, unfinished markers, and new modules with no tests. "
                "Call this before stopping. Do not ask the user; the goal is "
                "already the initial prompt. Leave goal empty unless you are "
                "passing that same prompt through."
            ),
            parameters=object_schema(
                {
                    "goal": {
                        "type": "string",
                        "description": (
                            "Stated goal from the initial prompt. Omit to "
                            "review the diff with no extra questions."
                        ),
                    }
                }
            ),
            handler=handler,
        )
    ]
