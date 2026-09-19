from __future__ import annotations

from agent_loco.sandbox import Workspace
from agent_loco.tools.base import ToolResult, ToolSpec, object_schema
from agent_loco.tools.shell import run_command


def test_tools(
    workspace: Workspace,
    test_command: str | None,
    timeout_seconds: int,
) -> list[ToolSpec]:
    return [
        ToolSpec(
            name="run_tests",
            description="Run the project's configured test command and return the full output.",
            parameters=object_schema({}),
            handler=lambda: run_project_tests(workspace, test_command, timeout_seconds),
        )
    ]


def run_project_tests(
    workspace: Workspace,
    test_command: str | None,
    timeout_seconds: int,
) -> ToolResult:
    if not test_command:
        return ToolResult(False, "no test command configured for this project")
    return run_command(workspace, test_command, timeout_seconds)
