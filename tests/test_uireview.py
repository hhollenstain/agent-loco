from __future__ import annotations

from pathlib import Path

from agent_loco.agent.prompts import SYSTEM_PROMPT
from agent_loco.llm.client import AssistantTurn, ScriptedClient
from agent_loco.progress import bind_progress, reset_progress
from agent_loco.runtime.improve import _pr_body, _review_goal
from agent_loco.runtime.project import load_project
from agent_loco.runtime.review import REVIEW_SYSTEM, review_goal
from agent_loco.runtime.uireview import (
    UiEvidence,
    _from_eval,
    _playwright_probe,
    format_ui_evidence,
    resolve_ui_screenshot,
    ui_review_needed,
    unverified_interactive_ui,
)
from agent_loco.sandbox import Workspace
from agent_loco.tools import build_tools, execute_tool


def test_ui_review_needed_for_template_diff_and_ui_goals(tmp_path: Path) -> None:
    (tmp_path / "src" / "agent_loco" / "templates").mkdir(parents=True)
    (tmp_path / "src" / "agent_loco" / "templates" / "index.html").write_text(
        "<html></html>\n", encoding="utf-8"
    )
    assert ui_review_needed(
        "Add a progress bar",
        "diff --git a/src/agent_loco/templates/index.html",
    )
    assert ui_review_needed("Add a progress bar", "", tmp_path)
    assert not ui_review_needed("update discord.py", "Pipfile.lock hashes changed")


def test_format_ui_evidence_lists_errors_and_tiny_controls() -> None:
    text = format_ui_evidence(
        UiEvidence(
            ok=False,
            url="http://127.0.0.1:9/",
            title="loco",
            page_errors=["label is not defined"],
            smashed=["Init (0x0)"],
            dead_controls=["[data-task-pane=\"screenshots\"] did not show the screenshots pane"],
            snapshot="button: Queue task (120x32)",
            interactive=False,
        )
    )
    assert "label is not defined" in text
    assert "Init (0x0)" in text
    assert "Queue task" in text
    assert "did not show the screenshots pane" in text
    assert "static dump-dom" in text


def test_from_eval_marks_js_errors_and_zero_size_controls() -> None:
    evidence = _from_eval(
        "http://127.0.0.1:9/",
        {
            "title": "loco",
            "text": "Queue task",
            "errors": ["label is not defined"],
            "tabs": [
                {
                    "name": "Screenshots",
                    "pane": "screenshots",
                    "selected": False,
                    "panelHidden": True,
                }
            ],
            "elements": [
                {"tag": "button", "name": "Init", "w": 0, "h": 0},
                {"tag": "button", "name": "Queue task", "w": 120, "h": 32},
            ],
        },
        page_errors=[],
        console_errors=[],
        screenshot=None,
    )
    assert evidence.ok is False
    assert "label is not defined" in evidence.page_errors
    assert "Init (0x0)" in evidence.smashed
    assert "Queue task (120x32)" in evidence.snapshot
    assert "tab: Screenshots" in evidence.snapshot


def test_review_goal_includes_rendered_ui_section() -> None:
    llm = ScriptedClient(
        [AssistantTurn(text='{"met": false, "reason": "progress stages have no labels"}')]
    )
    token = bind_progress()
    try:
        verdict = review_goal(
            llm,
            "Add a task stage progress bar",
            diff="--- a/index.html\n+++ b/index.html\n",
            summary="updated css",
            tests_passed=True,
            ui_evidence="Rendered UI:\nJavaScript errors:\n- label is not defined",
        )
    finally:
        reset_progress(token)
    assert verdict.met is False
    user = llm.calls[0][-1]["content"]
    assert "Rendered UI:" in user
    assert "label is not defined" in user


def test_review_goal_overrides_met_when_rendered_ui_throws(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        "agent_loco.runtime.improve.collect_ui_evidence",
        lambda *args, **kwargs: UiEvidence(
            ok=False,
            page_errors=["label is not defined"],
            smashed=["Init (0x0)"],
        ),
    )
    (tmp_path / ".loco").mkdir()
    (tmp_path / ".loco" / "config.yaml").write_text("name: fixture\n", encoding="utf-8")
    llm = ScriptedClient(
        [AssistantTurn(text='{"met": true, "reason": "css layout looks fine"}')]
    )
    token = bind_progress()
    try:
        verdict = _review_goal(
            Workspace(tmp_path),
            load_project(tmp_path),
            llm,
            "Add a progress bar",
            "diff --git a/src/agent_loco/templates/index.html",
            "updated css",
            True,
        )
    finally:
        reset_progress(token)
    assert verdict.met is False
    assert "label is not defined" in verdict.reason


def test_review_goal_overrides_met_when_screenshot_tab_is_dead(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        "agent_loco.runtime.improve.collect_ui_evidence",
        lambda *args, **kwargs: UiEvidence(
            ok=False,
            dead_controls=[
                '[data-task-pane="screenshots"] did not show the screenshots pane'
            ],
        ),
    )
    (tmp_path / ".loco").mkdir()
    (tmp_path / ".loco" / "config.yaml").write_text("name: fixture\n", encoding="utf-8")
    llm = ScriptedClient(
        [AssistantTurn(text='{"met": true, "reason": "screenshots tab is in the template"}')]
    )
    token = bind_progress()
    try:
        verdict = _review_goal(
            Workspace(tmp_path),
            load_project(tmp_path),
            llm,
            "Show UI screenshots in another tab",
            "diff --git a/src/agent_loco/templates/index.html",
            "added screenshots tab markup",
            True,
        )
    finally:
        reset_progress(token)
    assert verdict.met is False
    assert "did not show the screenshots pane" in verdict.reason


