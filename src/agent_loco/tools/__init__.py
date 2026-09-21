from __future__ import annotations

from agent_loco.sandbox import SandboxError, Workspace
from agent_loco.tools.base import ToolResult, ToolSpec
from agent_loco.tools.browser import browser_tools
from agent_loco.tools.files import file_tools
from agent_loco.tools.git import git_tools
from agent_loco.tools.issues import issues_tools
from agent_loco.tools.shell import shell_tools
from agent_loco.tools.tests import test_tools


def build_tools(
    workspace: Workspace,
    *,
    test_command: str | None,
    command_timeout_seconds: int,
    git_author_name: str | None,
    git_author_email: str | None,
    allow_publish: bool = False,
) -> list[ToolSpec]:
    return [
        *file_tools(workspace),
        *browser_tools(workspace),
        *shell_tools(workspace, command_timeout_seconds, allow_publish=allow_publish),
        *test_tools(workspace, test_command, command_timeout_seconds),
        *git_tools(workspace, git_author_name, git_author_email, allow_publish=allow_publish),
        *issues_tools(workspace),
    ]


def execute_tool(tools: list[ToolSpec], name: str, arguments: dict) -> ToolResult:
    spec = next((tool for tool in tools if tool.name == name), None)
    if spec is None:
        return ToolResult(False, f"unknown tool: {name}")
    try:
        return spec.handler(**arguments)
    except SandboxError as exc:
        return ToolResult(False, str(exc))
    except TypeError as exc:
        return ToolResult(False, f"invalid arguments for {name}: {exc}")
    except Exception as exc:  # noqa: BLE001 - tool errors must stay in-band
        return ToolResult(False, f"{name} failed: {exc}")
