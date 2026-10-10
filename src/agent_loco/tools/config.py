from __future__ import annotations

from agent_loco.runtime.project import save_project_commands
from agent_loco.sandbox import Workspace
from agent_loco.tools.base import ToolResult, ToolSpec, object_schema


def config_tools(workspace: Workspace) -> list[ToolSpec]:
    return [
        ToolSpec(
            name="configure_project",
            description=(
                "Set test_command, lint_command, preview_command, or setup_command "
                "in .loco/config.yaml. Other settings are kept. Before calling this, "
                "web_search for this language or framework and fetch_url an official "
                "docs page, then use commands those docs support. preview_command "
                "must listen on {port}. A web app serves the app. A native or Godot "
                "app captures the changed screen and serves that image on {port}."
            ),
            parameters=object_schema(
                {
                    "test_command": {
                        "type": "string",
                        "description": (
                            "Command that runs the real suite and exits non-zero on failure."
                        ),
                    },
                    "lint_command": {
                        "type": "string",
                        "description": "Command that lints this project, when the docs name one.",
                    },
                    "preview_command": {
                        "type": "string",
                        "description": (
                            "Command that listens on {port} and shows the changed screen."
                        ),
                    },
                    "setup_command": {
                        "type": "string",
                        "description": "Command that installs dependencies before tests.",
                    },
                },
            ),
            handler=lambda **kwargs: configure_project(workspace, **kwargs),
        )
    ]


def configure_project(
    workspace: Workspace,
    *,
    test_command: str | None = None,
    lint_command: str | None = None,
    preview_command: str | None = None,
    setup_command: str | None = None,
) -> ToolResult:
    try:
        written = save_project_commands(
            workspace.root,
            test_command=test_command,
            lint_command=lint_command,
            preview_command=preview_command,
            setup_command=setup_command,
        )
    except ValueError as exc:
        return ToolResult(False, str(exc))
    except OSError as exc:
        return ToolResult(False, f"could not write .loco/config.yaml: {exc}")
    lines = ", ".join(written)
    return ToolResult(True, f"Updated .loco/config.yaml: {lines}")
