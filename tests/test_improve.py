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
            _review_turn(True, "adder returns 5 and tests passed"),
        ]
    )
    result = run_cycle(tmp_path, settings, llm)
    assert result.status == "success"
    assert result.tests_passed is True
    assert result.committed is True
    assert result.published is False
    assert result.commit_sha
    kinds = [event["kind"] for event in result.events]
    assert "llm" in kinds
    assert "review" in kinds
    assert any(
        event["kind"] == "review" and event["parsed"] is True and event["met"] is True
        for event in result.events
    )
    assert any(
        event["kind"] == "file" and event["path"] == "app.py" and event["action"] == "updated"
        for event in result.events
    )
    file_event = next(event for event in result.events if event["kind"] == "file")
    assert "return left + right" in file_event["diff"]
    llm_event = next(event for event in result.events if event["kind"] == "llm")
    assert "elapsed_ms" in llm_event
    assert llm_event["at"].endswith("Z")
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
    tests = [event for event in result.events if event["kind"] == "test"]
    assert tests
    failed = [event for event in tests if event["ok"] is False]
    assert failed
    assert any("NotImplementedError" in (event.get("output") or "") for event in failed)
    assert any(event.get("phase") == "after" for event in tests)


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
            _review_turn(False, "the UI was not changed"),
        ]
    )
    result = run_cycle(tmp_path, settings, llm, goal="Improve the UI", cli_create_pr=True)
    assert result.status == "skipped"
    assert result.committed is False
    assert result.published is False
    assert "no files changed and the goal is not already met" in (result.reason or "")
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
            _review_turn(True, "adder returns 5 and tests passed"),
        ]
    )
    result = run_cycle(tmp_path, settings, llm, cli_create_pr=True)
    assert result.status == "success"
    assert result.committed is True
    assert result.published is True
    assert result.pr_url == "https://example.test/pull/1"
    assert any(
        event.get("kind") == "pr" and event.get("url") == result.pr_url
        for event in result.events
    )
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
    assert "Goal review confirmed the requested outcome" in str(captured["body"])


def _write_file_turn(path: str, content: str, call_id: str = "call-1") -> AssistantTurn:
    return AssistantTurn(
        text=None,
        tool_calls=[
            ToolCall(
                id=call_id,
                name="write_file",
                arguments={"path": path, "content": content},
            )
        ],
    )


def _review_turn(met: bool, reason: str) -> AssistantTurn:
    payload = '{"met": true, "reason": "%s"}' if met else '{"met": false, "reason": "%s"}'
    return AssistantTurn(text=payload % reason)


def test_cycle_skips_pr_when_goal_is_not_met(
    tmp_path: Path, settings: Settings, monkeypatch
) -> None:
    _green_project(tmp_path)
    monkeypatch.setattr(
        "agent_loco.runtime.improve.push_changes",
        lambda *args, **kwargs: ToolResult(True, "pushed"),
    )
    monkeypatch.setattr(
        "agent_loco.runtime.improve.create_pull_request",
        lambda *args, **kwargs: ToolResult(True, "https://example.test/pull/9"),
    )
    llm = ScriptedClient(
        [
            _write_file_turn("app.py", "def add(left, right):\n    return left + right\n# todo\n"),
            AssistantTurn(text="Tweaked the adder."),
            _review_turn(False, "header is still present and the sidebar toggle is hidden"),
        ]
    )
    result = run_cycle(
        tmp_path,
        settings,
        llm,
        goal="Remove the top header and make the sidebar collapsible",
        cli_create_pr=True,
    )
    assert result.status == "failed"
    assert result.published is False
    assert result.committed is False
    assert "goal not met" in (result.reason or "")
    assert current_branch(Workspace(tmp_path)) in {"main", "master"}


