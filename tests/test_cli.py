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


def test_clone_help_and_local_repo(tmp_path, monkeypatch) -> None:
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


def test_run_help_includes_model_flag() -> None:
    result = runner.invoke(app, ["run", "--help"])
    assert result.exit_code == 0
    assert "--model" in result.stdout
    assert "-m" in result.stdout
    assert "--base-url" in result.stdout
