from __future__ import annotations

import os
from pathlib import Path

from agent_loco.progress import bind_progress, current_events, reset_progress
from agent_loco.sandbox import Workspace
from agent_loco.tools.base import ToolResult
from agent_loco.tools.tests import run_project_tests, runner_unavailable


def test_missing_pytest_is_not_a_failed_suite() -> None:
    output = (
        "uv sync --dev\n"
        "PYTHONPATH=. uv run python -m pytest tests/ -v --tb=short\n"
        "/tmp/project/.venv/bin/python3: No module named pytest\n"
        "make: *** [test] Error 1\n"
    )
    assert runner_unavailable(output)
    ran = (
        "============================= test session starts =============================\n"
        "collected 1 item\n"
        "FAILED tests/test_models.py::test_rollback - AssertionError: not rolled back\n"
        "1 failed in 0.02s\n"
    )
    assert not runner_unavailable(ran)
    started = (
        "Ran 1 test in 0.001s\nModuleNotFoundError: No module named 'pytest'\nFAILED (errors=1)\n"
    )
    assert not runner_unavailable(started)


def _pyproject(root: Path, dev: str) -> None:
    (root / "pyproject.toml").write_text(
        "[project]\n"
        'name = "demo"\n'
        'version = "0.1.0"\n'
        "dependencies = []\n"
        "\n"
        "[project.optional-dependencies]\n"
        f"dev = [{dev}]\n"
        'docs = ["mkdocs"]\n',
        encoding="utf-8",
    )


def test_missing_pytest_installs_a_declared_extra(tmp_path: Path, monkeypatch) -> None:
    _pyproject(root=tmp_path, dev='"pytest>=8", "pytest-asyncio"')
    calls: list[str] = []

    def fake(workspace, command, timeout, allow_publish=False):
        calls.append(command)
        if command.startswith("uv sync "):
            return ToolResult(True, "synced")
        if "--extra dev" in command and "pytest" in command:
            return ToolResult(True, "1 passed in 0.01s")
        return ToolResult(
            False,
            "uv sync --dev\n"
            "PYTHONPATH=. uv run python -m pytest tests/ -v --tb=short\n"
            "python3: No module named pytest\n"
            "make: *** [test] Error 1",
        )

    monkeypatch.setattr("agent_loco.tools.tests.run_command", fake)
    token = bind_progress()
    try:
        result = run_project_tests(Workspace(tmp_path), "make test", 10, phase="after")
        steps = [event.get("message", "") for event in current_events() if event["kind"] == "step"]
    finally:
        reset_progress(token)
    assert result.ok
    assert "1 passed" in result.output
    assert calls[1] == "uv sync --extra dev"
    assert calls[2] == "PYTHONPATH=. uv run --extra dev python -m pytest tests/ -v --tb=short"
    assert any("Installing pytest from the dev extra" in message for message in steps)


def test_installed_runner_still_reports_a_real_failure(tmp_path: Path, monkeypatch) -> None:
    _pyproject(root=tmp_path, dev='"pytest>=8"')

    def fake(workspace, command, timeout, allow_publish=False):
        if command.startswith("uv sync "):
            return ToolResult(True, "synced")
        if "--extra dev" in command:
            return ToolResult(
                False,
                "test session starts\ncollected 1 item\n"
                "FAILED tests/test_models.py::test_rollback - AssertionError: not rolled back\n"
                "1 failed in 0.02s",
            )
        return ToolResult(False, "python3: No module named pytest\npytest tests/")

    monkeypatch.setattr("agent_loco.tools.tests.run_command", fake)
    result = run_project_tests(Workspace(tmp_path), "make test", 10, phase="after")
    assert not result.ok
    assert not runner_unavailable(result.output)
    assert "not a failing test" not in result.output


def test_missing_pytest_without_a_declared_extra_is_left_alone(tmp_path: Path, monkeypatch) -> None:
    calls: list[str] = []

    def fake(workspace, command, timeout, allow_publish=False):
        calls.append(command)
        return ToolResult(False, "python3: No module named pytest\n")

    monkeypatch.setattr("agent_loco.tools.tests.run_command", fake)
    result = run_project_tests(Workspace(tmp_path), "make test", 10, phase="after")
    assert calls == ["make test"]
    assert not result.ok
    assert "not a failing test" in result.output


