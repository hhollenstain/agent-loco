from __future__ import annotations

from agent_loco.runtime.project import load_project
from agent_loco.runtime.uireview import collect_ui_evidence, format_ui_evidence
from agent_loco.sandbox import Workspace
from agent_loco.tools.base import ToolResult, ToolSpec, object_schema


def browser_tools(workspace: Workspace, goal: str = "") -> list[ToolSpec]:
    return [
        ToolSpec(
            name="review_ui",
            description=(
                "Render the UI in a headless browser, click the new control and its "
                "panel, and report JavaScript errors, dead controls, and crushed/"
                "zero-size elements. The screenshot is taken after those clicks, so "
                "the opened panel is visible. Use this after HTML, CSS, JS, or "
                "template edits. Optional url, HTML path, and extra click selector."
            ),
            parameters=object_schema(
                {
                    "url": {
                        "type": "string",
                        "description": "Page to open. Omit to start this project's preview server.",
                    },
                    "path": {
                        "type": "string",
                        "description": "Workspace HTML file to serve when url is omitted.",
                    },
                    "click": {
                        "type": "string",
                        "description": "Extra CSS selector or button name to click last, before capturing.",
                    },
                    "wait_ms": {
                        "type": "integer",
                        "description": "How long to let the page settle. Default 4000.",
                    },
                },
                [],
            ),
            handler=lambda url=None, path=None, click=None, wait_ms=4000, goal=goal: _review_ui(
                workspace, goal, url, path, click, wait_ms
            ),
        )
    ]


def _review_ui(
    workspace: Workspace,
    goal: str,
    url: str | None,
    path: str | None,
    click: str | None,
    wait_ms: int,
) -> ToolResult:
    project = load_project(workspace.root)
    evidence = collect_ui_evidence(
        workspace,
        project,
        goal=goal or "review ui",
        url=url or None,
        path=path or None,
        click=click or None,
        wait_ms=int(wait_ms or 4000),
    )
    report = format_ui_evidence(evidence)
    if evidence.blocking_errors or evidence.smashed or evidence.dead_controls:
        return ToolResult(False, report)
    if not evidence.ok and evidence.notes and not evidence.snapshot:
        return ToolResult(False, report)
    return ToolResult(True, report)
