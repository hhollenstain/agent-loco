from __future__ import annotations

import re

import pytest
from typer.testing import CliRunner

from agent_loco import __version__
from agent_loco.cli import app

runner = CliRunner()
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_TEST_ENV_VARS = {"LOCO_MODEL_NAME": "test-model", "LOCO_MODEL_BASE_URL": "http://test:9000"}


@pytest.fixture
def monkeypatch_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    for k, v in _TEST_ENV_VARS.items():
        monkeypatch.setenv(k, v)


def _help_text(result) -> str:
    """Flag names stay intact even when rich wraps help to an 80-column CI terminal."""
    return _ANSI_RE.sub("", result.stdout).replace("\n", "")


def test_help_text_rejoins_wrapped_flags() -> None:
    class _Result:
        stdout = "\x1b[1m--max-\nconcurrent\x1b[0m  --web-\nui  --model"

    assert "--max-concurrent" in _help_text(_Result())
    assert "--web-ui" in _help_text(_Result())
    assert "--model" in _help_text(_Result())


def test_version(monkeypatch_env_vars: None) -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in _help_text(result)


def test_watch_help_includes_continuous(monkeypatch_env_vars: None) -> None:
    result = runner.invoke(app, ["watch", "--help"])
    assert result.exit_code == 0
    text = _help_text(result)
    assert "--continuous" in text
    assert "--create-pr" in text


def test_run_help_includes_continuous(monkeypatch_env_vars: None) -> None:
    result = runner.invoke(app, ["run", "--help"])
    assert result.exit_code == 0
    assert "--continuous" in _help_text(result)


def test_run_help_includes_web_ui(monkeypatch_env_vars: None) -> None:
    result = runner.invoke(app, ["run", "--help"])
    assert result.exit_code == 0
    assert "--web-ui" in _help_text(result)


def test_ui_help(monkeypatch_env_vars: None) -> None:
    result = runner.invoke(app, ["ui", "--help"])
    assert result.exit_code == 0
    text = _help_text(result)
    assert "--max-concurrent" in text
    assert "--port" in text
    assert "--model" in text
    assert "-m" in text
    assert "--base-url" in text


def test_clone_help_and_local_repo(tmp_path, monkeypatch, monkeypatch_env_vars: None) -> None:
    from tests.support import init_git_repo

    source = tmp_path / "source"
    source.mkdir()
    (source / "README.md").write_text("demo\n", encoding="utf-8")
    init_git_repo(source)
    parent = tmp_path / "parent"
    parent.mkdir()
    monkeypatch.chdir(parent)
    result = runner.invoke(app, ["clone", str(source), "checkout"])
    assert result.exit_code == 0, result.stdout
    assert "cloned" in result.stdout
    dest = parent / "checkout"
    assert (dest / "README.md").read_text(encoding="utf-8") == "demo\n"
    assert (dest / ".loco" / "config.yaml").exists()
    missing = runner.invoke(app, ["clone", str(source), "checkout"])
    assert missing.exit_code != 0


def test_run_help_includes_model_flag(monkeypatch_env_vars: None) -> None:
    result = runner.invoke(app, ["run", "--help"])
    assert result.exit_code == 0
    text = _help_text(result)
    assert "--model" in text
    assert "-m" in text
    assert "--base-url" in text


@pytest.mark.parametrize(
    "invalid_args, expected_error",
    [
        (["--invalid-option"], "No such option"),
        (["invalid-command"], "No such command"),
        (["--unknown-flag"], "No such option"),
        (["-x"], "No such option"),
    ],
)
def test_invalid_arguments(invalid_args, expected_error) -> None:
    """Test that invalid CLI arguments result in proper error messages and non-zero exit codes."""
    result = runner.invoke(app, invalid_args)
    # Ensure the process exits with non-zero code when invalid arguments are passed
    assert result.exit_code != 0, f"Expected non-zero exit code for {invalid_args}"
    assert expected_error in result.stderr


def test_invalid_arguments_detailed_messages() -> None:
    """Test that detailed error messages are displayed for various invalid CLI inputs."""
    # Test command-level invalid arguments
    result = runner.invoke(app, ["run", "--invalid-run-option"])
    assert result.exit_code != 0
    assert "No such option" in result.stderr or "invalid-run-option" in result.stderr

    # Test that valid commands with invalid options still work properly
    result = runner.invoke(app, ["watch", "--invalid-watch-option"])
    assert result.exit_code != 0
    assert "No such option" in result.stderr


def test_invalid_arguments_comprehensive_validation() -> None:
    """Test comprehensive validation of CLI argument parsing behavior."""
    # Test with no arguments at all (shows help and exits with code 2)
    result = runner.invoke(app, [])
    assert result.exit_code == 2  # Shows help and exits with non-zero code

    # Test with just --help flag
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0

    # Test invalid top-level commands
    result = runner.invoke(app, ["nonexistent-command"])
    assert result.exit_code != 0
    assert "No such command" in result.stderr

    # Test invalid options at top level
    result = runner.invoke(app, ["--invalid-flag"])
    assert result.exit_code != 0
    assert "No such option" in result.stderr

    # Test invalid subcommand options
    result = runner.invoke(app, ["run", "--invalid-option"])
    assert result.exit_code != 0
    assert "No such option" in result.stderr

    # Test that valid commands still work properly
    result = runner.invoke(app, ["run", "--help"])
    assert result.exit_code == 0

    result = runner.invoke(app, ["watch", "--help"])
    assert result.exit_code == 0