def test_cycle_retries_then_opens_pr_when_goal_is_met(
    tmp_path: Path, settings: Settings, monkeypatch
) -> None:
    _green_project(tmp_path)
    config = tmp_path / ".loco" / "config.yaml"
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            "max_repair_attempts: 0\n",
            "max_repair_attempts: 1\n",
        ),
        encoding="utf-8",
    )
    captured: dict[str, str | None] = {}

    def fake_pr(ws, title, body, *, base=None):
        captured["body"] = body
        return ToolResult(True, "https://example.test/pull/4")

    monkeypatch.setattr(
        "agent_loco.runtime.improve.push_changes",
        lambda *args, **kwargs: ToolResult(True, "pushed"),
    )
    monkeypatch.setattr("agent_loco.runtime.improve.create_pull_request", fake_pr)
    llm = ScriptedClient(
        [
            _write_file_turn("ui.html", "<header>loco</header>\n"),
            AssistantTurn(text="Added a header."),
            _review_turn(False, "the header is still there"),
            _write_file_turn(
                "ui.html",
                "<aside id='sidebar'><button id='toggle-sidebar'>collapse</button></aside>\n",
                call_id="call-2",
            ),
            AssistantTurn(text="Removed the header and added a collapse control."),
            _review_turn(True, "header gone and sidebar toggle is present"),
        ]
    )
    result = run_cycle(
        tmp_path,
        settings,
        llm,
        goal="Remove the top header and make the sidebar collapsible",
        cli_create_pr=True,
    )
    assert result.status == "success"
    assert result.committed is True
    assert result.published is True
    assert result.pr_url == "https://example.test/pull/4"
    assert any(
        event.get("kind") == "pr" and event.get("url") == result.pr_url
        for event in result.events
    )
    assert "Goal review confirmed" in str(captured["body"])
    assert "<header>" not in (tmp_path / "ui.html").read_text(encoding="utf-8")
    assert "toggle-sidebar" in (tmp_path / "ui.html").read_text(encoding="utf-8")


def test_cycle_does_not_agent_retry_unparsed_review(
    tmp_path: Path, settings: Settings
) -> None:
    _green_project(tmp_path)
    config = tmp_path / ".loco" / "config.yaml"
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            "max_repair_attempts: 0\n",
            "max_repair_attempts: 1\n",
        ),
        encoding="utf-8",
    )
    leftover = AssistantTurn(text="SHOULD NOT BE CONSUMED")
    llm = ScriptedClient(
        [
            _write_file_turn("app.py", "def add(left, right):\n    return left + right\n# note\n"),
            AssistantTurn(text="Tweaked the adder."),
            AssistantTurn(text="Looks done to me."),
            AssistantTurn(text="Still looks done."),
            leftover,
        ]
    )
    result = run_cycle(
        tmp_path,
        settings,
        llm,
        goal="Remove the top header and make the sidebar collapsible",
    )
    assert result.status == "failed"
    assert result.committed is False
    assert "verdict" in (result.reason or "")
    assert "Still looks done" in (result.reason or "")
    reviews = [event for event in result.events if event["kind"] == "review"]
    assert len(reviews) == 2
    assert reviews[0]["parsed"] is False
    assert reviews[0]["raw"] == "Looks done to me."
    assert leftover in llm._turns


