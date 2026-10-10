from __future__ import annotations

from pathlib import Path

from agent_loco.progress import bind_progress, current_events, reset_progress
from agent_loco.sandbox import Workspace
from agent_loco.tools.tests import run_project_tests


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
