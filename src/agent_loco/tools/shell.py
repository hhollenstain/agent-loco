from __future__ import annotations

import subprocess

from agent_loco.sandbox import Workspace
from agent_loco.tools.base import ToolResult, ToolSpec, object_schema


def shell_tools(workspace: Workspace, default_timeout: int) -> list[ToolSpec]:
    return [
        ToolSpec(
            name="run_command",
            description=(
                "Run a shell command in the workspace. Use this for language tooling, "
                "formatters, and project-specific CLIs. Prefer run_tests for the test suite."
            ),
            parameters=object_schema(
                {
                    "command": {
                        "type": "string",
                        "description": "Shell command to run with bash -lc.",
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "description": "Optional timeout. Defaults to the agent command timeout.",
                    },
                },
                ["command"],
            ),
            handler=lambda command, timeout_seconds=None: run_command(
                workspace,
                command,
                int(timeout_seconds) if timeout_seconds is not None else default_timeout,
            ),
        )
    ]


def run_command(workspace: Workspace, command: str, timeout_seconds: int) -> ToolResult:
    if not command or not command.strip():
        return ToolResult(False, "command is required")
    try:
        result = subprocess.run(
            ["/bin/bash", "-lc", command],
            cwd=workspace.root,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return ToolResult(False, f"timed out after {timeout_seconds}s: {command}")
    except OSError as exc:
        return ToolResult(False, f"failed to start command: {exc}")

    output = _combine(result.stdout, result.stderr)
    if result.returncode != 0:
        return ToolResult(False, f"exit {result.returncode}\n{output}".strip())
    return ToolResult(True, output or "(no output)")


def _combine(stdout: str, stderr: str) -> str:
    parts = [part.strip() for part in (stdout, stderr) if part and part.strip()]
    return "\n".join(parts)
