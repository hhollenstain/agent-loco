from __future__ import annotations

from pathlib import Path

from tests.support import init_git_repo

from agent_lbw.config import Settings
from agent_lbw.llm.client import AssistantTurn, ScriptedClient, ToolCall
from agent_lbw.runtime.improve import run_cycle
from agent_lbw.runtime.roles import (
    list_roles,
    optional_review_roles,
    parse_role_verdict,
    save_enabled_roles,
)
from agent_lbw.sandbox import Workspace
from agent_lbw.tools.git import current_sha, has_changes


def test_catalog_includes_bundled_roles_and_a_local_one(tmp_path: Path) -> None:
    role_dir = tmp_path / ".lbw" / "roles" / "policy"
    role_dir.mkdir(parents=True)
    (role_dir / "ROLE.md").write_text(
        "---\nname: policy\ntitle: Policy reviewer\njob: review\n"
        "description: Blocks a change that skips the stated check.\n---\n\n"
        "Block a diff that drops a required check.\n",
        encoding="utf-8",
    )
    names = [role.name for role in list_roles(tmp_path)]
    assert names[:5] == ["architect", "builder", "qa", "security", "quality"]
    assert "policy" in names
    enabled = [role.name for role in list_roles(tmp_path) if role.enabled]
    assert enabled == ["architect", "builder", "qa"]
    save_enabled_roles(tmp_path, ["security", "policy"])
    enabled = [role.name for role in list_roles(tmp_path) if role.enabled]
    assert enabled == ["builder", "qa", "security", "policy"]
    assert [role.name for role in optional_review_roles(tmp_path)] == ["security", "policy"]


def test_parse_role_verdict_accepts_fenced_json() -> None:
    verdict = parse_role_verdict(
        '```json\n{"verdict": "send_back", "summary": "missing a test"}\n```'
    )
    assert verdict.parsed is True
    assert verdict.verdict == "send_back"
    assert verdict.summary == "missing a test"
    missing = parse_role_verdict("looks fine to me")
    assert missing.parsed is False
    assert missing.verdict == "pass"


def test_ask_gate_stops_before_any_edit(tmp_path: Path, settings: Settings) -> None:
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    llm = ScriptedClient([])
    result = run_cycle(tmp_path, settings, llm, goal="Rename x to value", gate="ask")
    assert result.status == "waiting"
    assert result.stage == "triage"
    assert result.criteria == ["Rename x to value"]
    assert (tmp_path / "app.py").read_text(encoding="utf-8") == "x = 1\n"
    assert llm.calls == []


def test_hand_off_builds_nothing(tmp_path: Path, settings: Settings) -> None:
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    result = run_cycle(
        tmp_path,
        settings,
        ScriptedClient([]),
        goal="Rename x to value",
        gate="ask",
        decision="hand_off",
    )
    assert result.status == "handed_off"
    assert result.stage == "handed_off"
    assert (tmp_path / "app.py").read_text(encoding="utf-8") == "x = 1\n"


def _broken_adder(root: Path) -> None:
    (root / "app.py").write_text(
        "def add(left, right):\n    raise NotImplementedError\n",
        encoding="utf-8",
    )
    (root / "check.py").write_text(
        "from app import add\nassert add(2, 3) == 5\n",
        encoding="utf-8",
    )
    lbw = root / ".lbw"
    lbw.mkdir()
    (lbw / "config.yaml").write_text(
        "name: fixture\n"
        "test_command: python3 check.py\n"
        "max_repair_attempts: 0\n"
        "publish:\n  enabled: false\n"
        "goals_file: goals.md\n",
        encoding="utf-8",
    )
    (lbw / "goals.md").write_text("- [ ] Make the adder work\n", encoding="utf-8")
    (root / ".gitignore").write_text(".lbw/\n", encoding="utf-8")
    init_git_repo(root)


def _fix_turns() -> list[AssistantTurn]:
    return [
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
        AssistantTurn(text="Implemented add."),
        AssistantTurn(text='{"met": true, "reason": "adder returns 5"}'),
    ]


def test_ask_gate_waits_for_approval_then_commits(tmp_path: Path, settings: Settings) -> None:
    _broken_adder(tmp_path)
    held = run_cycle(
        tmp_path,
        settings,
        ScriptedClient(_fix_turns()),
        goal="Make the adder work",
        gate="ask",
        decision="build",
    )
    assert held.status == "waiting", held.reason
    assert held.stage == "approval"
    assert held.committed is False
    assert has_changes(Workspace(tmp_path))
    assert any(item["role"] == "qa" and item["verdict"] == "pass" for item in held.evidence)
    before = current_sha(Workspace(tmp_path))
    approved = run_cycle(
        tmp_path,
        settings,
        ScriptedClient([]),
        goal="Make the adder work",
        gate="ask",
        decision="approve",
        held_summary=held.summary or "",
    )
    assert approved.status == "success", approved.reason
    assert approved.committed is True
    assert approved.stage == "done"
    assert current_sha(Workspace(tmp_path)) != before
    assert "return left + right" in (tmp_path / "app.py").read_text(encoding="utf-8")


def test_skipping_plan_review_still_waits_before_the_pull_request(
    tmp_path: Path, settings: Settings
) -> None:
    _broken_adder(tmp_path)
    held = run_cycle(
        tmp_path,
        settings,
        ScriptedClient(_fix_turns()),
        goal="Make the adder work",
        triage_gate="auto",
        approval_gate="ask",
    )
    assert held.status == "waiting", held.reason
    assert held.stage == "approval"
    assert held.committed is False
    assert "return left + right" in (tmp_path / "app.py").read_text(encoding="utf-8")


def test_skipping_pull_request_review_commits_after_the_plan(
    tmp_path: Path, settings: Settings
) -> None:
    _broken_adder(tmp_path)
    held = run_cycle(
        tmp_path,
        settings,
        ScriptedClient([]),
        goal="Make the adder work",
        triage_gate="ask",
        approval_gate="auto",
    )
    assert held.status == "waiting"
    assert held.stage == "triage"
    assert "raise NotImplementedError" in (tmp_path / "app.py").read_text(encoding="utf-8")
    done = run_cycle(
        tmp_path,
        settings,
        ScriptedClient(_fix_turns()),
        goal="Make the adder work",
        triage_gate="ask",
        approval_gate="auto",
        decision="build",
    )
    assert done.status == "success", done.reason
    assert done.committed is True
    assert done.stage == "done"


def test_enabled_review_role_can_block_the_commit(tmp_path: Path, settings: Settings) -> None:
    _broken_adder(tmp_path)
    role_dir = tmp_path / ".lbw" / "roles" / "policy"
    role_dir.mkdir(parents=True)
    (role_dir / "ROLE.md").write_text(
        "---\nname: policy\ntitle: Policy reviewer\njob: review\n"
        "description: Blocks unsafe changes.\n---\n\nBlock secrets.\n",
        encoding="utf-8",
    )
    save_enabled_roles(tmp_path, ["policy"])
    turns = _fix_turns()
    turns.append(AssistantTurn(text='{"verdict": "block", "summary": "this would ship a secret"}'))
    result = run_cycle(
        tmp_path,
        settings,
        ScriptedClient(turns),
        goal="Make the adder work",
    )
    assert result.status == "failed"
    assert result.committed is False
    assert "Policy reviewer block" in (result.reason or "")
    assert any(item["role"] == "policy" and item["verdict"] == "block" for item in result.evidence)