def test_run_project_tests_marks_a_missing_runner(tmp_path: Path) -> None:
    script = tmp_path / "check.sh"
    script.write_text(
        "#!/bin/sh\n"
        "echo '/tmp/project/.venv/bin/python3: No module named pytest'\n"
        "echo 'make: *** [test] Error 1'\n"
        "exit 2\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    result = run_project_tests(Workspace(tmp_path), "sh check.sh", 10, phase="after")
    assert not result.ok
    assert runner_unavailable(result.output)
    assert "not a failing test" in result.output


def test_run_project_tests_reuses_result_when_tree_is_unchanged(tmp_path: Path) -> None:
    loco = tmp_path / ".loco"
    loco.mkdir()
    marker = loco / "runs.txt"
    (tmp_path / "check.py").write_text(
        "from pathlib import Path\n"
        "marker = Path('.loco/runs.txt')\n"
        "count = int(marker.read_text()) if marker.exists() else 0\n"
        "marker.write_text(str(count + 1))\n",
        encoding="utf-8",
    )
    workspace = Workspace(tmp_path)
    command = "python3 check.py"
    token = bind_progress()
    try:
        first = run_project_tests(workspace, command, 10, phase="agent")
        second = run_project_tests(workspace, command, 10, phase="after")
        events = current_events()
    finally:
        reset_progress(token)
    assert first.ok
    assert second.ok
    assert marker.read_text(encoding="utf-8") == "1"
    assert events[0]["reused"] is False
    assert events[1]["reused"] is True
    assert events[1]["elapsed_ms"] == 0
    assert events[1]["phase"] == "after"


def test_run_project_tests_reads_a_command_added_during_the_run(tmp_path: Path) -> None:
    loco = tmp_path / ".loco"
    loco.mkdir()
    (tmp_path / "check.py").write_text("print('ok')\n", encoding="utf-8")
    workspace = Workspace(tmp_path)
    missing = run_project_tests(workspace, None, 10, phase="agent")
    assert not missing.ok
    assert missing.output.startswith("no test command configured")
    (loco / "config.yaml").write_text(
        "name: demo\ntest_command: python3 check.py\n",
        encoding="utf-8",
    )
    found = run_project_tests(workspace, None, 10, phase="agent")
    assert found.ok
    assert "ok" in found.output


def test_run_project_tests_prefers_the_command_written_in_config(tmp_path: Path) -> None:
    loco = tmp_path / ".loco"
    loco.mkdir()
    (tmp_path / "check.py").write_text("print('from-config')\n", encoding="utf-8")
    (loco / "config.yaml").write_text(
        "name: demo\ntest_command: python3 check.py\n",
        encoding="utf-8",
    )
    result = run_project_tests(
        Workspace(tmp_path),
        "python3 -c 'raise SystemExit(1)'",
        10,
        phase="agent",
    )
    assert result.ok
    assert "from-config" in result.output


def test_run_project_tests_rejects_a_command_that_does_not_run(tmp_path: Path) -> None:
    script = tmp_path / "check.sh"
    script.write_text(
        "#!/bin/sh\necho 'Tests would be executed in the engine'\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    result = run_project_tests(Workspace(tmp_path), "sh check.sh", 10, phase="agent")
    assert not result.ok
    assert "without running tests" in result.output


def test_run_project_tests_reruns_after_an_edit(tmp_path: Path) -> None:
    loco = tmp_path / ".loco"
    loco.mkdir()
    marker = loco / "runs.txt"
    (tmp_path / "check.py").write_text(
        "from pathlib import Path\n"
        "marker = Path('.loco/runs.txt')\n"
        "count = int(marker.read_text()) if marker.exists() else 0\n"
        "marker.write_text(str(count + 1))\n",
        encoding="utf-8",
    )
    workspace = Workspace(tmp_path)
    command = "python3 check.py"
    first = run_project_tests(workspace, command, 10, phase="agent")
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    second = run_project_tests(workspace, command, 10, phase="after")
    assert first.ok
    assert second.ok
    assert marker.read_text(encoding="utf-8") == "2"


def test_run_project_tests_ignores_mtime_and_non_code(tmp_path: Path) -> None:
    loco = tmp_path / ".loco"
    loco.mkdir()
    marker = loco / "runs.txt"
    app = tmp_path / "app.py"
    app.write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "check.py").write_text(
        "from pathlib import Path\n"
        "marker = Path('.loco/runs.txt')\n"
        "count = int(marker.read_text()) if marker.exists() else 0\n"
        "marker.write_text(str(count + 1))\n",
        encoding="utf-8",
    )
    workspace = Workspace(tmp_path)
    command = "python3 check.py"
    run_project_tests(workspace, command, 10, phase="agent")
    later = app.stat().st_mtime + 50
    os.utime(app, (later, later))
    (tmp_path / "shot.png").write_bytes(b"\x89PNG\r\n")
    run_project_tests(workspace, command, 10, phase="after")
    assert marker.read_text(encoding="utf-8") == "1"


def test_run_project_tests_reruns_when_another_project_changes(tmp_path: Path) -> None:
    loco = tmp_path / ".loco"
    loco.mkdir()
    marker = loco / "runs.txt"
    (tmp_path / "check.py").write_text(
        "from pathlib import Path\n"
        "marker = Path('.loco/runs.txt')\n"
        "count = int(marker.read_text()) if marker.exists() else 0\n"
        "marker.write_text(str(count + 1))\n",
        encoding="utf-8",
    )
    workspace = Workspace(tmp_path)
    command = "python3 check.py"
    run_project_tests(workspace, command, 10, phase="agent")
    other = tmp_path / "other-app" / "src"
    other.mkdir(parents=True)
    (other / "main.py").write_text("print('hi')\n", encoding="utf-8")
    run_project_tests(workspace, command, 10, phase="after")
    assert marker.read_text(encoding="utf-8") == "2"