def test_review_goal_overrides_met_when_dump_dom_cannot_click_tabs(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        "agent_loco.runtime.improve.collect_ui_evidence",
        lambda *args, **kwargs: UiEvidence(
            ok=True,
            interactive=False,
            notes="Captured with Chrome --dump-dom",
        ),
    )
    (tmp_path / ".loco").mkdir()
    (tmp_path / ".loco" / "config.yaml").write_text("name: fixture\n", encoding="utf-8")
    llm = ScriptedClient(
        [AssistantTurn(text='{"met": true, "reason": "the screenshots tab exists in html"}')]
    )
    token = bind_progress()
    try:
        verdict = _review_goal(
            Workspace(tmp_path),
            load_project(tmp_path),
            llm,
            "Add a Screenshots tab next to Changes",
            "diff --git a/src/agent_loco/templates/index.html",
            "added tab markup",
            True,
        )
    finally:
        reset_progress(token)
    assert verdict.met is False
    assert "dump-dom" in verdict.reason


def test_unverified_interactive_ui_requires_clicking_visible_screenshots_tab() -> None:
    reason = unverified_interactive_ui(
        "Show screenshots in a tab",
        UiEvidence(
            ok=True,
            snapshot="tab: Screenshots selected=False panelHidden=True",
            clicked=["#history-list button.task"],
        ),
    )
    assert reason is not None
    assert "Screenshots tab" in reason


def test_review_ui_tool_reports_capture(tmp_path: Path, monkeypatch) -> None:
    workspace = Workspace(tmp_path)
    (tmp_path / ".loco").mkdir()
    (tmp_path / ".loco" / "config.yaml").write_text("name: fixture\n", encoding="utf-8")
    monkeypatch.setattr(
        "agent_loco.tools.browser.collect_ui_evidence",
        lambda *args, **kwargs: UiEvidence(
            ok=False,
            url="http://127.0.0.1:9/",
            page_errors=["label is not defined"],
            snapshot="progress-stage: Init (0x0)",
        ),
    )
    tools = build_tools(
        workspace,
        test_command=None,
        command_timeout_seconds=5,
        git_author_name=None,
        git_author_email=None,
    )
    result = execute_tool(tools, "review_ui", {})
    assert result.ok
    assert "label is not defined" in result.output
    assert "Init (0x0)" in result.output


def test_agent_prompt_requires_review_ui_after_ui_edits() -> None:
    assert "review_ui" in SYSTEM_PROMPT
    assert "zero-size" in SYSTEM_PROMPT
    assert "Click new tabs" in SYSTEM_PROMPT
    assert "Markup for a Screenshots tab is not enough" in REVIEW_SYSTEM


def test_resolve_ui_screenshot_serves_unique_and_legacy_pngs(tmp_path: Path) -> None:
    loco = tmp_path / ".loco"
    shots = loco / "ui-screenshots"
    shots.mkdir(parents=True)
    unique = shots / "ui-review_goal_1.png"
    legacy = loco / "ui-review.png"
    unique.write_bytes(b"unique")
    legacy.write_bytes(b"legacy")
    assert resolve_ui_screenshot(tmp_path, "ui-review_goal_1.png") == unique.resolve()
    assert resolve_ui_screenshot(tmp_path, "ui-review.png") == legacy.resolve()
    assert resolve_ui_screenshot(tmp_path, "../config.yaml") is None
    assert resolve_ui_screenshot(tmp_path, "missing.png") is None


def test_playwright_probe_marks_dead_and_working_tabs() -> None:
    class Locator:
        def __init__(self, count: int) -> None:
            self._count = count
            self.first = self

        def count(self) -> int:
            return self._count

        def click(self, timeout: int = 0) -> None:
            return None

    class Page:
        def __init__(self, *, hidden: bool, selected: str) -> None:
            self.hidden = hidden
            self.selected = selected

        def locator(self, _selector: str) -> Locator:
            return Locator(1)

        def get_by_role(self, *_args, **_kwargs) -> Locator:
            return Locator(0)

        def wait_for_timeout(self, _ms: int) -> None:
            return None

        def evaluate(self, _script: str, _name: str) -> dict[str, object]:
            return {"selected": self.selected, "hidden": self.hidden}

    clicked, dead = _playwright_probe(
        Page(hidden=True, selected="false"),
        ['[data-task-pane="screenshots"]'],
        500,
    )
    assert clicked == ['[data-task-pane="screenshots"]']
    assert dead and "screenshots" in dead[0]

    clicked, dead = _playwright_probe(
        Page(hidden=False, selected="true"),
        ['[data-task-pane="screenshots"]'],
        500,
    )
    assert clicked
    assert dead == []


def test_pr_body_embeds_screenshot_markdown() -> None:
    body = _pr_body(
        "Show screenshots",
        "added tab",
        tests_passed=True,
        commit_sha="abc",
        branch="loco/x",
        screenshots=["ui-review_goal.png", "ui-review.png"],
    )
    assert "![ui-review_goal.png](.loco/ui-screenshots/ui-review_goal.png)" in body
    assert "![ui-review.png](.loco/ui-review.png)" in body


def test_load_project_reads_preview_command(tmp_path: Path) -> None:
    loco = tmp_path / ".loco"
    loco.mkdir()
    (loco / "config.yaml").write_text(
        "preview_command: python3 -m http.server {port}\n",
        encoding="utf-8",
    )
    project = load_project(tmp_path)
    assert project.preview_command == "python3 -m http.server {port}"
