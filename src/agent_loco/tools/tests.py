from __future__ import annotations

import time

from agent_loco.progress import record_test_run
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
            handler=lambda: run_project_tests(
                workspace, test_command, timeout_seconds, phase="agent"
            ),
        )
    ]


def run_project_tests(
    workspace: Workspace,
    test_command: str | None,
    timeout_seconds: int,
    *,
    phase: str = "tests",
) -> ToolResult:
    if not test_command:
        result = ToolResult(False, "no test command configured for this project")
        record_test_run(command="", ok=False, output=result.output, phase=phase)
        return result
    started = time.perf_counter()
    result = run_command(workspace, test_command, timeout_seconds)
    elapsed_ms = int(round((time.perf_counter() - started) * 1000))
    record_test_run(
        command=test_command,
        ok=result.ok,
        output=result.output,
        phase=phase,
        elapsed_ms=elapsed_ms,
    )
    return result
