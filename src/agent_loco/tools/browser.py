from __future__ import annotations

from agent_loco.runtime.project import load_project
from agent_loco.runtime.uireview import collect_ui_evidence, format_ui_evidence
from agent_loco.sandbox import Workspace
from agent_loco.tools.base import ToolResult, ToolSpec, object_schema
from agent_loco.tools.codechange import code_fingerprint, reused_check_result

_REVIEW_CACHE: dict[str, tuple[str, ToolResult]] = {}


def browser_tools(workspace: Workspace, goal: str = "") -> list[ToolSpec]:
    return [
        ToolSpec(
            name="review_ui",
            description=(
                "Render the UI in a headless browser, click the new control and its "
                "panel, and report JavaScript errors, dead controls, and crushed/"
                "zero-size elements. The screenshot is taken after those clicks, so "
                "the opened panel is visible. Call this once after HTML, CSS, JS, or "
                "template edits, including edits in another project in this workspace. "
                "If no code has changed since the last review, this returns that "
                "result and does not open the browser again. Optional url, HTML path, "
                "and extra click selector. In a Godot project it runs the scene in "
                "Godot and reports engine errors and nodes that draw nothing; pass "
                "a .tscn path to pick the scene."
            ),
            parameters=object_schema(
                {
                    "url": {
                        "type": "string",
                        "description": "Page to open. Omit to start this project's preview server.",
                    },
                    "path": {
                        "type": "string",
                        "description": (
                            "Workspace HTML file to serve, or a Godot .tscn scene to run, "
                            "when url is omitted."
                        ),
                    },
                    "click": {
                        "type": "string",
                        "description": (
                            "Extra CSS selector or button name to click last, before capturing."
                        ),
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
    key = str(workspace.root)
    fingerprint = "|".join(
        (
            code_fingerprint(workspace.root),
            str(url or ""),
            str(path or ""),
            str(click or ""),
        )
    )
    cached = _REVIEW_CACHE.get(key)
    if cached is not None and cached[0] == fingerprint:
        return reused_check_result("review_ui", cached[1])
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
        result = ToolResult(False, report)
    elif not evidence.ok and evidence.notes and not evidence.snapshot:
        result = ToolResult(False, report)
    else:
        result = ToolResult(True, report)
    _REVIEW_CACHE[key] = (fingerprint, result)
    return result
