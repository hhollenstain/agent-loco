from __future__ import annotations

from pathlib import Path

from agent_loco.llm.client import AssistantTurn, ScriptedClient
from agent_loco.progress import bind_progress, reset_progress
from agent_loco.runtime.improve import _review_goal
from agent_loco.runtime.project import load_project
from agent_loco.runtime.supi_review import format_review, review_diff
from agent_loco.sandbox import Workspace
from agent_loco.tools import build_tools, execute_tool

_SECRET_DIFF = """\
diff --git a/src/app.py b/src/app.py
--- a/src/app.py
+++ b/src/app.py
@@ -1 +1,2 @@
+api_key = "abcdefghijklmnop"
"""

_SHELL_DIFF = """\
diff --git a/src/app.py b/src/app.py
--- a/src/app.py
+++ b/src/app.py
@@ -1 +1,2 @@
+os.system(f"tar czf /tmp/{name}.tar.gz /data")
"""

_PLACEHOLDER_DIFF = """\
diff --git a/src/app.py b/src/app.py
--- a/src/app.py
+++ b/src/app.py
@@ -1 +1,2 @@
+api_key = "your-api-key-here"
"""

_CLEAN_DIFF = """\
diff --git a/src/app.py b/src/app.py
--- a/src/app.py
+++ b/src/app.py
@@ -1 +1,2 @@
+value = name.strip()
"""


def test_review_diff_flags_secret_shell_and_unfinished_marker() -> None:
    secret = review_diff(_SECRET_DIFF)
    assert secret[0].severity == "error"
    assert "hard-coded secret" in secret[0].message
    assert "src/app.py:1" == secret[0].location

    shell = review_diff(_SHELL_DIFF)
    assert any("shell command" in item.message for item in shell)

    todo = review_diff(_CLEAN_DIFF.replace("value = name.strip()", "# TODO: later"))
    assert any("unfinished marker" in item.message for item in todo)
    allowed = review_diff(
        _CLEAN_DIFF.replace("value = name.strip()", "# TODO: later"),
        goal="Add a TODO comment for the follow-up",
    )
    assert allowed == []


def test_review_diff_ignores_placeholders_tests_and_clean_code() -> None:
    assert review_diff(_PLACEHOLDER_DIFF) == []
    assert review_diff(_CLEAN_DIFF) == []
    in_tests = _SECRET_DIFF.replace("src/app.py", "tests/test_app.py")
    assert review_diff(in_tests) == []


def test_review_diff_warns_when_a_new_module_has_no_test() -> None:
    diff = """\
diff --git a/src/pkg/new_mod.py b/src/pkg/new_mod.py
new file mode 100644
--- /dev/null
+++ b/src/pkg/new_mod.py
@@ -0,0 +1 @@
+def run():
+    return 1
"""
    findings = review_diff(diff)
    assert findings[0].severity == "warning"
    assert "no test" in findings[0].message
    covered = diff + "\n+++ b/tests/test_new_mod.py\n"
    assert review_diff(covered) == []


def test_untracked_file_body_is_reviewed() -> None:
    diff = '--- /dev/null\n+++ b/src/app.py\napi_key = "abcdefghijklmnop"\n'
    findings = review_diff(diff)
    assert findings[0].severity == "error"
    assert "src/app.py:1" == findings[0].location


def test_format_review_uses_finding_markers() -> None:
    text = format_review(review_diff(_SECRET_DIFF))
    assert text.startswith("**[error]** `src/app.py:1`")
    assert "Suggestion:" in text


def test_review_changes_tool_is_wired_and_fails_closed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "agent_loco.tools.supi.collect_work_diff",
        lambda *_args, **_kwargs: _SECRET_DIFF,
    )
    tools = build_tools(
        Workspace(tmp_path),
        test_command=None,
        lint_command=None,
        command_timeout_seconds=10,
        git_author_name=None,
        git_author_email=None,
        goal="Add the export command",
    )
    assert "review_changes" in {tool.name for tool in tools}
    result = execute_tool(tools, "review_changes", {})
    assert result.ok is False
    assert "hard-coded secret" in result.output


def test_review_goal_rejects_a_met_verdict_when_code_review_finds_an_error(
    tmp_path: Path,
) -> None:
    (tmp_path / ".loco").mkdir()
    (tmp_path / ".loco" / "config.yaml").write_text("name: fixture\n", encoding="utf-8")
    llm = ScriptedClient([AssistantTurn(text='{"met": true, "reason": "export works"}')])
    token = bind_progress()
    try:
        verdict = _review_goal(
            Workspace(tmp_path),
            load_project(tmp_path),
            llm,
            "Add the export command",
            _SHELL_DIFF,
            "added export",
            True,
        )
    finally:
        reset_progress(token)
    assert verdict.met is False
    assert "code review found an error" in verdict.reason
    assert "shell command" in verdict.reason
