from __future__ import annotations

from typer.testing import CliRunner

from agent_loco import __version__
from agent_loco.cli import app

runner = CliRunner()


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_run_help_includes_web_ui() -> None:
    result = runner.invoke(app, ["run", "--help"])
    assert result.exit_code == 0
    assert "--web-ui" in result.stdout


def test_ui_help() -> None:
    result = runner.invoke(app, ["ui", "--help"])
    assert result.exit_code == 0
    assert "--max-concurrent" in result.stdout
    assert "--port" in result.stdout
    assert "--model" in result.stdout
    assert "-m" in result.stdout
    assert "--base-url" in result.stdout


def test_run_help_includes_model_flag() -> None:
    result = runner.invoke(app, ["run", "--help"])
    assert result.exit_code == 0
    assert "--model" in result.stdout
    assert "-m" in result.stdout
    assert "--base-url" in result.stdout
