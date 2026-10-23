from __future__ import annotations

import subprocess
from pathlib import Path

from tests.support import init_git_repo

from agent_loco.llm.client import AssistantTurn, ScriptedClient
from agent_loco.runtime.project import load_project
from agent_loco.runtime.steward import (
    parse_proposed_goal,
    propose_improvement_goal,
    repeats_recent_work,
    style_sweep_already_done,
)
from agent_loco.sandbox import Workspace


def test_parse_proposed_goal_reads_goal_line() -> None:
    text = "GOAL: Extract inline styles into layout.css\n\nMove the button rules out of index.html."
    goal = parse_proposed_goal(text)
    assert goal is not None
    assert goal.startswith("Extract inline styles into layout.css")
    assert "layout.css" in goal


def test_parse_proposed_goal_nothing_to_do() -> None:
    assert parse_proposed_goal("NOTHING_TO_DO") is None
    assert parse_proposed_goal("The tree is fine.\nNOTHING_TO_DO\n") is None
    assert parse_proposed_goal("  ") is None


def test_repeats_recent_work_matches_the_same_change() -> None:
    recent = "c42f82d Add type hints to the cli.py file to improve code clarity\n"
    goal = "Add type hints to the cli.py file to improve code clarity and maintainability."
    assert repeats_recent_work(goal, recent)
    assert not repeats_recent_work("Show an empty state when the run list is empty.", recent)


def test_style_sweep_sees_hints_already_in_the_file(tmp_path: Path) -> None:
    source = tmp_path / "src" / "cli.py"
    source.parent.mkdir()
    source.write_text(
        "def load(path: str) -> str:\n"
        "    return path\n\n"
        "def save(path: str) -> None:\n"
        "    return\n",
        encoding="utf-8",
    )
    goal = "Add type hints to src/cli.py so every function is annotated."
    assert style_sweep_already_done(goal, Workspace(tmp_path))
    plain = tmp_path / "src" / "app.py"
    plain.write_text("def add(left, right):\n    return left + right\n", encoding="utf-8")
    assert not style_sweep_already_done(
        "Add type hints to src/app.py.",
        Workspace(tmp_path),
    )


def _project(tmp_path: Path) -> None:
    loco = tmp_path / ".loco"
    loco.mkdir()
    (loco / "config.yaml").write_text("name: demo\ngoals_file: goals.md\n", encoding="utf-8")
    (loco / "goals.md").write_text("", encoding="utf-8")
    (tmp_path / "app.py").write_text(
        "def add(left, right):\n    return left + right\n", encoding="utf-8"
    )


def test_propose_drops_a_step_that_was_just_committed(tmp_path: Path) -> None:
    _project(tmp_path)
    init_git_repo(tmp_path)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "Add type hints to the cli.py file"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    llm = ScriptedClient(
        [
            AssistantTurn(
                text="GOAL: Add type hints to the cli.py file\n\nTouch src/cli.py.",
            ),
            AssistantTurn(text="NOTHING_TO_DO"),
        ]
    )
    goal = propose_improvement_goal(
        llm,
        Workspace(tmp_path),
        load_project(tmp_path),
        allow_publish=False,
    )
    assert goal is None


def test_propose_keeps_a_step_the_review_accepts(tmp_path: Path) -> None:
    _project(tmp_path)
    llm = ScriptedClient(
        [
            AssistantTurn(text="GOAL: Show an empty state in app.py\n\nWhen there are no runs."),
            AssistantTurn(
                text='{"accept": true, "reason": "app.py has no empty state", "goal": ""}'
            ),
        ]
    )
    goal = propose_improvement_goal(
        llm,
        Workspace(tmp_path),
        load_project(tmp_path),
        allow_publish=False,
    )
    assert goal is not None
    assert "empty state" in goal


def test_propose_uses_the_reviewers_replacement(tmp_path: Path) -> None:
    _project(tmp_path)
    llm = ScriptedClient(
        [
            AssistantTurn(text="GOAL: Rename a local variable in app.py\n\nNo behavior change."),
            AssistantTurn(
                text=(
                    '{"accept": false, "reason": "a rename is not a user-facing gap", '
                    '"goal": "Show an empty state when the run list is empty."}'
                )
            ),
        ]
    )
    goal = propose_improvement_goal(
        llm,
        Workspace(tmp_path),
        load_project(tmp_path),
        allow_publish=False,
    )
    assert goal == "Show an empty state when the run list is empty."
