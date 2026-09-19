from __future__ import annotations

from pathlib import Path

import pytest

from agent_loco.sandbox import SandboxError, Workspace


def test_resolve_stays_inside_workspace(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path)
    target = workspace.resolve("src/app.py")
    assert target == tmp_path / "src" / "app.py"


def test_resolve_rejects_parent_escape(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path)
    with pytest.raises(SandboxError, match="escapes"):
        workspace.resolve("../outside.txt")


def test_resolve_rejects_absolute_escape(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path)
    with pytest.raises(SandboxError, match="escapes"):
        workspace.resolve("/tmp/outside.txt")