def test_cycle_review_prompt_includes_lockfile_versions(
    tmp_path: Path, settings: Settings
) -> None:
    _green_project(tmp_path)
    hashes = ",\n".join(f'                "sha256:{index:064x}"' for index in range(80))
    (tmp_path / "Pipfile.lock").write_text(
        "{\n"
        '    "default": {\n'
        '        "discord.py": {\n'
        f'            "hashes": [\n{hashes}\n            ],\n'
        '            "version": "==2.3.2"\n'
        "        }\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    (tmp_path / "setup.py").write_text("INSTALL = ['discord.py==2.3.2']\n", encoding="utf-8")
    run_git(Workspace(tmp_path), ["add", "-A"])
    run_git(Workspace(tmp_path), ["commit", "-m", "lockfiles"])
    new_lock = (
        "{\n"
        '    "default": {\n'
        '        "discord.py": {\n'
        f'            "hashes": [\n{hashes}\n            ],\n'
        '            "version": "==2.7.1"\n'
        "        }\n"
        "    }\n"
        "}\n"
    )
    llm = ScriptedClient(
        [
            _write_file_turn("setup.py", "INSTALL = ['discord.py==2.7.1']\n"),
            AssistantTurn(
                text=None,
                tool_calls=[
                    ToolCall(
                        id="call-2",
                        name="write_file",
                        arguments={"path": "Pipfile.lock", "content": new_lock},
                    )
                ],
            ),
            AssistantTurn(text="Updated discord.py to 2.7.1."),
            _review_turn(True, "discord.py 2.3.2 -> 2.7.1"),
        ]
    )
    result = run_cycle(
        tmp_path,
        settings,
        llm,
        goal="This repo is using a really outdated version of discord.py",
    )
    assert result.status == "success"
    review_messages = [
        message["content"]
        for batch in llm.calls
        for message in batch
        if message.get("role") == "user" and "Diff:" in str(message.get("content") or "")
    ]
    assert review_messages
    diff_text = str(review_messages[0])
    assert "discord.py: 2.3.2 -> 2.7.1" in diff_text
    assert "setup.py" in diff_text
    assert diff_text.count("sha256") < 3


def _discord_lockfile(version: str) -> str:
    return (
        "{\n"
        '    "default": {\n'
        '        "discord.py": {\n'
        '            "hashes": [\n'
        '                "sha256:00"\n'
        "            ],\n"
        f'            "version": "=={version}"\n'
        "        }\n"
        "    }\n"
        "}\n"
    )


def test_cycle_already_done_when_dependency_is_current(
    tmp_path: Path, settings: Settings
) -> None:
    _green_project(tmp_path)
    goal = "Update the outdated discord.py library"
    (tmp_path / ".loco" / "goals.md").write_text(f"- [ ] {goal}\n", encoding="utf-8")
    (tmp_path / "setup.py").write_text("INSTALL = ['discord.py==2.7.1']\n", encoding="utf-8")
    (tmp_path / "Pipfile.lock").write_text(_discord_lockfile("2.7.1"), encoding="utf-8")
    run_git(Workspace(tmp_path), ["add", "-A"])
    run_git(Workspace(tmp_path), ["commit", "-m", "discord.py 2.7.1"])
    llm = ScriptedClient(
        [
            AssistantTurn(text="discord.py is already 2.7.1."),
            AssistantTurn(text="No files to change."),
            AssistantTurn(text="The lockfile already has the current version."),
            AssistantTurn(text="Stopping without edits."),
            _review_turn(True, "discord.py is already 2.7.1 in Pipfile.lock"),
        ]
    )
    result = run_cycle(tmp_path, settings, llm, goal=goal)
    assert result.status == "success"
    assert result.committed is False
    assert result.published is False
    assert "no changes needed" in (result.reason or "")
    assert "2.7.1" in (result.reason or "")
    assert "not pushed" in (result.reason or "")
    assert "No work to do" in (result.summary or "")
    assert "- [x] Update the outdated discord.py library" in (
        tmp_path / ".loco" / "goals.md"
    ).read_text(encoding="utf-8")
    review_messages = [
        message["content"]
        for batch in llm.calls
        for message in batch
        if message.get("role") == "user"
        and "Current workspace:" in str(message.get("content") or "")
    ]
    assert review_messages
    assert "discord.py" in str(review_messages[0])
    assert "2.7.1" in str(review_messages[0])


def test_cycle_already_done_notes_when_head_matches_upstream(
    tmp_path: Path, settings: Settings
) -> None:
    _green_project(tmp_path)
    workspace = Workspace(tmp_path)
    (tmp_path / "setup.py").write_text("INSTALL = ['discord.py==2.7.1']\n", encoding="utf-8")
    run_git(workspace, ["add", "-A"])
    run_git(workspace, ["commit", "-m", "discord.py 2.7.1"])
    branch = current_branch(workspace)
    sha = current_sha(workspace)
    run_git(workspace, ["update-ref", f"refs/remotes/origin/{branch}", sha or ""])
    run_git(workspace, ["branch", f"--set-upstream-to=origin/{branch}"])
    llm = ScriptedClient(
        [
            AssistantTurn(text="Already updated."),
            AssistantTurn(text="Nothing to edit."),
            AssistantTurn(text="Tree already matches the goal."),
            AssistantTurn(text="Done."),
            _review_turn(True, "setup.py already pins discord.py 2.7.1"),
        ]
    )
    result = run_cycle(
        tmp_path,
        settings,
        llm,
        goal="Update discord.py",
    )
    assert result.status == "success"
    assert "no changes needed" in (result.reason or "")
    assert "pushed" in (result.reason or "")
    assert "not pushed" not in (result.reason or "")
