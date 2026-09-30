"""Tests for last-failure tracking functionality."""

import json
from pathlib import Path

import pytest

from agent_loco.runtime.brief import _compute_goal_key, build_task_brief
from agent_loco.runtime.improve import (
    CycleResult,
    _delete_last_failure,
    _write_last_failure,
)
from agent_loco.runtime.project import load_project
from agent_loco.sandbox import Workspace


@pytest.fixture
def test_workspace(tmp_path):
    """Create a minimal workspace structure."""

    # Create pyproject.toml
    project_path = tmp_path / "test_project"
    project_path.mkdir()
    pyproject = project_path / "pyproject.toml"
    pyproject.write_text(
        """
[project]
name = "test-project"
"""
    )

    # Create workspace directory
    workspace_dir = project_path / "workspaces" / "default"
    workspace_dir.mkdir(parents=True)
    (workspace_dir / "README.md").write_text("test")

    return (Workspace(workspace_dir), workspace_dir, project_path)


def test_failed_cycle_writes_last_failure(test_workspace):
    """A failed cycle writes .loco/last-failure.json with reason."""
    workspace, workspace_dir, _ = test_workspace

    result = CycleResult(
        status="failed",
        goal="fix a bug",
        summary="Test summary",
        tests_passed=False,
        committed=False,
        published=False,
        commit_sha=None,
        reason="tests failed; commit skipped",
    )

    _write_last_failure(workspace.root, "fix a bug", result)

    failure_file = workspace.root / ".loco" / "last-failure.json"
    assert failure_file.exists()

    data = json.loads(failure_file.read_text(encoding="utf-8"))
    assert data["goal"] == "fix a bug"
    assert data["status"] == "failed"
    assert data["reason"] == "tests failed; commit skipped"
    assert data["goal_key"] == _compute_goal_key("fix a bug")
    assert len(data["goal_key"]) == 16
    assert data["summary"] == "Test summary"


def test_brief_includes_last_failure_for_same_goal_only(tmp_path: Path):
    """Brief includes failure for matching goal_key, not for different goals."""
    project_path = tmp_path / "test_project"
    project_path.mkdir()
    pyproject = project_path / "pyproject.toml"
    pyproject.write_text('[project]\nname = "test-project"\n')
    workspace_dir = project_path / "workspaces" / "default"
    workspace_dir.mkdir(parents=True)
    (workspace_dir / "README.md").write_text("test")

    # Create .loco/last-failure.json
    failure_path = workspace_dir / ".loco" / "last-failure.json"
    failure_path.parent.mkdir(exist_ok=True)
    failure_path.write_text(
        json.dumps(
            {
                "goal": "fix a bug",
                "goal_key": _compute_goal_key("fix a bug"),
                "status": "failed",
                "reason": "tests failed",
                "summary": "test",
                "finished_at": "2024-01-01T00:00:00Z",
            }
        )
    )

    project = load_project(workspace_dir)

    # Brief for same goal includes failure
    brief = build_task_brief(workspace_dir, "fix a bug", project)
    assert "Last attempt of this goal failed:" in brief
    assert "tests failed" in brief

    # Brief for different goal does not include failure
    brief2 = build_task_brief(workspace_dir, "fix another bug", project)
    assert "Last attempt of this goal failed:" not in brief2


def test_successful_cycle_clears_last_failure(tmp_path: Path):
    """A successful cycle deletes last-failure.json."""
    project_path = tmp_path / "test_project"
    project_path.mkdir()
    pyproject = project_path / "pyproject.toml"
    pyproject.write_text('[project]\nname = "test-project"\n')
    workspace_dir = project_path / "workspaces" / "default"
    workspace_dir.mkdir(parents=True)
    (workspace_dir / "README.md").write_text("test")

    # Seed last-failure.json
    failure_path = workspace_dir / ".loco" / "last-failure.json"
    failure_path.parent.mkdir(exist_ok=True)
    failure_path.write_text(
        json.dumps(
            {
                "goal": "test goal",
                "goal_key": _compute_goal_key("test goal"),
                "status": "failed",
                "reason": "test reason",
                "summary": "test",
                "finished_at": "2024-01-01T00:00:00Z",
            }
        )
    )
    assert failure_path.exists()

    # Call _delete_last_failure
    _delete_last_failure(workspace_dir)

    assert not failure_path.exists()


def test_delete_last_failure_no_error_when_missing(tmp_path: Path):
    """Deleting non-existent last-failure.json does not raise."""
    result = Path(tmp_path)
    _delete_last_failure(result)  # Should not raise


def test_write_last_failure_truncates_summary(tmp_path: Path):
    """Summary is truncated to 500 chars."""
    workspace = Workspace(tmp_path)
    long_summary = "x" * 1000
    result = CycleResult(
        status="failed",
        goal="test",
        summary=long_summary,
        tests_passed=None,
        committed=False,
        published=False,
        commit_sha="abc123",
        reason="test reason",
    )

    (workspace.root / ".loco").mkdir()
    _write_last_failure(workspace.root, "test", result)

    data = json.loads((workspace.root / ".loco" / "last-failure.json").read_text(encoding="utf-8"))
    assert len(data["summary"]) == 500
