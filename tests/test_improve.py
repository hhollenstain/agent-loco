from __future__ import annotations

from pathlib import Path

from tests.support import init_git_repo

from agent_loco.config import Settings
from agent_loco.llm.client import AssistantTurn, ScriptedClient, ToolCall
from agent_loco.runtime.improve import resolve_create_pr, run_cycle
from agent_loco.runtime.project import load_project
from agent_loco.sandbox import Workspace
from agent_loco.tools.base import ToolResult
from agent_loco.tools.git import current_branch, current_sha, run_git


def _broken_project(root: Path) -> None:
    (root / "app.py").write_text(
        "def add(left, right):\n    raise NotImplementedError\n",
        encoding="utf-8",
    )
    (root / "check.py").write_text(
        "from app import add\nassert add(2, 3) == 5\n",
        encoding="utf-8",
    )
    loco = root / ".loco"
    loco.mkdir()
    (loco / "config.yaml").write_text(
        "name: fixture\n"
        "test_command: python3 check.py\n"
        "max_repair_attempts: 0\n"
        "publish:\n  enabled: false\n"
        "goals_file: goals.md\n",
        encoding="utf-8",
    )
    (loco / "goals.md").write_text("- [ ] Make the adder work\n", encoding="utf-8")
    init_git_repo(root)


def test_cycle_commits_when_scripted_fix_passes(tmp_path: Path, settings: Settings) -> None:
    _broken_project(tmp_path)
    llm = ScriptedClient(
        [
            AssistantTurn(
                text=None,
                tool_calls=[
                    ToolCall(
                        id="call-1",
                        name="write_file",
                        arguments={
                            "path": "app.py",
                            "content": "def add(left, right):\n    return left + right\n",
                        },
                    )
                ],
            ),
            AssistantTurn(text="Implemented add and verified with python3 check.py."),
        ]
    )
    result = run_cycle(tmp_path, settings, llm)
    assert result.status == "success"
    assert result.tests_passed is True
    assert result.committed is True
    assert result.published is False
    assert result.commit_sha
    branch = current_branch(Workspace(tmp_path))
    assert branch in {"main", "master"}
    assert (tmp_path / "app.py").read_text(encoding="utf-8") == (
        "def add(left, right):\n    return left + right\n"
    )


def test_cycle_skips_commit_when_tests_still_fail(tmp_path: Path, settings: Settings) -> None:
    _broken_project(tmp_path)
    llm = ScriptedClient([AssistantTurn(text="I looked around and stopped.")])
    result = run_cycle(tmp_path, settings, llm, goal="Make the adder work")
    assert result.status == "failed"
    assert result.tests_passed is False
    assert result.committed is False


def test_no_create_pr_flag_wins_over_project_config(tmp_path: Path, settings: Settings) -> None:
    _broken_project(tmp_path)
    config = tmp_path / ".loco" / "config.yaml"
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            "publish:\n  enabled: false\n",
            "publish:\n  enabled: true\n",
        ),
        encoding="utf-8",
    )
    project = load_project(tmp_path)
    assert project.publish_enabled is True
    assert resolve_create_pr(settings, project, cli_create_pr=False) is False
    assert resolve_create_pr(settings, project, cli_create_pr=None) is True
    assert resolve_create_pr(settings, project, cli_create_pr=True) is True


def _green_project(root: Path) -> None:
    (root / "app.py").write_text(
        "def add(left, right):\n    return left + right\n",
        encoding="utf-8",
    )
    (root / "check.py").write_text(
        "from app import add\nassert add(2, 3) == 5\n",
        encoding="utf-8",
    )
    loco = root / ".loco"
    loco.mkdir()
    (loco / "config.yaml").write_text(
        "name: fixture\n"
        "test_command: python3 check.py\n"
        "max_repair_attempts: 0\n"
        "publish:\n  enabled: false\n"
        "goals_file: goals.md\n",
        encoding="utf-8",
    )
    (loco / "goals.md").write_text("- [ ] Improve the UI\n", encoding="utf-8")
    init_git_repo(root)
    runs = loco / "runs"
    runs.mkdir()
    (runs / "old.json").write_text('{"status": "success"}\n', encoding="utf-8")


def test_cycle_does_not_commit_run_logs_or_plans(tmp_path: Path, settings: Settings) -> None:
    _green_project(tmp_path)
    llm = ScriptedClient(
        [
            AssistantTurn(text="I will add a copy field next."),
            AssistantTurn(text="Here is the write_file JSON I would send."),
            AssistantTurn(text="Summary of the planned UI changes."),
            AssistantTurn(text="Still only describing the work."),
        ]
    )
    result = run_cycle(tmp_path, settings, llm, goal="Improve the UI", cli_create_pr=True)
    assert result.status == "skipped"
    assert result.committed is False
    assert result.published is False
    assert "no project files changed" in (result.reason or "")
    tracked = run_git(Workspace(tmp_path), ["ls-files", ".loco/runs"])
    assert tracked.stdout.strip() == ""
    assert (tmp_path / ".loco" / "runs" / "old.json").exists()


def test_cycle_create_pr_uses_feature_branch_not_main(
    tmp_path: Path, settings: Settings, monkeypatch
) -> None:
    _broken_project(tmp_path)
    workspace = Workspace(tmp_path)
    protected = current_branch(workspace)
    initial = current_sha(workspace)
    captured: dict[str, str | None] = {}

    def fake_push(ws, remote="origin", branch=None):
        captured["push_branch"] = branch
        captured["push_current"] = current_branch(ws)
        return ToolResult(True, "pushed")

    def fake_pr(ws, title, body, *, base=None):
        captured["title"] = title
        captured["body"] = body
        captured["base"] = base
        captured["pr_branch"] = current_branch(ws)
        return ToolResult(True, "https://example.test/pull/1")

    monkeypatch.setattr("agent_loco.runtime.improve.push_changes", fake_push)
    monkeypatch.setattr("agent_loco.runtime.improve.create_pull_request", fake_pr)
    llm = ScriptedClient(
        [
            AssistantTurn(
                text=None,
                tool_calls=[
                    ToolCall(
                        id="call-1",
                        name="write_file",
                        arguments={
                            "path": "app.py",
                            "content": "def add(left, right):\n    return left + right\n",
                        },
                    )
                ],
            ),
            AssistantTurn(text="Implemented add and verified with python3 check.py."),
        ]
    )
    result = run_cycle(tmp_path, settings, llm, cli_create_pr=True)
    assert result.status == "success"
    assert result.committed is True
    assert result.published is True
    assert captured["push_branch"]
    assert str(captured["push_branch"]).startswith("loco/")
    assert captured["push_branch"] != protected
    assert captured["push_current"] == captured["push_branch"]
    assert captured["pr_branch"] == captured["push_branch"]
    assert captured["base"] == protected
    assert "## Summary" in str(captured["body"])
    assert "## Test plan" in str(captured["body"])
    assert current_branch(workspace).startswith("loco/")
    assert run_git(workspace, ["rev-parse", protected or "HEAD"]).stdout.strip() == initial