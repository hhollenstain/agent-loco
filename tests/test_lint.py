from __future__ import annotations

import re
import tempfile
from pathlib import Path

from agent_lbw.config import Settings
from agent_lbw.progress import bind_progress, current_events, reset_progress
from agent_lbw.runtime.improve import _ensure_lint
from agent_lbw.runtime.project import (
    ProjectConfig,
    infer_lint_command,
    load_project,
    write_default_project_files,
)
from agent_lbw.runtime.testbed import ruff_unavailable
from agent_lbw.sandbox import Workspace
from agent_lbw.tools import build_tools
from agent_lbw.tools.base import ToolResult
from agent_lbw.tools.lint import apply_ruff_autofix, lint_targets_changed_files, run_project_lint

_BED = re.compile(r"(lbw-testbed-[0-9a-f]{12}-ruff)")
_MISSING_RUFF = "exit 127\n/bin/bash: line 1: ruff: command not found\n"


def _write_ruff_pyproject(root: Path) -> None:
    (root / "pyproject.toml").write_text(
        "[project]\nname='x'\n[tool.ruff]\nline-length = 100\n",
        encoding="utf-8",
    )


def test_infer_ruff_for_src_and_tests(tmp_path: Path) -> None:
    _write_ruff_pyproject(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    assert infer_lint_command(tmp_path) == "ruff format src tests && ruff check src tests"


def test_infer_ruff_dot_without_src_layout(tmp_path: Path) -> None:
    _write_ruff_pyproject(tmp_path)
    assert infer_lint_command(tmp_path) == "ruff format . && ruff check ."


def test_infer_skips_ruff_when_pyproject_has_no_ruff_config(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (tmp_path / "Pipfile").write_text("[packages]\n", encoding="utf-8")
    assert infer_lint_command(tmp_path) is None


def test_infer_ruff_when_listed_as_dependency(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project.optional-dependencies]\ndev = ["ruff>=0.9.0"]\n',
        encoding="utf-8",
    )
    assert infer_lint_command(tmp_path) == "ruff format . && ruff check ."


def test_infer_ruff_uses_pipenv_when_pipfile_present(tmp_path: Path) -> None:
    (tmp_path / "ruff.toml").write_text("line-length = 100\n", encoding="utf-8")
    (tmp_path / "Pipfile").write_text("[packages]\n", encoding="utf-8")
    assert infer_lint_command(tmp_path) == (
        "PIPENV_IGNORE_VIRTUALENVS=1 pipenv run ruff format . && "
        "PIPENV_IGNORE_VIRTUALENVS=1 pipenv run ruff check ."
    )


def test_empty_lint_command_disables_inference(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    lbw = tmp_path / ".lbw"
    lbw.mkdir()
    (lbw / "config.yaml").write_text("name: demo\nlint_command: ''\n", encoding="utf-8")
    assert load_project(tmp_path).lint_command is None


def test_init_writes_inferred_lint_command(tmp_path: Path) -> None:
    _write_ruff_pyproject(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    write_default_project_files(tmp_path)
    config = (tmp_path / ".lbw" / "config.yaml").read_text(encoding="utf-8")
    assert "lint_command: ruff format src tests && ruff check src tests" in config


def test_run_lint_applies_ruff_format(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (tmp_path / "tests").mkdir()
    _write_ruff_pyproject(tmp_path)
    messy = src / "app.py"
    messy.write_text("x=1+2\n", encoding="utf-8")
    result = run_project_lint(Workspace(tmp_path), infer_lint_command(tmp_path), 30)
    assert result.ok
    assert messy.read_text(encoding="utf-8") == "x = 1 + 2\n"


def test_run_lint_is_a_wired_tool(tmp_path: Path) -> None:
    tools = build_tools(
        Workspace(tmp_path),
        test_command=None,
        lint_command="ruff check .",
        command_timeout_seconds=10,
        git_author_name=None,
        git_author_email=None,
    )
    assert "run_lint" in {tool.name for tool in tools}


def test_run_project_lint_reads_a_command_added_during_the_run(tmp_path: Path) -> None:
    lbw = tmp_path / ".lbw"
    lbw.mkdir()
    (tmp_path / "check.py").write_text("print('ok')\n", encoding="utf-8")
    workspace = Workspace(tmp_path)
    missing = run_project_lint(workspace, None, 10, phase="agent")
    assert not missing.ok
    assert missing.output.startswith("no lint command configured")
    (lbw / "config.yaml").write_text(
        "name: demo\nlint_command: python3 check.py\n",
        encoding="utf-8",
    )
    result = run_project_lint(workspace, None, 10, phase="agent")
    assert result.ok
    assert "ok" in result.output


def test_run_project_lint_records_failure(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path)
    command = "python3 -c \"raise SystemExit('E501 line too long')\""
    token = bind_progress()
    try:
        result = run_project_lint(workspace, command, 10, phase="after")
        events = current_events()
    finally:
        reset_progress(token)
    assert result.ok is False
    assert "E501" in result.output
    assert events[0]["kind"] == "lint"
    assert events[0]["ok"] is False
    assert events[0]["phase"] == "after"


def test_lint_targets_ruff_paths_in_this_cycle() -> None:
    output = "F821 Undefined name `utils`\n   --> autochannel/lib/plugins/join.py:44:10\n"
    assert lint_targets_changed_files(output, ["autochannel/lib/plugins/join.py"])
    assert not lint_targets_changed_files(output, ["Pipfile", "setup.py"])
    assert lint_targets_changed_files("F401 unused import", ["app.py"])


def _patch_commands(monkeypatch, fake) -> None:
    monkeypatch.setattr("agent_lbw.tools.lint.run_command", fake)
    monkeypatch.setattr("agent_lbw.runtime.testbed.run_command", fake)


def _only_python3(name: str) -> str | None:
    if name == "python3":
        return "/usr/bin/python3"
    return None


def _materialize_ruff(command: str) -> None:
    match = _BED.search(command)
    if match is None:
        return
    path = Path(tempfile.gettempdir()) / match.group(1) / "bin" / "ruff"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")


def test_ruff_unavailable_is_a_missing_binary() -> None:
    assert ruff_unavailable(_MISSING_RUFF)
    assert ruff_unavailable("python3: No module named ruff\n")
    assert not ruff_unavailable("F401 `os` imported but unused\n --> app.py:1:8\n")


def test_missing_ruff_builds_a_throwaway_environment(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\ndependencies = ["ruff>=0.9"]\n',
        encoding="utf-8",
    )
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    calls: list[str] = []

    def fake(workspace, command, timeout, allow_publish=False):
        calls.append(command)
        if "python3 -m venv" in command:
            _materialize_ruff(command)
            return ToolResult(True, "installed")
        if command.startswith("export PATH="):
            return ToolResult(True, "All checks passed!")
        return ToolResult(False, _MISSING_RUFF)

    _patch_commands(monkeypatch, fake)
    monkeypatch.setattr("agent_lbw.runtime.testbed.shutil.which", _only_python3)
    token = bind_progress()
    try:
        result = run_project_lint(Workspace(tmp_path), "ruff check .", 10, phase="after")
        steps = [event.get("message", "") for event in current_events() if event["kind"] == "step"]
    finally:
        reset_progress(token)
    install, rerun = calls[1], calls[2]
    assert result.ok
    assert "All checks passed" in result.output
    assert "python3 -m venv" in install
    assert "--only-binary=:all:" in install
    assert "ruff>=0.9" in install
    assert "lbw-testbed-" in install
    assert str(tmp_path) not in install
    assert rerun.startswith("export PATH=")
    assert rerun.endswith(" && ruff check .")
    assert str(tmp_path) not in rerun
    assert any("throwaway" in message and "ruff" in message for message in steps)


def test_missing_ruff_reuses_the_throwaway_environment(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    installs: list[str] = []

    def fake(workspace, command, timeout, allow_publish=False):
        if "python3 -m venv" in command:
            installs.append(command)
            _materialize_ruff(command)
            return ToolResult(True, "installed")
        if command.startswith("export PATH="):
            return ToolResult(True, "All checks passed!")
        return ToolResult(False, _MISSING_RUFF)

    _patch_commands(monkeypatch, fake)
    monkeypatch.setattr("agent_lbw.runtime.testbed.shutil.which", _only_python3)
    workspace = Workspace(tmp_path)
    assert run_project_lint(workspace, "ruff check .", 10).ok
    (tmp_path / "app.py").write_text("x = 2\n", encoding="utf-8")
    assert run_project_lint(workspace, "ruff check .", 10).ok
    assert len(installs) == 1


def test_missing_ruff_uses_a_locked_down_container(tmp_path: Path, monkeypatch) -> None:
    calls: list[str] = []

    def fake(workspace, command, timeout, allow_publish=False):
        calls.append(command)
        if command.startswith("docker run "):
            return ToolResult(True, "All checks passed!")
        return ToolResult(False, _MISSING_RUFF)

    _patch_commands(monkeypatch, fake)
    monkeypatch.setattr("agent_lbw.runtime.testbed.shutil.which", lambda name: None)
    monkeypatch.setattr("agent_lbw.runtime.testbed._docker_available", lambda: True)
    command = "ruff format . && ruff check ."
    result = run_project_lint(Workspace(tmp_path), command, 10)
    assert result.ok
    assert len(calls) == 3
    first, second = calls[1], calls[2]
    for docker in (first, second):
        assert docker.startswith("docker run --rm --network none --read-only ")
        assert "--cap-drop=ALL" in docker
        assert "--security-opt=no-new-privileges" in docker
        assert "--entrypoint ruff ghcr.io/astral-sh/ruff:0.16.10" in docker
        assert "bash -lc" not in docker
    assert first.endswith(" format .")
    assert second.endswith(" check .")


def test_unsafe_lint_command_is_not_run_in_a_container(tmp_path: Path, monkeypatch) -> None:
    calls: list[str] = []

    def fake(workspace, command, timeout, allow_publish=False):
        calls.append(command)
        return ToolResult(False, _MISSING_RUFF)

    _patch_commands(monkeypatch, fake)
    monkeypatch.setattr("agent_lbw.runtime.testbed.shutil.which", lambda name: None)
    monkeypatch.setattr("agent_lbw.runtime.testbed._docker_available", lambda: True)
    result = run_project_lint(Workspace(tmp_path), "ruff check .; echo pwned", 10)
    assert calls == ["ruff check .; echo pwned"]
    assert not result.ok
    assert "not a lint finding" in result.output


def test_a_real_lint_failure_does_not_build_an_environment(tmp_path: Path, monkeypatch) -> None:
    calls: list[str] = []

    def fake(workspace, command, timeout, allow_publish=False):
        calls.append(command)
        return ToolResult(False, "F401 `os` imported but unused\n --> app.py:1:8\n")

    _patch_commands(monkeypatch, fake)
    result = run_project_lint(Workspace(tmp_path), "ruff check .", 10)
    assert calls == ["ruff check ."]
    assert not result.ok
    assert "F401" in result.output


class _Agent:
    def run(self, *_args, **_kwargs):
        raise AssertionError("lint repair should not run when ruff never started")


def test_missing_ruff_does_not_start_a_repair(tmp_path: Path, monkeypatch) -> None:
    def fake(workspace, command, timeout, allow_publish=False):
        return ToolResult(False, _MISSING_RUFF)

    _patch_commands(monkeypatch, fake)
    monkeypatch.setattr("agent_lbw.runtime.testbed.shutil.which", lambda name: None)
    monkeypatch.setattr("agent_lbw.runtime.testbed._docker_available", lambda: False)
    project = ProjectConfig(
        name="demo",
        root=tmp_path,
        test_command=None,
        setup_command=None,
        publish_enabled=False,
        publish_remote="origin",
        publish_branch=None,
        create_pr=False,
        goals_file="goals.md",
        max_repair_attempts=4,
        lint_command="ruff check .",
    )
    result = _ensure_lint(Workspace(tmp_path), project, Settings(), _Agent(), allow_create_pr=False)
    assert result is not None
    assert not result.ok
    assert "not a lint finding" in result.output


def test_apply_ruff_autofix_removes_unused_import(tmp_path: Path) -> None:
    _write_ruff_pyproject(tmp_path)
    app = tmp_path / "app.py"
    app.write_text("import os\n\nx = 1\n", encoding="utf-8")
    assert apply_ruff_autofix(Workspace(tmp_path), "ruff check .", 30)
    assert "import os" not in app.read_text(encoding="utf-8")
